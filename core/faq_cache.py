"""Opt-in, reviewed FAQ-only semantic cache. Never caches arbitrary chat."""
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path

import httpx
from redis.asyncio import Redis
from core.model_gate import get_model_gate


def normalize(text):
    return text.strip().rstrip('？?。').strip()


def cosine(a, b):
    if not a or len(a) != len(b) or not all(math.isfinite(x) for x in a + b):
        return -1.
    denominator = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / denominator if denominator else -1.


class FAQCache:
    def __init__(self, redis, catalog, embed, threshold=.95, ttl=3600, version=''):
        if not 0 < threshold <= 1 or ttl < 1:
            raise ValueError('Invalid FAQ cache configuration')
        self.redis, self.catalog, self.embed = redis, catalog, embed
        self.threshold, self.ttl = threshold, ttl
        self.version = hashlib.sha256((json.dumps(catalog, sort_keys=True, ensure_ascii=False)+version).encode()).hexdigest()

    def eligible(self, message, conv_id=None, confirmation_id=None):
        if conv_id or confirmation_id:
            return None
        for item in self.catalog['items']:
            if normalize(message) in {normalize(q) for q in item['aliases']}:
                return item
        return None

    async def answer(self, item, message, scope, generate):
        key = 'faq:' + hashlib.sha256(f'{self.version}:{scope}:{item["id"]}'.encode()).hexdigest()
        vector = None
        try:
            vector = await self.embed(message)
            async with asyncio.timeout(1):
                records = await self.redis.hvals(key)
            best = None
            for raw in records:
                record = json.loads(raw)
                score = cosine(vector, record['vector'])
                if score >= self.threshold and (best is None or score > best[0]):
                    best = (score, record['answer'])
            if best:
                return best[1], True
        except Exception:
            # Cache/embedding failure is a miss, never an unavailable answer.
            vector = None
        answer = await generate(item)
        if not isinstance(answer, str) or not answer.strip():
            raise ValueError('Empty FAQ answer')
        if vector is not None:
            try:
                # Reviewed aliases bound hash cardinality. Atomic write + TTL.
                async with asyncio.timeout(1):
                    async with self.redis.pipeline(transaction=True) as pipe:
                        pipe.hset(key, normalize(message), json.dumps({'vector': vector, 'answer': answer}))
                        pipe.expire(key, self.ttl)
                        await pipe.execute()
            except Exception:
                pass
        return answer, False


async def embed_question(text):
    async with get_model_gate().slot(), asyncio.timeout(3):
        async with httpx.AsyncClient(timeout=3) as client:
            response = await client.post(os.environ['FAQ_EMBEDDING_URL'],
                headers={'Authorization': 'Bearer ' + os.environ['FAQ_EMBEDDING_API_KEY']},
                json={'model': os.environ['FAQ_EMBEDDING_MODEL'], 'input': text})
            response.raise_for_status()
            vector = response.json()['data'][0]['embedding']
            if not isinstance(vector, list) or not vector or len(vector) > 65536:
                raise ValueError('Invalid embedding')
            vector = [float(x) for x in vector]
            if not all(math.isfinite(x) for x in vector) or not any(vector):
                raise ValueError('Invalid embedding')
            return vector


def configured_cache():
    if os.getenv('FAQ_CACHE_ENABLED', 'false').lower() != 'true':
        return None
    for key in ['FAQ_EMBEDDING_URL', 'FAQ_EMBEDDING_API_KEY', 'FAQ_EMBEDDING_MODEL', 'REDIS_URL']:
        if not os.getenv(key):
            raise ValueError(f'{key} is required when FAQ_CACHE_ENABLED=true')
    path = Path(os.getenv('FAQ_CATALOG_PATH', str(Path(__file__).resolve().parent.parent/'config/cacheable_faq.json')))
    catalog = json.loads(path.read_text())
    redis = Redis.from_url(os.environ['REDIS_URL'], socket_connect_timeout=1, socket_timeout=1)
    version = 'faq-prompt-v1|' + '|'.join(os.getenv(k, '') for k in ['FAQ_KNOWLEDGE_VERSION', 'FAQ_EMBEDDING_URL', 'FAQ_EMBEDDING_MODEL', 'ANTHROPIC_MODEL', 'ANTHROPIC_BASE_URL'])
    return FAQCache(redis, catalog, embed_question, float(os.getenv('FAQ_CACHE_THRESHOLD', '.95')),
                    int(os.getenv('FAQ_CACHE_TTL_SECONDS', '3600')), version)
