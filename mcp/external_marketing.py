"""Verified public marketing references, separate from private brand facts."""
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse

import chromadb

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / 'data/external-marketing/catalog.json'
SOURCE = 'external_marketing_v1'


def records(catalog=None):
    path = Path(catalog) if catalog else CATALOG
    if not path.exists():
        return []
    data = json.loads(path.read_text())
    if data.get('synthetic') is not False:
        raise ValueError('public knowledge must contain real sources')
    items = []
    domains = {'zhihu': {'zhuanlan.zhihu.com', 'www.zhihu.com'},
               'twitter': {'x.com', 'twitter.com'}, 'xiaohongshu': {'www.xiaohongshu.com'}}
    for source in data['sources']:
        url = urlparse(source['url'])
        if url.scheme != 'https' or url.hostname not in domains.get(source['platform'], set()):
            raise ValueError('unexpected source domain')
        if not source.get('coverage') or not source.get('summary') or len(source['summary']) > 4000:
            raise ValueError('source must have bounded verified coverage and summary')
        passages = [('article', source['summary'], None)]
        passages += [('image', image['caption'], image['label']) for image in source.get('images', [])]
        for kind, content, image_label in passages:
            key = hashlib.sha256(json.dumps([source['url'], kind, content], ensure_ascii=False).encode()).hexdigest()
            images = [dict(label=i['label'], caption=i['caption'], original_url=i['url'],
                           mime=mimetypes.guess_type(i['local_path'])[0])
                      for i in source.get('images', []) if image_label is None or i['label'] == image_label]
            items.append({'id': key, 'source_id': source['id'], 'kind': kind, 'title': source['title'], 'author': source['author'],
                          'platform': source['platform'], 'url': source['url'], 'content': content,
                          'coverage': source['coverage'], 'images': images,
                          'trust': 'external_author_view', 'retrieved_at': source['retrieved_at']})
    return list({e['id']: e for e in items}.values())


def open_index():
    settings = chromadb.Settings(anonymized_telemetry=False)
    mode = os.getenv('STUDIO_MEDIA_RAG_STORAGE', 'local')
    if mode == 'local':
        client = chromadb.PersistentClient(path=os.getenv('CHROMA_PERSIST_DIRECTORY', str(ROOT / 'data/chroma')),
                                          settings=settings)
    elif mode == 'server':
        client = chromadb.HttpClient(host=os.getenv('CHROMA_HOST', 'localhost'),
                                     port=int(os.getenv('CHROMA_PORT', '8001')), settings=settings)
    else:
        raise ValueError('invalid storage mode')
    index = client.get_or_create_collection('knowledge_base_cosine_v1', metadata={'hnsw:space': 'cosine'})
    if (index.metadata or {}).get('hnsw:space') != 'cosine':
        raise ValueError('knowledge distance mismatch')
    return index


def sync(index=None, catalog=None):
    entries = records(catalog)
    if not entries:
        return {'sources': 0, 'images': 0, 'vectors': 0, 'inserted': 0}
    index = index if index is not None else open_index()
    existing = set(index.get(ids=[e['id'] for e in entries], include=[])['ids'])
    pending = [e for e in entries if e['id'] not in existing]
    for offset in range(0, len(pending), 100):
        batch = pending[offset:offset + 100]
        index.upsert(ids=[e['id'] for e in batch], documents=[e['content'] for e in batch], metadatas=[{
            'source': SOURCE, 'external_chunk_id': e['id'], 'title': e['title'], 'source_url': e['url'],
            'source_title': e['title'], 'platform': e['platform'], 'author': e['author'], 'kind': e['kind'],
            'coverage': e['coverage'], 'trust': e['trust'], 'retrieved_at': e['retrieved_at'],
            'images_json': json.dumps(e['images'], ensure_ascii=False)} for e in batch])
    return {'sources': len({e['url'] for e in entries}),
            'images': sum(e['kind'] == 'image' for e in entries), 'vectors': len(entries), 'inserted': len(pending)}


def retrieve(query, *, index=None, catalog=None, top_k=3, min_score=.25):
    entries = records(catalog)
    report = {'status': 'empty', 'retrieved': [], 'citations': []}
    if not entries:
        return report
    index = index if index is not None else open_index()
    sync(index, catalog)
    by_id = {e['id']: e for e in entries}
    rows = index.query(query_texts=[query], n_results=min(top_k, len(entries)),
                       where={'external_chunk_id': {'$in': list(by_id)}}, include=['distances'])
    for ident, distance in zip(rows['ids'][0], rows['distances'][0]):
        score = 1 - float(distance)
        if ident in by_id and math.isfinite(score) and score >= min_score:
            report['retrieved'].append({**by_id[ident], 'score': round(max(-1., min(1., score)), 4)})
    report['status'] = 'retrieved' if report['retrieved'] else 'no_match'
    return report
