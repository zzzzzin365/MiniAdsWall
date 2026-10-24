"""Shared opt-in retrieval service used by knowledge_search in both entrypoints."""
import asyncio
import json
import logging
import os
import time

from core.rag.index import TextIndex, digest, fuse

logger = logging.getLogger(__name__)


def flag(name, default='false'):
    value = os.getenv(name, default).strip().lower()
    if value not in {'true', 'false'}:
        raise ValueError('invalid_' + name)
    return value == 'true'


class RetrievalService:
    def __init__(self, kb, path):
        self.kb = kb
        self.index = TextIndex(path)
        self.local_reranker = None
        if os.getenv('RAG_V2_RERANKER', 'llm') == 'dedicated':
            from core.rag.reranker import LocalReranker
            self.local_reranker = LocalReranker(os.environ['RAG_V2_RERANK_MODEL_PATH'])

    def embedding_version(self):
        fn = self.kb._embedding_function
        return digest([type(fn).__module__, type(fn).__qualname__,
            getattr(fn, 'MODEL_NAME', None), getattr(fn, '_MODEL_SHA256', None)])

    def build(self, documents):
        generation, name, chunks = self.index.stage(documents, self.embedding_version())
        if len(chunks) > 10000:
            raise ValueError('rag_v2_capacity_limit_10000_chunks')
        collection = self.kb._client.get_or_create_collection(name,
            metadata={'hnsw:space': 'cosine', 'generation': generation},
            embedding_function=self.kb._embedding_function)
        for start in range(0, len(chunks), 100):
            batch = chunks[start:start+100]
            collection.upsert(ids=[c['id'] for c in batch], documents=[c['content'] for c in batch],
                              metadatas=[{'doc_id': c['doc_id']} for c in batch])
        ids = []
        for offset in range(0, collection.count(), 500):
            ids.extend(collection.get(limit=500, offset=offset, include=[])['ids'])
        # A query smoke check catches incompatible embedding dimensions before publication.
        collection.query(query_texts=[chunks[0]['content']], n_results=1)
        self.index.publish(generation, ids)
        return {'generation_id': generation, 'documents': len(documents), 'chunks': len(chunks)}

    def vector(self, generation, query, k):
        manifest = self.index.manifest(generation)
        if manifest['embedding_version'] != self.embedding_version():
            raise RuntimeError('embedding_version_mismatch')
        collection = self.kb._client.get_collection(manifest['collection_name'],
                                                    embedding_function=self.kb._embedding_function)
        # Filter deny-list before Top-K, not after it. This first release is bounded.
        allowed = self.index.chunks(generation)
        if not allowed:
            return []
        rows = {c['id']: c for c in allowed}
        doc_ids = sorted({c['doc_id'] for c in allowed})
        where = {'doc_id': {'$in': doc_ids}}
        result = collection.query(query_texts=[query], n_results=min(k, len(rows)), where=where)
        return [{**rows[cid], 'vector_score': 1-float(distance)}
                for cid, distance in zip(result['ids'][0], result['distances'][0]) if cid in rows]

    def revalidate(self, chunks):
        documents, result = {}, []
        for chunk in chunks:
            generation = chunk.get('generation_id')
            if not generation:
                continue
            if generation not in documents:
                self.index.manifest(generation)
                documents[generation] = {d['doc_id']: d for d in self.index.documents(generation)}
            doc = documents[generation].get(chunk.get('doc_id'))
            start, end = chunk.get('start_offset'), chunk.get('end_offset')
            if (doc and chunk.get('scope_id') == 'public' and self.index.allowed(doc['doc_id'])
                    and doc['revision'] == chunk.get('revision')
                    and type(start) is int and type(end) is int and 0 <= start < end <= len(doc['content'])
                    and doc['content'][start:end] == chunk['content']):
                result.append(chunk)
        return result

    async def search(self, manager, query, top_k=8, *, stage_timeout=3., additional_queries=None, request_id=None):
        from core.rag.usage import receipts
        calls = []
        token = receipts.set(calls)
        try:
            result = await self._search(manager, query, top_k, stage_timeout=stage_timeout,
                additional_queries=additional_queries, request_id=request_id)
            if result.retrieval is not None:
                result.retrieval['model_calls'] = calls
            return result
        finally:
            receipts.reset(token)

    async def _search(self, manager, query, top_k=8, *, stage_timeout=3., additional_queries=None, request_id=None):
        from mcp.tool_manager import ToolResult
        started = time.monotonic()
        generation = self.index.active()
        if not generation:
            return ToolResult(False, [], 'knowledge_search', error='rag_v2_generation_missing')
        top_k = max(1, min(int(top_k), 10))
        # Total bound is shared across stages, not reset for each subquery.
        deadline = started + 3 * stage_timeout
        timings, failures = {}, []
        async def bounded(awaitable):
            remaining = deadline - time.monotonic()
            return await asyncio.wait_for(awaitable, max(.001, min(stage_timeout, remaining)))
        queries = [query]
        if manager is not None and flag('RAG_V2_REWRITE_ENABLED', 'true'):
            tick = time.monotonic()
            queries += [q for q in (additional_queries or []) if isinstance(q, str) and q.strip()]
            try:
                rewrite = await bounded(manager.rewrite_query(query, n=2))
                if not isinstance(rewrite, list):
                    raise ValueError('invalid_rewrite')
                queries += [q for q in rewrite if isinstance(q, str) and q.strip()]
            except Exception:
                failures.append('rewrite')
            timings['rewrite_ms'] = (time.monotonic()-tick)*1000
        queries = list(dict.fromkeys(queries))[:3]
        rankings = []
        async def branch(kind, q):
            tick = time.monotonic()
            try:
                fn = self.vector if kind == 'vector' else self.index.keyword
                rows = await bounded(asyncio.to_thread(fn, generation, q, 20))
                return rows, None, (time.monotonic()-tick)*1000
            except Exception:
                return [], kind, (time.monotonic()-tick)*1000
        kinds = ['vector', 'keyword'] if flag('RAG_V2_HYBRID_ENABLED', 'true') else ['vector']
        results = await asyncio.gather(*(branch(kind, q) for q in queries for kind in kinds))
        successful = 0
        for i, (rows, failure, duration) in enumerate(results):
            timings[f'recall_{i}_ms'] = duration
            if failure:
                failures.append(f'{failure}:{i//len(kinds)}')
            else:
                successful += 1
                rankings.append(rows)
        metadata = {'request_id': request_id, 'generation_id': generation, 'partial': bool(failures), 'failed_branches': failures,
                    'retrieval_mode': '+'.join(kinds), 'timings': timings, 'rerank_applied': False,
                    'config_hash': digest([kinds, queries, top_k, os.getenv('RAG_V2_RERANKER','llm'),
                                          flag('RAG_V2_PARENT_ENABLED')])}
        if not successful:
            return ToolResult(False, [], 'knowledge_search', error='retrieval_unavailable', retrieval=metadata)
        candidates = fuse(rankings, 30)
        mode = os.getenv('RAG_V2_RERANKER', 'llm')
        if mode not in {'llm', 'none', 'dedicated'}:
            raise ValueError('unsupported_rag_v2_reranker')
        selected = candidates[:top_k]
        tick = time.monotonic()
        if manager is not None and mode == 'llm' and len(candidates) > top_k:
            try:
                selected = await bounded(manager._rerank(query, candidates, top_k))
                metadata['rerank_applied'] = True
            except Exception:
                failures.append('rerank')
        if manager is not None and mode == 'dedicated' and len(candidates) > top_k:
            try:
                if self.local_reranker is None:
                    raise RuntimeError('local_reranker_not_loaded')
                selected = await bounded(asyncio.to_thread(self.local_reranker.rank, query, candidates, top_k))
                metadata['rerank_applied'] = True
                metadata['reranker_revision'] = self.local_reranker.revision
            except Exception:
                failures.append('rerank')
        timings['rerank_ms'] = (time.monotonic()-tick)*1000
        selected = self.revalidate([{**c, "generation_id": generation} for c in selected])
        if flag('RAG_V2_PARENT_ENABLED'):
            parents, expanded = set(), []
            for child in selected:
                pid = child['parent_id']
                if pid in parents:
                    continue
                parents.add(pid)
                expanded.append({**child, 'id': pid, 'content': child['parent_text'],
                    'start_offset': child['parent_start'], 'end_offset': child['parent_end'],
                    'child_id': child['id'], 'hit_start': child['start_offset']})
                if len(expanded) == 4:
                    break
            selected = expanded
        # UTF-8 bytes upper-bound token count for byte-tokenized models; conservative
        # until model-specific tokenizers are supplied. No unchecked text is added later.
        budget, limited = 4000, []
        for row in selected:
            size = len(row['content'].encode('utf-8'))
            if size > budget:
                metadata['context_truncated'] = True
                # Keep the retrieved hit when a large parent must be cropped.
                begin = max(0, row.get('hit_start', row['start_offset']) - row['start_offset'] - 100)
                text = row['content'][begin:].encode('utf-8')[:budget].decode('utf-8', errors='ignore')
                row = {**row, 'start_offset': row['start_offset'] + begin}
                if not text.strip():
                    break
                end = row['start_offset'] + len(text)
                row = {**row, 'content': text, 'end_offset': end,
                       'id': digest([row['doc_id'], row['revision'], row['start_offset'], end, text])}
                size = len(text.encode('utf-8'))
            budget -= size
            limited.append({key: value for key, value in {**row, 'generation_id': generation}.items()
                            if key != 'parent_text'})
        metadata['partial'] = bool(failures)
        from core.rag.usage import receipts
        metadata['model_calls'] = list(receipts.get() or [])
        metadata['sources'] = [{'id': c['id'], 'doc_id': c['doc_id'], 'revision': c['revision']} for c in limited]
        timings['total_ms'] = (time.monotonic()-started)*1000
        logger.info('rag_retrieval %s', json.dumps(metadata, ensure_ascii=False))
        return ToolResult(True, limited, 'knowledge_search', reranked=metadata['rerank_applied'], retrieval=metadata)
