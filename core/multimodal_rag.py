"""Caption-based media RAG. Koa supplies the currently authorized SQL corpus.

The vector index is a derived cache, never the authority for permissions or
source versions. Only exact chunk IDs from this request can be retrieved.
"""
import hashlib
import json
import math
import os
import threading
from typing import Literal

import chromadb
from pydantic import BaseModel, ConfigDict, Field


class Strict(BaseModel):
    model_config = ConfigDict(extra='forbid')


class MediaObservation(Strict):
    description: str = Field(min_length=1, max_length=500)
    frame_index: int = Field(ge=0, le=7)
    time_ms: int | None = Field(default=None, ge=0, le=60000)


class MediaSource(Strict):
    asset_id: str = Field(min_length=1, max_length=100)
    analysis_job_id: str = Field(min_length=1, max_length=100)
    asset_revision: int = Field(ge=1)
    kind: Literal['image', 'video']
    subject: str = Field(min_length=1, max_length=500)
    marketing_summary: str = Field(max_length=1500)
    observations: list[MediaObservation] = Field(max_length=15)
    source_claims: list[str] = Field(max_length=10)
    uncertainties: list[str] = Field(max_length=10)
    limitations: list[str] = Field(max_length=5)


class MediaCorpus(Strict):
    brand_id: str = Field(min_length=1, max_length=100)
    environment: Literal['synthetic', 'sandbox', 'production']
    sources: list[MediaSource] = Field(max_length=100)
    truncated: bool = False


def chunks(corpus: MediaCorpus) -> list[dict]:
    scope = hashlib.sha256(f'{corpus.environment}\0{corpus.brand_id}'.encode()).hexdigest()
    result = []
    for source in corpus.sources:
        if any(len(s) > 500 for s in source.source_claims + source.uncertainties + source.limitations):
            raise ValueError('media_evidence_too_long')
        base = source.model_dump(exclude={'observations'})
        passages = [(None, None, source.subject + '\n' + source.marketing_summary)]
        passages += [(o.frame_index, o.time_ms, source.subject + '\n' + o.description)
                     for o in source.observations]
        for frame_index, time_ms, text in passages:
            entry = {**base, 'frame_index': frame_index, 'time_ms': time_ms, 'excerpt': text}
            raw = json.dumps([scope, entry], sort_keys=True, ensure_ascii=False)
            entry['id'] = hashlib.sha256(raw.encode()).hexdigest()
            entry['scope'] = scope
            result.append(entry)
    # Duplicate observations/sources must not break Chroma upserts.
    return list({entry['id']: entry for entry in result}.values())


_lock = threading.Lock()
_client = None


def collection():
    global _client
    with _lock:
        if _client is None:
            settings = chromadb.Settings(anonymized_telemetry=False)
            mode = os.getenv('STUDIO_MEDIA_RAG_STORAGE', 'local')
            # Development already uses embedded Chroma. Server mode is
            # explicit and must not silently fall back to a different index.
            if mode == 'server':
                _client = chromadb.HttpClient(host=os.getenv('CHROMA_HOST', 'localhost'),
                    port=int(os.getenv('CHROMA_PORT', '8001')), settings=settings)
            elif mode == 'local':
                _client = chromadb.PersistentClient(
                    path=os.getenv('CHROMA_PERSIST_DIRECTORY', 'data/chroma'), settings=settings)
            else:
                raise ValueError('media_rag_storage_invalid')
        value = _client.get_or_create_collection('studio_media_caption_cosine_v1',
            metadata={'hnsw:space': 'cosine', 'description': 'Authorized image and sampled-video captions'})
        if (value.metadata or {}).get('hnsw:space') != 'cosine':
            raise ValueError('media_index_distance_mismatch')
        return value


def retrieve(corpus: MediaCorpus, query: str, *, index=None, top_k: int = 5,
             min_score: float | None = None) -> dict:
    entries = chunks(corpus)
    report = {'status': 'empty', 'method': 'caption-vector-v1',
              'corpus_assets': len(corpus.sources), 'corpus_truncated': corpus.truncated,
              'retrieved': [], 'citations': []}
    if not entries or not query.strip():
        return report
    threshold = float(os.getenv('STUDIO_MEDIA_RAG_MIN_SCORE', '0.25')) if min_score is None else min_score
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError('media_rag_threshold_invalid')
    index = index if index is not None else collection()
    ids = [e['id'] for e in entries]
    existing = set(index.get(ids=ids, include=[])['ids'])
    missing = [e for e in entries if e['id'] not in existing]
    for offset in range(0, len(missing), 100):
        batch = missing[offset:offset + 100]
        index.upsert(ids=[e['id'] for e in batch], documents=[e['excerpt'] for e in batch],
                     metadatas=[{'chunk_key': e['id'], 'scope': e['scope'],
                                 'asset_id': e['asset_id']} for e in batch])
    rows = index.query(query_texts=[query], n_results=min(max(1, top_k), len(ids)),
                       where={'chunk_key': {'$in': ids}}, include=['distances'])
    allowed = {e['id']: e for e in entries}
    for chunk_id, distance in zip(rows['ids'][0], rows['distances'][0]):
        score = max(-1.0, min(1.0, 1 - float(distance))) if math.isfinite(float(distance)) else float('nan')
        if chunk_id not in allowed or not math.isfinite(score) or score < threshold:
            continue
        entry = {k: v for k, v in allowed[chunk_id].items() if k != 'scope'}
        report['retrieved'].append({**entry, 'score': round(score, 4)})
    report['status'] = 'retrieved' if report['retrieved'] else 'no_match'
    return report
