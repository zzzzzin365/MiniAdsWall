"""Idempotently index curated summaries and visually checked image captions."""
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import load_dotenv
from mcp.external_marketing import open_index, sync

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--catalog', default=str(ROOT / 'data/external-marketing/catalog.json'))
    parser.add_argument('--receipt', default=str(ROOT / 'docs/knowledge/external-marketing-import.json'))
    args = parser.parse_args()
    load_dotenv(ROOT / '.env')
    index = open_index()
    before = index.count()
    result = sync(index=index, catalog=args.catalog)
    receipt = {**result, 'synthetic': False, 'collection': index.name, 'before': before,
               'after': index.count(), 'verification': 'real Chroma upsert/count; no paid model request'}
    path = Path(args.receipt)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(receipt, ensure_ascii=False))
