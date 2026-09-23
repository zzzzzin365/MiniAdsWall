import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi import HTTPException
from fastapi.testclient import TestClient
from api import main, generation


class GenerationBoundaryTests(unittest.TestCase):
    def test_service_auth_and_request_id(self):
        # No lifespan: these tests exercise HTTP boundaries, not external infrastructure.
        client = TestClient(main.app)
        with patch.dict(os.environ, {"AGENT_SERVICE_TOKEN": "service-test"}):
            self.assertEqual(client.get('/health').status_code, 503)  # no lifespan, but not an auth rejection
            self.assertEqual(client.post('/generate/creative', json={}).status_code, 401)
            response = client.post('/generate/creative', json={}, headers={
                'Authorization': 'Bearer service-test', 'X-Request-ID': 'request-test'
            })
            self.assertEqual(response.status_code, 422)
            self.assertEqual(response.headers['x-request-id'], 'request-test')
        with patch.dict(os.environ, {"AGENT_SERVICE_TOKEN": ""}):
            self.assertEqual(client.post('/chat', json={}).status_code, 401)


class GenerationModelTests(unittest.IsolatedAsyncioTestCase):
    async def invoke(self, payload=None, error=None):
        client = AsyncMock()
        if error:
            client.post.side_effect = error
        else:
            client.post.return_value = httpx.Response(200, request=httpx.Request('POST', 'https://model.test'), json={
                'choices': [{'message': {'content': payload}}]
            })
        manager = AsyncMock()
        manager.__aenter__.return_value = client
        with patch.dict(os.environ, {'OPENROUTER_API_KEY': 'test'}), patch.object(generation.httpx, 'AsyncClient', return_value=manager):
            return await generation.generate('creative', generation.GenerationInput(adDescription='shoe', industry='retail'), 'request-id')

    async def test_valid_creative(self):
        result = await self.invoke('{"titles":["a"],"texts":["b"],"scripts":["c"],"keywords":["d"]}')
        self.assertEqual(result.titles, ['a'])

    async def test_malformed_model_output(self):
        for payload in ['not json', '{"titles":"wrong type"}', '{"titles":[],"texts":[],"scripts":[],"keywords":[]}']:
            with self.assertRaises(HTTPException) as caught:
                await self.invoke(payload)
            self.assertEqual(caught.exception.status_code, 502)

    async def test_timeout(self):
        with self.assertRaises(HTTPException) as caught:
            await self.invoke(error=httpx.ReadTimeout('timeout'))
        self.assertEqual(caught.exception.status_code, 504)
