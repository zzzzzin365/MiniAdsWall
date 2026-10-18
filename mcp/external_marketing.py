"""Verified public marketing references, separate from private brand facts."""
import hashlib
import json
import math
import mimetypes
import os
from pathlib import Path
from urllib.parse import urlparse
from core.image_embeddings import METHOD, MODEL_ID, MODEL_REVISION, native_enabled, open_visual_index, search_visual

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


def retrieve_caption(query, *, index=None, catalog=None, top_k=3, min_score=.25, articles_only=False):
    entries = [e for e in records(catalog) if not articles_only or e['kind'] == 'article']
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


def visual_records(catalog=None):
    """Raw pixels, locally curated paths only; captions are output context, never embedding input."""
    path = Path(catalog) if catalog else CATALOG
    entries = records(catalog)
    data = json.loads(path.read_text()) if path.exists() else {'sources': []}
    by_source = {e['source_id']: e for e in entries if e['kind'] == 'article'}
    allowed_root = (path.parent / 'images').resolve()
    images = []
    for source in data['sources']:
        for image in source.get('images', []):
            entry = by_source[source['id']]
            if len(image['caption']) > 4000:
                raise ValueError('reference_caption_too_long')
            supplied = Path(image['local_path'])
            file = (ROOT / supplied).resolve() if catalog is None or path.resolve() == CATALOG.resolve() else (path.parent / supplied).resolve()
            if not file.is_relative_to(allowed_root) or not file.is_file():
                raise ValueError('invalid_reference_image_path')
            if file.stat().st_size > 10 * 1024 * 1024:
                raise ValueError('reference_image_too_large')
            raw = file.read_bytes()
            digest = hashlib.sha256(raw).hexdigest()
            ident = hashlib.sha256(json.dumps([source['url'], image['label'], digest, MODEL_ID, MODEL_REVISION]).encode()).hexdigest()
            images.append({**entry, 'id': ident, 'kind': 'image', 'content': image['caption'],
                           'images': [{'label': image['label'], 'caption': image['caption'],
                                       'original_url': image['url'], 'mime': mimetypes.guess_type(str(file))[0]}],
                           'image_sha256': digest, 'retrieval_method': METHOD,
                           'embedding_model': MODEL_ID, 'embedding_revision': MODEL_REVISION, '_raw': raw})
    return images


def retrieve_images(query, *, catalog=None, index=None, top_k=3, min_score=0., model=None):
    entries = visual_records(catalog)
    report = {'status': 'empty', 'method': METHOD, 'retrieved': [], 'citations': []}
    if not entries or not query.strip():
        return report
    index = index if index is not None else open_visual_index('external_image_chinese_clip_v1')
    report['retrieved'] = search_visual(entries, query, index=index, top_k=top_k, min_score=min_score, model=model)
    report['status'] = 'retrieved' if report['retrieved'] else 'no_match'
    return report


def retrieve(query, *, index=None, catalog=None, top_k=3, min_score=.25, image_index=None, model=None, mode='all'):
    if mode not in {'all', 'images', 'articles'}:
        raise ValueError('invalid_reference_search_mode')
    if not native_enabled():
        return retrieve_caption(query, index=index, catalog=catalog, top_k=top_k, min_score=min_score)
    images = retrieve_images(query, catalog=catalog, index=image_index, top_k=top_k, model=model) if mode != 'articles' else {'retrieved': []}
    if mode == 'images':
        return images
    articles = retrieve_caption(query, index=index, catalog=catalog, top_k=top_k, min_score=min_score,
                                articles_only=True)
    if mode == 'articles':
        return {**articles, 'method': 'text-vector-v1'}
    # Scores from MiniLM and Chinese-CLIP are not comparable. Rank-fuse the two lists.
    ranked = []
    for rank in range(top_k):
        for result in (images, articles):
            if rank < len(result['retrieved']):
                entry = result['retrieved'][rank]
                ranked.append({**entry, 'fusion_score': round(1 / (60 + rank + 1), 6)})
    selected = ranked[:top_k]
    return {'status': 'retrieved' if selected else 'empty', 'method': 'native-image-plus-text-v1',
            'retrieved': selected, 'citations': []}
