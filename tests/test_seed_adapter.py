import json
import os
import unittest
from unittest.mock import AsyncMock, patch
import httpx
from core.model_client import create_model_client, business_model_config

class SeedTests(unittest.IsolatedAsyncioTestCase):
    def test_seed_priority_and_missing_key(self):
        with patch.dict(os.environ, {'ARK_API_KEY':'fake','DASHSCOPE_API_KEY':'qwen','LLM_PROVIDER':''}):
            self.assertEqual(business_model_config()['provider'],'seed')
        with patch.dict(os.environ, {'ARK_API_KEY':'','LLM_PROVIDER':'seed'}):
            with self.assertRaises(RuntimeError): business_model_config()

    async def test_chat_and_stream(self):
        from hosting.streaming import stream_text
        def handle(request):
            body=json.loads(request.content)
            self.assertEqual(str(request.url),'https://ark.cn-beijing.volces.com/api/v3/chat/completions')
            self.assertEqual(body['thinking'],{'type':'disabled'})
            self.assertNotIn('enable_thinking',body)
            self.assertNotIn('reasoning',body)
            if body.get('stream'):
                return httpx.Response(200,content=b'data: {"choices":[{"delta":{"content":"OK"}}]}\n\ndata: [DONE]\n\n')
            return httpx.Response(200,json={'choices':[{'message':{'content':'OK'},'finish_reason':'stop'}]})
        client=create_model_client('fake','https://ark.cn-beijing.volces.com/api/v3')
        client.client.transport=httpx.MockTransport(handle)
        response=await client.messages.create(model='seed',messages=[],max_tokens=32)
        self.assertEqual(response.content[0].text,'OK')
        self.assertEqual([x async for x in stream_text(client,model='seed',messages=[],system='test')],['OK'])

    async def test_generation_routes_to_seed(self):
        from api import generation
        client=AsyncMock()
        client.post.return_value=httpx.Response(200,request=httpx.Request('POST','https://model.test'),json={'choices':[{'message':{'content':'{"titles":["a"],"texts":["b"],"scripts":["c"],"keywords":["d"]}'}}]})
        manager=AsyncMock(); manager.__aenter__.return_value=client
        with patch.dict(os.environ,{'ARK_API_KEY':'fake','LLM_PROVIDER':'seed'}), patch.object(generation.httpx,'AsyncClient',return_value=manager):
            result=await generation.generate('creative',generation.GenerationInput(adDescription='shoe',industry='retail'),'id')
        self.assertEqual(result.titles,['a'])
        self.assertIn('volces.com',client.post.call_args.args[0])
        self.assertEqual(client.post.call_args.kwargs['json']['thinking'],{'type':'disabled'})
