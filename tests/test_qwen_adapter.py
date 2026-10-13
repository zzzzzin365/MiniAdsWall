import json
import os
import unittest
from unittest.mock import patch
import httpx
from core.model_client import create_model_client, qwen_config, ModelResponseError


class QwenTests(unittest.IsolatedAsyncioTestCase):
    async def test_qwen_request_and_response(self):
        def handle(request):
            self.assertEqual(str(request.url), 'https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions')
            body = json.loads(request.content)
            self.assertFalse(body['enable_thinking'])
            self.assertNotIn('reasoning', body)
            self.assertEqual(body['messages'][0]['role'], 'system')
            return httpx.Response(200, json={'choices':[{'message':{'content':'建议'}, 'finish_reason':'stop'}], 'usage':{'total_tokens':8}})
        client = create_model_client('fake', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        client.client.transport = httpx.MockTransport(handle)
        result = await client.messages.create(model='qwen3.7-flash-2026-07-15', messages=[{'role':'user','content':'广告'}], system='运营', max_tokens=100)
        self.assertEqual(result.content[0].text, '建议')
        self.assertEqual(result.usage['total_tokens'], 8)

    async def test_truncated_output_rejected(self):
        client = create_model_client('fake', 'https://dashscope.aliyuncs.com/compatible-mode/v1')
        client.client.transport = httpx.MockTransport(lambda r: httpx.Response(200, json={'choices':[{'message':{'content':'partial'}, 'finish_reason':'length'}]}))
        with self.assertRaises(ModelResponseError):
            await client.messages.create(model='qwen',messages=[],max_tokens=1)

    def test_config_credential_gate(self):
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY':''}):
            self.assertIsNone(qwen_config())
        with patch.dict(os.environ, {'DASHSCOPE_API_KEY':'fake','QWEN_MODEL':'snapshot'}):
            self.assertEqual(qwen_config()['model'], 'snapshot')

    async def test_stream_uses_qwen_endpoint(self):
        from hosting.streaming import stream_text
        def handle(request):
            body=json.loads(request.content)
            self.assertTrue(body['stream'])
            self.assertFalse(body['enable_thinking'])
            self.assertNotIn('reasoning',body)
            self.assertIn('dashscope.aliyuncs.com',str(request.url))
            return httpx.Response(200,content=b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\ndata: [DONE]\n\n')
        client=create_model_client('fake','https://dashscope.aliyuncs.com/compatible-mode/v1')
        client.client.transport=httpx.MockTransport(handle)
        chunks=[x async for x in stream_text(client,model='qwen',messages=[],system='test')]
        self.assertEqual(chunks,['ok'])
