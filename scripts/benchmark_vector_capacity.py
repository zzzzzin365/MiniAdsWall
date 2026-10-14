"""Isolated synthetic-vector capacity benchmark; never writes a knowledge collection.

Storage/search: normalized float32 vectors in disk shards + exact full scan.
This measures that backend, not Chroma/HNSW capacity, real knowledge or RAG quality.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import resource
import shutil
import sys
import time

import numpy as np


def save(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    temp.replace(path)


def run(args):
    root = Path(args.directory).resolve()
    if not 1 <= args.count or not 1 <= args.dimension <= 4096 or args.batch < 1 or args.shard_size < 1:
        raise ValueError('positive bounded dimensions and batch sizes required')
    root.mkdir(parents=True, exist_ok=True)
    manifest_path = root / 'manifest.json'
    spec = {'synthetic': True, 'backend': 'sharded-float32-exact-scan', 'target': args.count,
            'dimension': args.dimension, 'dtype': 'float32', 'seed': args.seed, 'shard_size': args.shard_size}
    manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {**spec, 'shards': []}
    if any(manifest.get(k) != v for k, v in spec.items()):
        raise ValueError('existing benchmark manifest differs; use a new directory')
    complete = sum(s['count'] for s in manifest['shards'])
    required = (args.count - complete) * args.dimension * 4
    if shutil.disk_usage(root).free < required + args.reserve_gib * 1024**3:
        raise RuntimeError('insufficient free disk for remaining vectors and safety reserve')
    started = time.monotonic()
    write_started = started
    for part, start in enumerate(range(0, args.count, args.shard_size)):
        count = min(args.shard_size, args.count - start)
        destination = root / f'shard-{part:04d}.f32'
        if part < len(manifest['shards']):
            recorded = manifest['shards'][part]
            if recorded['count'] != count or destination.stat().st_size != count * args.dimension * 4:
                raise ValueError('completed shard size mismatch')
            continue
        rng = np.random.default_rng(np.random.SeedSequence([args.seed, part]))
        digest = hashlib.sha256()
        temp = destination.with_suffix('.partial')
        with temp.open('wb') as stream:
            for offset in range(0, count, args.batch):
                vectors = rng.random((min(args.batch, count - offset), args.dimension), dtype=np.float32)
                vectors -= np.float32(.5)
                norms = np.sqrt(np.einsum('ij,ij->i', vectors, vectors))
                vectors /= np.maximum(norms[:, None], np.float32(1e-12))
                raw = vectors.tobytes(order='C')
                stream.write(raw)
                digest.update(raw)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(destination)
        manifest['shards'].append({'file': destination.name, 'start': start, 'count': count,
                                  'bytes': destination.stat().st_size, 'sha256': digest.hexdigest()})
        manifest['completed_vectors'] = sum(s['count'] for s in manifest['shards'])
        manifest['status'] = 'writing'
        save(manifest_path, manifest)
        print(json.dumps({'vectors_written': manifest['completed_vectors'], 'target': args.count,
                          'elapsed_s': round(time.monotonic() - started, 2)}), flush=True)
    write_seconds = time.monotonic() - write_started
    total_count = sum((root / s['file']).stat().st_size // (args.dimension * 4) for s in manifest['shards'])
    if total_count != args.count:
        raise ValueError('actual vector count does not match target')
    # Query actual stored rows; each must be the top-1 result across every shard.
    query_ids = sorted(set([0, args.count // 3, args.count // 2, args.count - 1]))
    queries = []
    for qid in query_ids:
        s = next(s for s in manifest['shards'] if s['start'] <= qid < s['start'] + s['count'])
        matrix = np.memmap(root / s['file'], dtype=np.float32, mode='r', shape=(s['count'], args.dimension))
        queries.append(matrix[qid - s['start']].copy())
        del matrix
    query_matrix = np.stack(queries).T.copy()
    best_scores = np.full(len(query_ids), -np.inf, dtype=np.float32)
    best_ids = np.full(len(query_ids), -1, dtype=np.int64)
    search_started = time.monotonic()
    scanned = 0
    for s in manifest['shards']:
        matrix = np.memmap(root / s['file'], dtype=np.float32, mode='r', shape=(s['count'], args.dimension))
        for offset in range(0, s['count'], args.batch):
            block = matrix[offset:offset + args.batch]
            scores = block @ query_matrix
            local_ids = np.argmax(scores, axis=0)
            local_scores = scores[local_ids, np.arange(len(query_ids))]
            better = local_scores > best_scores
            best_ids[better] = s['start'] + offset + local_ids[better]
            best_scores[better] = local_scores[better]
        scanned += s['count']
        del block, matrix
        print(json.dumps({'vectors_scanned': scanned, 'query_batch': len(query_ids),
                          'search_elapsed_s': round(time.monotonic() - search_started, 2)}), flush=True)
    search_seconds = time.monotonic() - search_started
    if best_ids.tolist() != query_ids or not np.all(best_scores > .999):
        raise ValueError('full-corpus self retrieval failed')
    files = [root / s['file'] for s in manifest['shards']]
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    report = {**spec, 'status': 'passed', 'actual_vectors': total_count, 'scanned_vectors': scanned,
              'logical_bytes': sum(f.stat().st_size for f in files),
              'allocated_bytes': sum(f.stat().st_blocks * 512 for f in files),
              'write_seconds_this_run': round(write_seconds, 3),
              'search_batch_seconds': round(search_seconds, 3), 'query_ids': query_ids,
              'top1_ids': best_ids.tolist(), 'top1_scores': best_scores.tolist(),
              'peak_rss_bytes': rss if sys.platform == 'darwin' else rss * 1024,
              'total_seconds_this_run': round(time.monotonic() - started, 3),
              'limitations': ['Synthetic vectors only; no real article count or RAG-quality claim',
                              'Exact disk scan; not a Chroma/HNSW capacity or production QPS test',
                              'One query batch; no percentile latency or concurrency claim']}
    save(root / 'report.json', report)
    manifest['status'] = 'complete'
    save(manifest_path, manifest)
    print(json.dumps(report), flush=True)
    return report


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--count', type=int, default=36_000_000)
    parser.add_argument('--dimension', type=int, default=384)
    parser.add_argument('--batch', type=int, default=50_000)
    parser.add_argument('--shard-size', type=int, default=1_000_000)
    parser.add_argument('--seed', type=int, default=20261014)
    parser.add_argument('--reserve-gib', type=int, default=20)
    parser.add_argument('--directory', default='data/benchmarks/synthetic-36m-384-f32')
    run(parser.parse_args())
