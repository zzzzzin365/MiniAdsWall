#!/usr/bin/env python3
"""Explicit offline text-index build/publish/rollback. Never changes legacy vectors."""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['build', 'rollback', 'deny', 'status', 'export', 'export-defaults'])
    parser.add_argument('--documents', help='JSON array of complete source documents, not old chunks')
    parser.add_argument('--generation')
    parser.add_argument('--doc-id')
    parser.add_argument('--output')
    args = parser.parse_args()
    from core.rag.index import TextIndex
    path = os.getenv('RAG_V2_STORE', os.path.join(os.getenv('CHROMA_PERSIST_DIRECTORY','data/chroma'), 'rag-text-v2.sqlite3'))
    index = TextIndex(path)
    if args.action == 'export-defaults':
        if not args.output:
            parser.error('--output required')
        target = Path(args.output)
        if target.exists():
            raise FileExistsError('refusing_to_overwrite_source_documents')
        from mcp.knowledge_base import KnowledgeBase
        target.write_text(json.dumps(KnowledgeBase.__new__(KnowledgeBase)._default_docs(), ensure_ascii=False, indent=2))
    elif args.action == 'status':
        generation = index.active()
        print(json.dumps({'generation_id': generation, 'manifest': index.manifest(generation) if generation else None}, ensure_ascii=False))
    elif args.action == 'rollback':
        if not args.generation:
            parser.error('--generation required')
        index.activate(args.generation)
        print(json.dumps({'generation_id': index.active()}))
    elif args.action == 'deny':
        if not args.doc_id:
            parser.error('--doc-id required')
        index.deny(args.doc_id)
        print(json.dumps({'denied': args.doc_id}))
    elif args.action == 'export':
        if not args.output or not (args.generation or index.active()):
            parser.error('--output and a published generation required')
        target = Path(args.output)
        if target.exists():
            raise FileExistsError('refusing_to_overwrite_source_documents')
        target.write_text(json.dumps(index.documents(args.generation or index.active()), ensure_ascii=False, indent=2))
    else:
        if not args.documents:
            parser.error('--documents required; original document boundaries must be explicit')
        docs = json.loads(Path(args.documents).read_text())
        # The builder must work before a generation exists even if runtime v2 is enabled.
        os.environ['RAG_V2_ENABLED'] = 'false'
        from mcp.knowledge_base import KnowledgeBase
        kb = KnowledgeBase(chroma_host=os.getenv('CHROMA_HOST', 'localhost'),
            chroma_port=int(os.getenv('CHROMA_PORT', '8001')),
            chroma_path=os.getenv('CHROMA_PERSIST_DIRECTORY','data/chroma'))
        print(json.dumps(kb.retrieval_service.build(docs), ensure_ascii=False))


if __name__ == '__main__':
    main()
