import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch
import httpx
from fastapi import HTTPException
from tests.test_hosting import Fixture
from memory.sync_gateway import durable_chat,resolve_conversation
from hosting.api import create_app
from hosting.repository import Conflict

class AliasTests(Fixture,unittest.TestCase):
    def setUp(self):self.setup_repo()
    def tearDown(self):self.teardown_repo()
    def test_alias_idempotency_and_subject_isolation(self):
        a=resolve_conversation(self.repo,'alice','old-uuid')
        self.assertEqual(a,resolve_conversation(self.repo,'alice','old-uuid'))
        self.assertNotEqual(a,resolve_conversation(self.repo,'bob','old-uuid'))
        with self.assertRaises(Conflict):resolve_conversation(self.repo,'bob',str(a))

class SyncGatewayTests(Fixture,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):self.setup_repo()
    async def asyncTearDown(self):self.teardown_repo()
    async def test_service_auth_is_mandatory_and_public_user_id_has_no_power(self):
        body=SimpleNamespace(conv_id='old-uuid',user_id='alice',message='hello',ads=[],ad_context=None)
        request=SimpleNamespace(headers={'Authorization':'Bearer wrong','X-Request-ID':'sync-key-123456789'})
        with patch.dict(os.environ,AGENT_SERVICE_TOKEN='secret'):
            with self.assertRaises(HTTPException) as error:await durable_chat(body,request,repo=self.repo)
            self.assertEqual(error.exception.status_code,401)
    async def test_legacy_alias_and_retry_use_one_hosted_session(self):
        calls=[]
        async def handler(request):
            calls.append((request.url.path,json.loads(request.content)))
            if request.url.path=='/auth/session':return httpx.Response(200,json={'session_token':'opaque'})
            return httpx.Response(202,json={'run_id':'1','conv_id':request.url.path.split('/')[2],'status':'queued'})
        body=SimpleNamespace(conv_id=None,user_id='alice',message='hello',ads=[],ad_context=None)
        request=SimpleNamespace(headers={'Authorization':'Bearer secret','X-Request-ID':'sync-key-123456789'})
        with patch.dict(os.environ,AGENT_SERVICE_TOKEN='secret'):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url='http://hosted') as client:
                first=await durable_chat(body,request,client,self.repo)
                second=await durable_chat(body,request,client,self.repo)
        self.assertEqual(first.status_code,202)
        self.assertEqual(json.loads(first.body)['conv_id'],json.loads(second.body)['conv_id'])
        targets=[path for path,data in calls if path.endswith('/chat')]
        self.assertEqual(targets[0],targets[1])
        self.assertNotIn('user_id',next(data for path,data in calls if path.endswith('/chat')))
