"""Frozen span-based retrieval evaluation. No answer-quality or cost claims.

Input: {"cases":[{"id":"...","family":"...","split":"test",
"query":"...","gold":[{"doc_id":"...","revision":"...","start":0,"end":20}]}]}.
Gold offsets refer to NFC-normalized source text. Labels must be human-reviewed
before using this output for release decisions.
"""
import argparse
import asyncio
import hashlib
import json
import os
import statistics
import sys
import time
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def coverage(gold, retrieved):
    fractions = []
    for span in gold:
        start, end = span['start'], span['end']
        if type(start) is not int or type(end) is not int or not 0 <= start < end:
            raise ValueError('invalid_gold_span')
        intervals = sorted((max(start, c['start_offset']), min(end, c['end_offset']))
            for c in retrieved if c['doc_id'] == span['doc_id'] and c['revision'] == span['revision']
            and c['end_offset'] > start and c['start_offset'] < end)
        total, cursor = 0, start
        for left, right in intervals:
            if right > cursor:
                total += right - max(cursor, left)
                cursor = right
        fractions.append(total / (end-start))
    return sum(fractions)/len(fractions) if fractions else None


async def evaluate(service, cases, mode, generation):
    rows = []
    for case in cases:
        started = time.monotonic()
        try:
            vector = await asyncio.to_thread(service.vector, generation, case['query'], 20)
            if mode == 'hybrid':
                keyword = service.index.keyword(generation, case['query'], 20)
                from core.rag.index import fuse
                retrieved = fuse([vector, keyword], 8)
            else:
                retrieved = vector[:8]
            score = coverage(case['gold'], retrieved)
            rows.append({'id': case['id'], 'family':case['family'], 'status':'succeeded',
                'evidence_span_coverage_at8':score, 'full_required_evidence':score == 1 if score is not None else None,
                'retrieved_ids':[c['id'] for c in retrieved], 'duration_ms':(time.monotonic()-started)*1000})
        except Exception as exc:
            rows.append({'id':case['id'], 'family':case['family'], 'status':'failed',
                'error':type(exc).__name__, 'duration_ms':(time.monotonic()-started)*1000})
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cases', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--generation')
    args = parser.parse_args()
    payload = Path(args.cases).read_bytes()
    cases = json.loads(payload)['cases']
    if not cases or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('empty_or_duplicate_cases')
    family_splits = {}
    for c in cases:
        family_splits.setdefault(c['family'], set()).add(c['split'])
    if any(len(splits)>1 for splits in family_splits.values()):
        raise ValueError('document_family_leaks_across_splits')
    os.environ['RAG_V2_ENABLED'] = 'false'
    from mcp.knowledge_base import KnowledgeBase
    kb = KnowledgeBase(chroma_host=os.getenv('CHROMA_HOST','localhost'),
        chroma_port=int(os.getenv('CHROMA_PORT','8001')),chroma_path=os.getenv('CHROMA_PERSIST_DIRECTORY','data/chroma'))
    service = kb.retrieval_service
    generation = args.generation or service.index.active()
    manifest = service.index.manifest(generation)
    docs = {d['doc_id']:d for d in service.index.documents(generation)}
    for case in cases:
        for span in case['gold']:
            doc = docs.get(span['doc_id'])
            if not doc or doc['revision'] != span['revision'] or span['end']>len(doc['content']):
                raise ValueError('gold_does_not_match_frozen_generation')
    report = {'kind':'retrieval_only','generation_id':generation,'manifest':manifest,
        'cases_sha256':hashlib.sha256(payload).hexdigest(), 'human_reviewed':False,
        'answer_quality':None,'provider_cost':None,'groups':{}}
    for mode in ('vector','hybrid'):
        rows = asyncio.run(evaluate(service,cases,mode,generation))
        successes = [r for r in rows if r['status']=='succeeded']
        # Failed attempts count as zero for coverage over all answerable cases.
        answerable = {c['id'] for c in cases if c['gold']}
        values = [r.get('evidence_span_coverage_at8') or 0 for r in rows if r['id'] in answerable]
        durations = sorted(r['duration_ms'] for r in rows)
        report['groups'][mode] = {'attempts':len(rows),'failures':len(rows)-len(successes),
            'mean_evidence_span_coverage_at8':statistics.mean(values) if values else None,
            'p50_ms':statistics.median(durations),'p95_ms':durations[min(len(durations)-1,int(.95*len(durations)))],
            'rows':rows}
    target = Path(args.output)
    if target.exists():
        raise FileExistsError('refusing_to_overwrite_previous_evaluation')
    target.parent.mkdir(parents=True,exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
