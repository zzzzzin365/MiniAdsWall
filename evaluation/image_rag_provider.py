"""Receipted provider calls for the explicitly authorized image RAG experiment.

One attempt per call key. An uncertain submit never retries automatically.
Only response metadata and structured model output are persisted, not keys or
image data URLs. The request hash includes every actual image byte.
"""
import hashlib
import json
import os
from pathlib import Path
import time

import httpx
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
VISION_MODEL = 'doubao-seed-2-0-mini-260428'
TEXT_MODEL = 'deepseek-v4-1-flash-260910'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def write(path, value):
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2))
    temp.replace(path)


class Provider:
    def __init__(self, out, max_calls=160):
        self.out = Path(out)
        self.receipts = self.out / 'receipts'
        self.receipts.mkdir(exist_ok=True)
        env = dotenv_values(ROOT / '.env')
        self.key = os.getenv('ARK_API_KEY') or env.get('ARK_API_KEY')
        if not self.key:
            raise RuntimeError('ARK_API_KEY unavailable')
        self.url = (os.getenv('ARK_BASE_URL') or env.get('ARK_BASE_URL') or 'https://ark.cn-beijing.volces.com/api/v3').rstrip('/') + '/chat/completions'
        self.http = httpx.Client(timeout=90)
        self.max_calls = max_calls

    def call(self, call_key, model, messages, max_tokens=1500):
        payload = {'model': model, 'messages': messages, 'temperature': 0,
                   'max_tokens': max_tokens, 'thinking': {'type': 'disabled'},
                   'response_format': {'type': 'json_object'}}
        payload_hash = digest(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode())
        stem = digest(call_key.encode())[:24]
        receipt_path = self.receipts / (stem + '.json')
        response_path = self.receipts / (stem + '.response.json')
        if receipt_path.exists():
            previous = json.loads(receipt_path.read_text())
            if previous['input_sha256'] != payload_hash:
                raise RuntimeError('Receipt payload changed; use a different experiment')
            if previous['status'] == 'succeeded':
                return json.loads(response_path.read_text()), previous, True
            raise RuntimeError('Existing unsuccessful/uncertain receipt; do not blindly resubmit')
        if len([p for p in self.receipts.glob('*.json') if not p.name.endswith('.response.json')]) >= self.max_calls:
            raise RuntimeError('Experiment call budget reached')
        receipt = {'call_key': call_key, 'model': model, 'request_id': stem,
                   'input_sha256': payload_hash, 'status': 'submitting', 'money': None}
        with receipt_path.open('x') as stream:
            json.dump(receipt, stream)
        start = time.perf_counter()
        try:
            response = self.http.post(self.url, json=payload, headers={
                'Authorization': 'Bearer ' + self.key, 'X-Request-ID': stem})
            receipt['elapsed_ms'] = (time.perf_counter() - start) * 1000
            receipt['http_status'] = response.status_code
            data = response.json()
            if not response.is_success:
                receipt['provider_error_code'] = data.get('error', {}).get('code')
                raise RuntimeError('provider_response_error')
            receipt.update(provider_id=data.get('id'), usage=data.get('usage'))
            choice = data['choices'][0]
            if choice.get('finish_reason') == 'length':
                raise RuntimeError('model_output_truncated')
            parsed = json.loads(choice['message']['content'].strip())
            if not isinstance(parsed, dict):
                raise RuntimeError('model_output_not_object')
            write(response_path, parsed)
            receipt['status'] = 'succeeded'
            write(receipt_path, receipt)
            return parsed, receipt, False
        except Exception as error:
            receipt.update(status='failed_or_submission_unknown', error_type=type(error).__name__,
                           elapsed_ms=(time.perf_counter() - start) * 1000)
            write(receipt_path, receipt)
            raise RuntimeError(f'Provider call failed; inspect receipt {receipt_path.name}') from None

    def close(self):
        self.http.close()
