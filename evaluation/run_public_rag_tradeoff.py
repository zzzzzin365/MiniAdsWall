"""Read-only public/general snapshots, paired retrieval and synthetic ANN tests.

No merchant DB, private media, credentials, or online model requests are read.
An earlier development-selected lexical weight is frozen before this run.
"""
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import platform
import random
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import chromadb
from chromadb.utils.embedding_functions import DefaultEmbeddingFunction
import hnswlib
import numpy as np
from mcp.knowledge_base import KnowledgeBase
from mcp.external_marketing import records


def sha(data):
    return hashlib.sha256(data).hexdigest()


def save(path, value):
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temporary.replace(path)


def tokens(text):
    return re.findall(r'[a-z0-9]+', text.lower()) + [
        segment[i:i + 2] for segment in re.findall(r'[\u4e00-\u9fff]+', text)
        for i in range(len(segment) - 1)]


def percentiles(values):
    return {'n': len(values), 'p50_ms': float(np.percentile(values, 50)),
            'p95_ms': float(np.percentile(values, 95))}


def public_experiment(out, previous, repeats):
    corpus = json.loads((previous / 'corpus.json').read_text())
    ids, docs, metas = (corpus[k] for k in ('ids', 'documents', 'metadatas'))
    # Verify every copied record against allowed public source text, before use.
    kb = KnowledgeBase.__new__(KnowledgeBase)
    allowed = {}
    for doc in kb._default_docs():
        for number, text in enumerate(kb._chunk_text(doc['content'], chunk_size=500)):
            ident = hashlib.md5(f'default_ads_{doc["title"]}_{number}'.encode()).hexdigest()
            allowed[ident] = (text, 'default_ads')
    for row in records():
        allowed[row['id']] = (row['content'], 'external_marketing_v1')
    for ident, text, meta in zip(ids, docs, metas):
        if ident not in allowed or allowed[ident] != (text, meta.get('source')):
            raise ValueError('Snapshot contains unverified or non-public source')
    old_vectors = np.load(previous / 'vectors.npz')
    if old_vectors['ids'].tolist() != ids:
        raise ValueError('Snapshot vector mapping mismatch')
    vectors = old_vectors['embeddings']
    cases = json.loads((previous / 'cases.json').read_text())
    selection = json.loads((previous / 'development-selection.json').read_text())
    weight = selection['selected_weight']
    tests = [row for row in cases if row['split'] == 'test']
    if any(not set(group) <= set(ids) for row in cases for group in row['gold']):
        raise ValueError('Gold refers to unknown corpus records')
    save(out / 'corpus.json', corpus)
    save(out / 'cases.json', cases)
    save(out / 'frozen-selection.json', selection)
    ef = DefaultEmbeddingFunction()
    client = chromadb.PersistentClient(path=str(out / 'isolated-index'),
                                       settings=chromadb.Settings(anonymized_telemetry=False))
    index = client.create_collection('public_tradeoff', metadata={'hnsw:space': 'cosine'}, embedding_function=ef)
    index.add(ids=ids, documents=docs, metadatas=metas, embeddings=vectors.tolist())
    ef([tests[0]['query']])
    start = time.perf_counter()
    cache = {row['query']: ef([row['query']])[0] for row in tests}
    precompute_seconds = time.perf_counter() - start
    np.savez(out / 'query-vectors.npz', queries=list(cache), vectors=np.asarray(list(cache.values())))
    counters = [Counter(tokens(doc)) + Counter({k: 4 * v for k, v in Counter(tokens(meta.get('title', ''))).items()})
                for doc, meta in zip(docs, metas)]
    df = Counter(key for counter in counters for key in counter)
    idf = {key: math.log((len(ids) + 1) / (n + 1)) + 1 for key, n in df.items()}
    lexical_vectors = [{key: (1 + math.log(n)) * idf[key] for key, n in counter.items()} for counter in counters]
    norms = [math.sqrt(sum(v * v for v in row.values())) or 1 for row in lexical_vectors]

    def retrieve(query, hybrid, hit):
        begin = time.perf_counter()
        embedding = cache[query] if hit else ef([query])[0]
        embedded = time.perf_counter()
        result = index.query(query_embeddings=[embedding], n_results=10, include=['distances'])
        dense = result['ids'][0]
        indexed = time.perf_counter()
        ranking = dense[:5]
        if hybrid:
            counts = Counter(tokens(query))
            qvec = {key: (1 + math.log(n)) * idf[key] for key, n in counts.items() if key in idf}
            norm = math.sqrt(sum(v * v for v in qvec.values())) or 1
            scores = np.asarray([sum(v * row.get(key, 0) for key, v in qvec.items()) / (norm * dn)
                                 for row, dn in zip(lexical_vectors, norms)])
            fused = {ident: (1 - weight) / (60 + rank) for rank, ident in enumerate(dense, 1)}
            for rank, j in enumerate(np.argsort(-scores, kind='stable')[:10], 1):
                if scores[j] > 0:
                    fused[ids[j]] = fused.get(ids[j], 0) + weight / (60 + rank)
            ranking = sorted(fused, key=lambda ident: (-fused[ident], ident))[:5]
        end = time.perf_counter()
        return ranking, {'embedding_ms': (embedded - begin) * 1000,
                         'index_ms': (indexed - embedded) * 1000,
                         'fusion_ms': (end - indexed) * 1000, 'ms': (end - begin) * 1000}

    arms = {'A1': (False, False), 'F': (False, True), 'H': (True, False), 'R': (True, True)}
    quality = []
    for case in tests:
        rankings = {arm: retrieve(case['query'], hybrid, True)[0] for arm, (hybrid, _) in arms.items()}
        metrics = {}
        for arm, hits in rankings.items():
            found = sum(bool(set(group) & set(hits)) for group in case['gold'])
            metrics[arm] = {'recall': found / len(case['gold']) if case['gold'] else None,
                            'complete': int(found == len(case['gold'])) if case['gold'] else None}
        quality.append({**case, 'rankings': rankings, 'metrics': metrics})
    save(out / 'quality-trace.json', quality)
    expected = {row['id']: row['rankings'] for row in quality}
    rng = random.Random(20261014)
    latency = []
    with (out / 'latency.jsonl').open('w') as stream:
        for hit_ratio in (0, .5, 1):
            jobs = [(case, repeat) for case in tests for repeat in range(repeats)]
            rng.shuffle(jobs)
            positions = list(range(len(jobs)))
            rng.shuffle(positions)
            hit_positions = set(positions[:round(len(jobs) * hit_ratio)])
            for number, (case, repeat) in enumerate(jobs):
                order = list(arms)
                rng.shuffle(order)
                for arm in order:
                    hybrid, cache_enabled = arms[arm]
                    hit = cache_enabled and number in hit_positions
                    retrieved, timing = retrieve(case['query'], hybrid, hit)
                    if retrieved != expected[case['id']][arm]:
                        raise AssertionError('Cache/embedding changed ranking')
                    row = {'case_id': case['id'], 'repeat': repeat, 'profile_hit_ratio': hit_ratio,
                           'arm': arm, 'hit': hit, 'status': 'success', **timing}
                    latency.append(row)
                    stream.write(json.dumps(row) + '\n')
                if (number + 1) % 36 == 0:
                    stream.flush()
                    print(f'public hit={hit_ratio}: {number + 1}/{len(jobs)} paired jobs', flush=True)
    results = {}
    for arm in arms:
        results[arm] = {}
        for scope in ('all', 'general', 'external'):
            rows = [row for row in quality if row['answerable'] and (scope == 'all' or row['scope'] == scope)]
            results[arm][scope] = {'n': len(rows), 'recall_at5': float(np.mean([row['metrics'][arm]['recall'] for row in rows])),
                                   'complete_at5': float(np.mean([row['metrics'][arm]['complete'] for row in rows]))}
    speed = {str(ratio): {arm: percentiles([row['ms'] for row in latency if row['arm'] == arm and row['profile_hit_ratio'] == ratio])
                         for arm in arms} for ratio in (0, .5, 1)}
    positives = [row for row in quality if row['answerable']]
    families = sorted({row['family'] for row in positives})
    groups = [[row for row in positives if row['family'] == family] for family in families]
    bootstrap = np.random.default_rng(20261014)
    deltas = []
    for _ in range(10000):
        sample = [row for j in bootstrap.integers(0, len(groups), len(groups)) for row in groups[j]]
        deltas.append(np.mean([row['metrics']['R']['recall'] - row['metrics']['A1']['recall'] for row in sample]))
    summary = {'scope': 'public/general text and public image-caption retrieval only', 'chunks': len(ids),
               'test_cases': len(tests), 'positive_cases': len(positives), 'negative_cases': len(tests) - len(positives),
               'families': len(groups), 'weight_frozen_from_prior_development': weight, 'quality': results, 'speed': speed,
               'recall_delta': results['R']['all']['recall_at5'] - results['A1']['all']['recall_at5'],
               'recall_delta_ci95': np.percentile(deltas, [2.5, 97.5]).tolist(),
               'cache_precompute_seconds': precompute_seconds, 'cache_estimated_vector_bytes': len(cache) * 384 * 4,
               'model_calls': 0, 'joint_answer_quality_goal_proven': False,
               'limitations': ['Same convenience questions previously evaluated; not a new blind test or independent annotation.',
                               'A1/F/H/R here denote dense, cached dense, hybrid, cached hybrid; not the private-media plan arms.',
                               'Cache hit ratios are controlled bypass profiles, not observed online traffic or an eviction-policy test.',
                               'No authorization, paid rewrite, original-image reading or final-answer latency/quality tested.',
                               'Top5 offline ranking, no similarity cutoff; not an exact production endpoint comparison.',
                               'Negative questions have no gold; candidate returns do not measure hallucination or refusal.',
                               'Chroma snapshot only; production collections and private merchant storage untouched.']}
    save(out / 'public-summary.json', summary)
    return summary


