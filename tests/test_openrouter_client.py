import asyncio
import json
import unittest

import httpx
from core.model_client import OpenRouterClient, ModelResponseError, client_options


class OpenRouterClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_endpoint_and_valid_text(self):
        async def handler(request):
            self.assertEqual(str(request.url), 'https://openrouter.ai/api/v1/chat/completions')
            self.assertEqual(request.headers['Authorization'], 'Bearer test-key')
            body = json.loads(request.content)
            self.assertEqual(body['messages'][0], {'role': 'system', 'content': 'rules'})
            self.assertFalse(body['reasoning']['enabled'])
            return httpx.Response(200, json={'choices': [{'finish_reason': 'stop', 'message': {'content': 'ok'}}]})
        client = OpenRouterClient('test-key', httpx.MockTransport(handler))
        result = await client.messages.create(model='test:free', max_tokens=10, system='rules',
                                               messages=[{'role': 'user', 'content': 'hello'}])
        self.assertEqual(result.content[0].text, 'ok')

    async def test_invalid_success_bodies_fail_without_retries(self):
        for body in [{'error': {'message': 'busy'}}, {'choices': []},
                     {'choices': [{'message': {'content': None}}]},
                     {'choices': [{'finish_reason': 'length', 'message': {'content': 'partial'}}]}]:
            calls = []
            async def handler(request):
                calls.append(request)
                return httpx.Response(200, json=body)
            client = OpenRouterClient('test', httpx.MockTransport(handler))
            with self.assertRaises(ModelResponseError):
                await client.create(model='test:free', messages=[], max_tokens=10)
            self.assertEqual(len(calls), 1)

    async def test_timeout_cancels_inflight_request(self):
        stopped = asyncio.Event()
        async def handler(request):
            try:
                await asyncio.sleep(20)
            finally:
                stopped.set()
        client = OpenRouterClient('test', httpx.MockTransport(handler))
        with self.assertRaises(TimeoutError):
            await client.create(model='test:free', messages=[], max_tokens=10, timeout=0.02)
        self.assertTrue(stopped.is_set())

    def test_native_sdk_has_bounded_timeout_and_no_retries(self):
        options = client_options('key')
        self.assertEqual(options['timeout'], 18)
        self.assertEqual(options['max_retries'], 0)
        self.assertNotIn('default_headers', options)
