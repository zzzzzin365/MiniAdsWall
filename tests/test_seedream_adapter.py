import asyncio
import httpx
from unittest.mock import patch
from fastapi import HTTPException
from starlette.requests import Request
from api import studio


def test_seedream_request():
    import os
    os.environ['ARK_API_KEY'] = 'test-only'
    real_client = httpx.AsyncClient
    def respond(request):
        import json
        body = json.loads(request.content)
        assert request.url.path == '/api/v3/images/generations'
        assert body['model'] == 'doubao-seedream-5-0-flash-260915'
        assert body['size'] == '2K'
        assert body['response_format'] == 'b64_json'
        assert 'sequential_image_generation' not in body
        return httpx.Response(200, json={'data': [{'b64_json': 'fake-image'}]})
    with patch.object(studio.httpx, 'AsyncClient', lambda **kw: real_client(transport=httpx.MockTransport(respond), **kw)):
        result = asyncio.run(studio.image_generate(studio.ImageInput(prompt='牙刷广告图片'), Request({'type': 'http'})))
    assert result['b64_json'] == 'fake-image'


def test_seedream_missing_key():
    import os
    with patch.dict(os.environ, {'ARK_API_KEY': ''}):
        try:
            asyncio.run(studio.image_generate(studio.ImageInput(prompt='广告'), Request({'type': 'http'})))
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError('missing key accepted')

if __name__ == '__main__':
    test_seedream_request()
    test_seedream_missing_key()
    print('2 Seedream adapter checks passed')