def synthetic_experiment(out, pilot, repeats):
    parent = ROOT / 'data/benchmarks/synthetic-36m-384-f32/shard-0000.f32'
    base = np.array(np.memmap(parent, dtype='float32', mode='r', shape=(1000000, 384))[:100000])
    queries = np.load(pilot / 'synthetic-queries.npz')['queries']
    exact = json.loads((pilot / 'synthetic-exact-gold.json').read_text())
    previous = json.loads((pilot / 'synthetic-summary.json').read_text())
    if sha(base.tobytes()) != previous['document_vectors_sha256'] or sha(queries.tobytes()) != previous['query_vectors_sha256']:
        raise ValueError('Frozen synthetic vectors changed')
    # Revalidate exact gold instead of assuming old results are still correct.
    for i, query in enumerate(queries):
        scores = base @ query
        top = np.argpartition(-scores, 4)[:5]
        top = top[np.argsort(-scores[top], kind='stable')]
        if top.tolist() != exact[i]:
            raise AssertionError('Exact gold changed')
    ann = hnswlib.Index(space='cosine', dim=384)
    ann.load_index(str(pilot / 'synthetic-hnsw.bin'), max_elements=100000)
    ann.set_num_threads(1)
    widths = [10, 64, 256, 1024, 4096, 16384]
    rng = random.Random(20261014)
    trace = []
    for repeat in range(repeats):
        order = list(range(len(queries)))
        rng.shuffle(order)
        for i in order:
            arms = ['exact'] + widths.copy()
            rng.shuffle(arms)
            for arm in arms:
                if arm != 'exact':
                    ann.set_ef(arm)
                start = time.perf_counter()
                if arm == 'exact':
                    scores = base @ queries[i]
                    top = np.argpartition(-scores, 4)[:5]
                    got = top[np.argsort(-scores[top], kind='stable')].tolist()
                else:
                    got = ann.knn_query(queries[i:i + 1], k=5, num_threads=1)[0][0].tolist()
                milliseconds = (time.perf_counter() - start) * 1000
                trace.append({'repeat': repeat, 'query_id': i, 'arm': str(arm), 'ms': milliseconds,
                              'top5': got, 'neighbor_recall_at5': len(set(got) & set(exact[i])) / 5})
        print(f'synthetic randomized round {repeat + 1}/{repeats}', flush=True)
    save(out / 'synthetic-trace.json', trace)
    result = {'vectors': len(base), 'parent_vectors': 36000000, 'queries': len(queries), 'repeats': repeats,
              'backend': 'hnswlib direct, fixed existing index, warm RAM, one query at a time',
              'arms': {str(arm): {**percentiles([row['ms'] for row in trace if row['arm'] == str(arm)]),
                                  'neighbor_recall_at5': float(np.mean([row['neighbor_recall_at5'] for row in trace if row['arm'] == str(arm)]))}
                       for arm in ['exact'] + widths},
              'vector_sha256': sha(base.tobytes()), 'query_sha256': sha(queries.tobytes()),
              'limits': ['100k subset of 36M, not full 36M ANN performance.', 'Nearest-neighbor agreement, not semantic or multimodal recall.',
                         'Frozen index build reused; ef/query/order randomized, but no server or concurrent-user benchmark.']}
    save(out / 'synthetic-summary.json', result)
    return result


def main(args):
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    save(out / 'manifest.json', {'status': 'running', 'seed': 20261014, 'platform': platform.platform(),
                                'script_sha256': sha(Path(__file__).read_bytes()),
                                'git_head': subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=ROOT, text=True).strip(),
                                'scope': 'public/default snapshot and isolated synthetic subset only', 'private_db_read': False})
    real = public_experiment(out, ROOT / 'docs/validation/general-rag-ab/20261014-v1', args.repeats)
    synthetic = synthetic_experiment(out, ROOT / 'docs/validation/multimodal-rag-ab/20261014-pilot-v1', args.repeats)
    save(out / 'summary.json', {'public': real, 'synthetic': synthetic, 'paid_model_calls': 0})
    manifest = json.loads((out / 'manifest.json').read_text())
    manifest['status'] = 'completed'
    save(out / 'manifest.json', manifest)
    print(json.dumps({'public_recall_delta': real['recall_delta'], 'speed': real['speed'], 'synthetic': synthetic['arms']}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--repeats', type=int, default=2)
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error('repeats must be positive')
    main(args)
