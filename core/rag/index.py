"""Immutable text generations with an atomic SQLite publication pointer.

The authoritative content lives here; Chroma holds disposable vectors. Only the
single server-owned public knowledge pool is supported in this first release.
"""
import hashlib
import json
import math
import re
import sqlite3
import threading
import unicodedata
from collections import Counter
from contextlib import contextmanager
from pathlib import Path

CHUNKER = 'paragraph-window-v1'
TOKENIZER = 'unicode-han-bigram-v1'
SCOPE = 'public'


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def tokenize(text):
    """Fixed dependency-free tokenizer: exact ASCII identifiers + Han bigrams."""
    text = unicodedata.normalize('NFKC', text).lower()
    result = re.findall(r'[a-z0-9]+(?:[._:/-][a-z0-9]+)*', text)
    for run in re.findall(r'[\u3400-\u9fff]+', text):
        result.extend(run if len(run) == 1 else [run[i:i+2] for i in range(len(run)-1)])
    return result


def windows(text, limit, overlap=0):
    """Exact source offsets, bounded even for a single very long sentence."""
    if limit < 1 or not 0 <= overlap < limit:
        raise ValueError('invalid_window')
    start = 0
    while start < len(text):
        end = min(len(text), start + limit)
        if end < len(text):
            cuts = [text.rfind(c, start + limit // 2, end) for c in ('\n', '。', '！', '？', '. ')]
            cut = max(cuts)
            if cut >= start + limit // 2:
                end = cut + 1
        yield start, end
        if end == len(text):
            break
        start = max(start + 1, end - overlap)


def parent_windows(text):
    headings = list(re.finditer(r'^([#]{1,6})[ \t]+(.+)$', text, re.MULTILINE))
    sections = []
    if not headings or headings[0].start() > 0:
        sections.append((0, headings[0].start() if headings else len(text), ''))
    path = []
    for i, heading in enumerate(headings):
        level = len(heading.group(1))
        path = [(lev, title) for lev, title in path if lev < level] + [(level, heading.group(2).strip())]
        sections.append((heading.start(), headings[i+1].start() if i+1<len(headings) else len(text),
                         ' / '.join(title for _, title in path)))
    for start, end, section in sections:
        for left, right in windows(text[start:end], 1600):
            yield start+left, start+right, section


def split_document(doc):
    if (not isinstance(doc, dict) or doc.get('scope_id', SCOPE) != SCOPE or not isinstance(doc.get('content'), str)
            or not isinstance(doc.get('title', ''), str)
            or not isinstance(doc.get('source_url', ''), str)
            or not isinstance(doc.get('source_title', ''), str)
            or ('doc_id' in doc and (not isinstance(doc['doc_id'], str) or not doc['doc_id']))
            or len(doc.get('title', '')) > 200 or len(doc.get('source_url', '')) > 2048
            or len(doc.get('source_title', '')) > 200
            or len(doc.get('doc_id', '')) > 128
            or len(doc['content']) > 1000000):
        raise ValueError('invalid_document')
    text = unicodedata.normalize('NFC', doc['content'].replace('\r\n', '\n').replace('\r', '\n'))
    if not text.strip():
        raise ValueError('empty_document')
    title = doc.get('title', '')
    source = doc.get('source_url', '')
    doc_id = doc.get('doc_id') or digest([SCOPE, doc.get('knowledge_id') or [source, title]])
    revision = digest([text, title, source, doc.get('source_title', '')])
    chunks = []
    for ps, pe, section in parent_windows(text):
        parent_id = digest([doc_id, revision, CHUNKER, ps, pe])
        for cs, ce in windows(text[ps:pe], 400, 80):
            start, end = ps + cs, ps + ce
            chunks.append({'id': digest([doc_id, revision, CHUNKER, start, end, text[start:end]]),
                'doc_id': doc_id, 'revision': revision, 'title': title, 'source_url': source,
                'source_title': doc.get('source_title', ''), 'content': text[start:end],
                'start_offset': start, 'end_offset': end, 'parent_id': parent_id,
                'parent_start': ps, 'parent_end': pe, 'parent_text': text[ps:pe],
                'scope_id': SCOPE, 'chunker_version': CHUNKER, 'section_path': title + (' / ' + section if section else '')})
    return {'doc_id': doc_id, 'revision': revision, 'title': title, 'source_url': source,
            'source_title': doc.get('source_title', ''), 'content': text}, chunks


class TextIndex:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = str(path)
        self._lock = threading.RLock()
        with self.connect() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS generations(id TEXT PRIMARY KEY, collection_name TEXT NOT NULL,
                    manifest TEXT NOT NULL, state TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS documents(generation TEXT, doc_id TEXT, body TEXT NOT NULL,
                    PRIMARY KEY(generation,doc_id));
                CREATE TABLE IF NOT EXISTS chunks(generation TEXT, id TEXT, doc_id TEXT, body TEXT NOT NULL,
                    PRIMARY KEY(generation,id));
                CREATE INDEX IF NOT EXISTS chunks_doc ON chunks(generation,doc_id);
                CREATE TABLE IF NOT EXISTS active(singleton INTEGER PRIMARY KEY CHECK(singleton=1), generation TEXT);
                CREATE TABLE IF NOT EXISTS denied(doc_id TEXT PRIMARY KEY);
            ''')

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def active(self):
        with self.connect() as db:
            row = db.execute('SELECT generation FROM active WHERE singleton=1').fetchone()
            return row[0] if row else None

    def manifest(self, generation):
        with self.connect() as db:
            row = db.execute('SELECT * FROM generations WHERE id=?', (generation,)).fetchone()
            if not row or row['state'] != 'ready':
                raise RuntimeError('generation_not_ready')
            return {**json.loads(row['manifest']), 'collection_name': row['collection_name']}

    def stage(self, documents, embedding_version):
        if not isinstance(documents, list) or not 1 <= len(documents) <= 1000:
            raise ValueError('invalid_document_batch')
        parsed = [split_document(doc) for doc in documents]
        if sum(len(chunks) for _, chunks in parsed) > 10000:
            raise ValueError('rag_v2_capacity_limit_10000_chunks')
        ids = [d['doc_id'] for d, _ in parsed]
        if not ids or len(set(ids)) != len(ids):
            raise ValueError('empty_or_duplicate_documents')
        manifest = {'documents': sorted((d['doc_id'], d['revision']) for d, _ in parsed),
                    'embedding_version': embedding_version, 'chunker': CHUNKER, 'tokenizer': TOKENIZER}
        generation = digest(manifest)
        collection = 'rag_v2_' + generation[:40]
        with self._lock, self.connect() as db:
            old = db.execute('SELECT state FROM generations WHERE id=?', (generation,)).fetchone()
            if not old:
                db.execute('INSERT INTO generations VALUES(?,?,?,?)',
                           (generation, collection, json.dumps(manifest), 'building'))
                for doc, chunks in parsed:
                    db.execute('INSERT INTO documents VALUES(?,?,?)', (generation, doc['doc_id'], json.dumps(doc)))
                    db.executemany('INSERT INTO chunks VALUES(?,?,?,?)',
                        [(generation, c['id'], c['doc_id'], json.dumps(c)) for c in chunks])
        return generation, collection, [c for _, chunks in parsed for c in chunks]

    def publish(self, generation, vector_ids):
        """Caller must finish vector writes and exact ID verification first."""
        with self._lock, self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            expected = {r[0] for r in db.execute('SELECT id FROM chunks WHERE generation=?', (generation,))}
            if not expected or expected != set(vector_ids):
                raise ValueError('vector_manifest_mismatch')
            db.execute("UPDATE generations SET state='ready' WHERE id=?", (generation,))
            db.execute('INSERT INTO active VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation',
                       (generation,))

    def activate(self, generation):
        self.manifest(generation)
        with self.connect() as db:
            db.execute('INSERT INTO active VALUES(1,?) ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation',
                       (generation,))

    def deny(self, doc_id):
        with self.connect() as db:
            db.execute('INSERT OR IGNORE INTO denied VALUES(?)', (doc_id,))

    def allowed(self, doc_id):
        with self.connect() as db:
            return not db.execute('SELECT 1 FROM denied WHERE doc_id=?', (doc_id,)).fetchone()

    def documents(self, generation):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('SELECT body FROM documents WHERE generation=? ORDER BY doc_id', (generation,))]

    def chunks(self, generation):
        self.manifest(generation)
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute('''SELECT c.body FROM chunks c
                LEFT JOIN denied d ON c.doc_id=d.doc_id WHERE c.generation=? AND d.doc_id IS NULL ORDER BY c.id''', (generation,))]

    def select(self, generation, ids):
        wanted = set(ids)
        return {c['id']: c for c in self.chunks(generation) if c['id'] in wanted}

    def keyword(self, generation, query, k):
        rows = self.chunks(generation)
        if not rows:
            return []
        tokens = [Counter(tokenize(c['title'] + '\n' + c['content'])) for c in rows]
        terms = set(tokenize(query))
        avg = sum(sum(t.values()) for t in tokens) / len(tokens) or 1
        frequency = {term: sum(term in t for t in tokens) for term in terms}
        scores = []
        for row, counts in zip(rows, tokens):
            length = sum(counts.values())
            score = 0.
            for term in terms:
                tf = counts[term]
                idf = math.log(1 + (len(rows) - frequency[term] + .5) / (frequency[term] + .5))
                score += idf * tf * 2.5 / (tf + 1.5 * (.25 + .75 * length / avg)) if tf else 0
            if score:
                scores.append({**row, 'keyword_score': score})
        return sorted(scores, key=lambda c: (-c['keyword_score'], c['id']))[:k]


def fuse(rankings, k=30):
    scores, rows = {}, {}
    for ranking in rankings:
        seen = set()
        rank = 0
        for row in ranking:
            key = row['id']
            if key in seen:
                continue
            seen.add(key)
            rank += 1
            scores[key] = scores.get(key, 0) + 1 / (60 + rank)
            rows[key] = row
    return [{**rows[key], 'fusion_score': scores[key]} for key in
            sorted(scores, key=lambda key: (-scores[key], key))[:k]]
