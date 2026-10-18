"""Pixel-native Chinese-CLIP embeddings. No caption is used to encode an image."""
import base64
import hashlib
import io
import math
import os
import re
import threading
from pathlib import Path

MODEL_ID = 'OFA-Sys/chinese-clip-vit-base-patch16'
MODEL_REVISION = '36e679e65c2a2fead755ae21162091293ad37834'
DIMENSION = 512
METHOD = 'chinese-clip-image-v1'
_lock = threading.RLock()
_instance = None


def native_enabled():
    mode = os.getenv('STUDIO_MEDIA_RAG_MODE', 'native')
    if mode not in {'native', 'caption'}:
        raise ValueError('invalid_media_rag_mode')
    return mode == 'native'


def validate_vector(vector):
    if len(vector) != DIMENSION or any(isinstance(x, bool) or not math.isfinite(float(x)) for x in vector):
        raise ValueError('invalid_visual_embedding')
    norm = math.sqrt(sum(float(x) ** 2 for x in vector))
    if abs(norm - 1) > .002:
        raise ValueError('visual_embedding_not_normalized')
    return [float(x) for x in vector]


def decode_image(data_url):
    match = re.fullmatch(r'data:image/(jpeg|png|webp);base64,([A-Za-z0-9+/=]+)', data_url)
    if not match:
        raise ValueError('invalid_media_data')
    raw = base64.b64decode(match[2], validate=True)
    if not raw or len(raw) > 10 * 1024 * 1024:
        raise ValueError('image_size_limit')
    return raw


class ChineseClip:
    model_id = MODEL_ID
    revision = MODEL_REVISION
    dimension = DIMENSION

    def __init__(self, *, local_files_only=True):
        import torch
        from transformers import ChineseCLIPModel, ChineseCLIPProcessor
        self.torch = torch
        torch.set_num_threads(max(1, int(os.getenv('IMAGE_EMBEDDING_THREADS', '4'))))
        location = os.getenv('IMAGE_EMBEDDING_MODEL_PATH', MODEL_ID)
        kwargs = {'revision': MODEL_REVISION, 'local_files_only': local_files_only}
        # No custom model code or unpinned model revision is executed.
        self.processor = ChineseCLIPProcessor.from_pretrained(location, use_fast=False, **kwargs)
        self.model = ChineseCLIPModel.from_pretrained(location, **kwargs).eval().to('cpu')

    def _normalize(self, features):
        return (features / features.norm(p=2, dim=-1, keepdim=True)).cpu().tolist()

    def images(self, raws):
        from PIL import Image, ImageOps
        images = []
        for raw in raws:
            with Image.open(io.BytesIO(raw)) as im:
                if im.width * im.height > 40_000_000:
                    raise ValueError('image_pixel_limit')
                if im.format not in {'PNG', 'JPEG', 'WEBP'}:
                    raise ValueError('unsupported_image_format')
                images.append(ImageOps.exif_transpose(im).convert('RGB'))
        with _lock, self.torch.inference_mode():
            inputs = self.processor(images=images, return_tensors='pt')
            return self._normalize(self.model.get_image_features(**inputs))

    def text(self, query):
        with _lock, self.torch.inference_mode():
            inputs = self.processor(text=[query], padding=True, truncation=True, max_length=512,
                                    return_tensors='pt')
            # Match ChineseCLIPModel.forward: project the unpooled final CLS state.
            hidden = self.model.text_model(**inputs, return_dict=True).last_hidden_state[:, 0, :]
            return self._normalize(self.model.text_projection(hidden))[0]


def encoder():
    global _instance
    with _lock:
        if _instance is None:
            try:
                _instance = ChineseClip()
            except (ImportError, OSError) as exc:
                raise RuntimeError('image_embedding_model_unavailable: run scripts/setup_image_retrieval.py first') from exc
        return _instance


def embed_frames(frames, *, model=None):
    raws = [decode_image(f.data_url if hasattr(f, 'data_url') else f['data_url']) for f in frames]
    if sum(map(len, raws)) > 16 * 1024 * 1024:
        raise ValueError('media_size_limit')
    vectors = (model or encoder()).images(raws)
    if len(vectors) != len(raws):
        raise ValueError('image_embedding_count_mismatch')
    return [{'model': MODEL_ID, 'revision': MODEL_REVISION, 'image_sha256': hashlib.sha256(raw).hexdigest(),
             'frame_index': i, 'time_ms': f.time_ms if hasattr(f, 'time_ms') else f.get('time_ms'),
             'vector': validate_vector(v)} for i, (raw, f, v) in enumerate(zip(raws, frames, vectors))]


def open_visual_index(name):
    import chromadb
    settings = chromadb.Settings(anonymized_telemetry=False)
    mode = os.getenv('STUDIO_MEDIA_RAG_STORAGE', 'local')
    if mode == 'local':
        client = chromadb.PersistentClient(path=str(Path(os.getenv('CHROMA_PERSIST_DIRECTORY', 'data/chroma')).resolve()), settings=settings)
    elif mode == 'server':
        client = chromadb.HttpClient(host=os.getenv('CHROMA_HOST', 'localhost'),
                                    port=int(os.getenv('CHROMA_PORT', '8001')), settings=settings)
    else:
        raise ValueError('invalid_storage_mode')
    expected = {'hnsw:space': 'cosine', 'model': MODEL_ID, 'revision': MODEL_REVISION, 'dimension': DIMENSION}
    index = client.get_or_create_collection(name, metadata=expected, embedding_function=None)
    if any((index.metadata or {}).get(k) != v for k, v in expected.items()):
        raise ValueError('visual_index_model_mismatch')
    return index


def search_visual(entries, query, *, index, top_k, min_score=0.0, model=None):
    """Only explicit pixel vectors are inserted; Chroma never embeds captions."""
    if not entries or not query.strip():
        return []
    if not math.isfinite(min_score) or not -1 <= min_score <= 1 or not 1 <= top_k <= 20:
        raise ValueError('invalid_visual_search_parameters')
    by_id = {e['id']: e for e in entries}
    existing = set(index.get(ids=list(by_id), include=[])['ids'])
    pending = [e for e in by_id.values() if e['id'] not in existing]
    model = model or encoder()
    for offset in range(0, len(pending), 8):
        batch = pending[offset:offset + 8]
        raw_entries = [e for e in batch if '_raw' in e]
        generated = model.images([e['_raw'] for e in raw_entries]) if raw_entries else []
        if len(generated) != len(raw_entries):
            raise ValueError('image_embedding_count_mismatch')
        pixels = dict(zip((e['id'] for e in raw_entries), generated))
        vectors = [validate_vector(e['_vector'] if '_vector' in e else pixels[e['id']]) for e in batch]
        index.upsert(ids=[e['id'] for e in batch], embeddings=vectors,
                     metadatas=[{'chunk_key': e['id'], 'image_sha256': e['image_sha256'],
                                 'model': MODEL_ID, 'revision': MODEL_REVISION} for e in batch])
    query_vector = validate_vector(model.text(query))
    rows = index.query(query_embeddings=[query_vector], n_results=min(top_k, len(by_id)),
                       where={'chunk_key': {'$in': list(by_id)}}, include=['distances'])
    results = []
    for ident, distance in zip(rows['ids'][0], rows['distances'][0]):
        score = 1 - float(distance)
        if ident in by_id and math.isfinite(score) and score >= min_score:
            entry = {k: v for k, v in by_id[ident].items() if not k.startswith('_')}
            results.append({**entry, 'score': round(max(-1., min(1., score)), 4)})
    return results
