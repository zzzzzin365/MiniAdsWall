import os
import unittest
from unittest.mock import patch
import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from api import main, video

class VideoTests(unittest.IsolatedAsyncioTestCase):
    async def request(self, handler, call):
        real=httpx.AsyncClient
        with patch.dict(os.environ,{'ARK_API_KEY':'fake'}), patch.object(video.httpx,'AsyncClient',side_effect=lambda **kw: real(transport=httpx.MockTransport(handler),**kw)):
            return await call()

    async def test_create_single_image(self):
        import json
        def handler(req):
            self.assertTrue(str(req.url).endswith('/contents/generations/tasks'))
            p=json.loads(req.content); self.assertEqual(p['model'],video.DEFAULT_MODEL)
            self.assertEqual(p['resolution'],'480p');self.assertEqual(p['content'][1]['role'],'first_frame')
            self.assertNotIn('generate_audio',p)
            return httpx.Response(200,json={'id':'task-one'})
        result=await self.request(handler,lambda:video.create_task(video.VideoInput(prompt='商品展示',image_url='https://example.com/product.png')))
        self.assertEqual(result['id'],'task-one')

    async def test_failed_and_success_results(self):
        for data in [{'id':'task','status':'failed','error':{'code':'BadImage'}},{'id':'task','status':'succeeded','content':{'video_url':'https://example.com/video.mp4'}}]:
            result=await self.request(lambda req:httpx.Response(200,json=data),lambda:video.get_task('task'))
            self.assertEqual(result,data)

    async def test_timeout_not_retried(self):
        calls=[]
        def handler(req):
            calls.append(req);raise httpx.ReadTimeout('timeout')
        with self.assertRaises(HTTPException) as caught:
            await self.request(handler,lambda:video.create_task(video.VideoInput(prompt='广告')))
        self.assertEqual(caught.exception.status_code,504);self.assertEqual(len(calls),1)

    def test_validation_and_service_auth(self):
        for fields in [{'duration':13},{'resolution':'4k'},{'prompt':'  '},{'prompt':'广告 --dur 12'},{'image_url':'file:///secret'}]:
            with self.assertRaises(ValidationError):video.VideoInput(**({'prompt':'广告'}|fields))
        with patch.dict(os.environ,{'AGENT_SERVICE_TOKEN':'fake'}):
            client=TestClient(main.app)
            self.assertEqual(client.post('/generate/video',json={'prompt':'广告'}).status_code,401)
            self.assertEqual(client.get('/generate/video/task').status_code,401)
