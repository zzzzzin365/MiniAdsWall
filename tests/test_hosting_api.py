import asyncio
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch
import httpx
from redis.asyncio import Redis
from hosting.api import create_app
from hosting.repository import Repository
from hosting.objects import Objects

@unittest.skipUnless(shutil.which('redis-server'),'redis-server required for real Redis integration')
class HostingAPITests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(); cls.socket=str(Path(cls.tmp.name)/'redis.sock')
        cls.server=subprocess.Popen(['redis-server','--port','0','--unixsocket',cls.socket,'--save','','--appendonly','no'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        for _ in range(100):
            if Path(cls.socket).exists(): break
            time.sleep(.02)
    @classmethod
    def tearDownClass(cls):
        cls.server.terminate(); cls.server.wait(timeout=5); cls.tmp.cleanup()
    async def asyncSetUp(self):
        self.data=tempfile.TemporaryDirectory()
        self.repo=Repository('sqlite://',Objects(self.data.name),'k'*32,test=True); self.repo.migrate()
        self.redis=Redis(unix_socket_path=self.socket,decode_responses=True); await self.redis.flushdb()
        self.env=patch.dict(os.environ,{'AGENT_SERVICE_TOKEN':'test-service'}); self.env.start()
        self.app=create_app(self.repo,self.redis)
        self.http=httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app),base_url='http://test')
        self.headers={'Authorization':'Bearer test-service'}
        response=await self.http.post('/auth/session',json={'subject':'alice'},headers=self.headers)
        self.assertEqual(response.status_code,200)
        self.identity=response.json(); self.headers['X-Agent-Session']=self.identity['session_token']
    async def asyncTearDown(self):
        await self.http.aclose(); await self.redis.aclose(); self.repo.engine.dispose(); self.env.stop(); self.data.cleanup()
    async def make_run(self):
        session=(await self.http.post('/sessions',headers=self.headers,json={'workspace_id':self.identity['workspace_id'],'title':'demo'})).json()
        response=await self.http.post(f"/sessions/{session['id']}/runs",headers={**self.headers,'Idempotency-Key':'test-operation-12345'},json={'message':'hello'})
        self.assertEqual(response.status_code,202,response.text)
        return session,response.json()
    async def test_legacy_ads_quantity_boundary_baseline(self):
        session=(await self.http.post('/sessions',headers=self.headers,json={'workspace_id':self.identity['workspace_id']})).json()
        url=f"/sessions/{session['id']}/runs"
        ads=[{'id':str(i)} for i in range(1001)]
        accepted=await self.http.post(url,headers={**self.headers,'Idempotency-Key':'baseline-1000-ads-1234'},json={'message':'baseline','ads':ads[:1000]})
        self.assertEqual(accepted.status_code,202,accepted.text)
        persisted=json.loads(self.repo.objects.get(self.repo.get_run(self.identity['user_id'],accepted.json()['id'])['input_ref']))
        self.assertEqual(len(persisted['ads']),1000)
        rejected=await self.http.post(url,headers={**self.headers,'Idempotency-Key':'baseline-1001-ads-1234'},json={'message':'baseline','ads':ads})
        self.assertEqual(rejected.status_code,422,rejected.text)
        self.assertTrue(any(item['loc'][-1]=='ads' and item['type']=='too_long' for item in rejected.json()['detail']))
        from hosting import schema as s
        from sqlalchemy import select,func
        with self.repo.engine.connect() as c:
            self.assertEqual(c.execute(select(func.count()).select_from(s.runs)).scalar_one(),1)

    async def test_auth_and_logout(self):
        self.assertEqual((await self.http.get('/sessions')).status_code,401)
        await self.http.delete('/auth/session',headers=self.headers)
        self.assertEqual((await self.http.get('/sessions',headers=self.headers)).status_code,401)
    async def test_protocol_guard_and_upgraded_health(self):
        self.assertEqual((await self.http.get('/health')).status_code,200)
        session=(await self.http.post('/sessions',headers=self.headers,json={'workspace_id':self.identity['workspace_id']})).json()
        for payload in [{'message':'test','input_protocol_version':2},{'message':'test','ad_context':{'version':1}}]:
            response=await self.http.post(f"/sessions/{session['id']}/runs",headers={**self.headers,'Idempotency-Key':'unknown-protocol-12345'},json=payload)
            self.assertEqual(response.status_code,422,response.text)
    async def test_create_cancel_history_and_sse_replay(self):
        session,run=await self.make_run()
        self.assertIsInstance(run['id'],str)
        cancel=await self.http.post(f"/runs/{run['id']}/cancel",headers=self.headers)
        self.assertEqual(cancel.json()['status'],'cancelled')
        response=await self.http.get(f"/runs/{run['id']}/events",headers={**self.headers,'Last-Event-ID':'1'})
        self.assertEqual(response.status_code,200); self.assertIn('event: run.finished',response.text)
        self.assertNotIn('event: run.queued',response.text)
        history=(await self.http.get(f"/sessions/{session['id']}/history",headers=self.headers)).json()
        self.assertEqual(history['items'][0]['content_preview'],'hello')
    async def test_foreign_principal_and_disabled_account(self):
        _,run=await self.make_run()
        bob=(await self.http.post('/auth/session',headers=self.headers,json={'subject':'bob'})).json()
        response=await self.http.get(f"/runs/{run['id']}",headers={**self.headers,'X-Agent-Session':bob['session_token']})
        self.assertEqual(response.status_code,404)
        from hosting import schema as s
        from sqlalchemy import update
        with self.repo.engine.begin() as c: c.execute(update(s.users).where(s.users.c.id==int(self.identity['user_id'])).values(status='disabled'))
        self.assertEqual((await self.http.get('/sessions',headers=self.headers)).status_code,403)
    async def test_redis_loss_does_not_erase_history(self):
        session,run=await self.make_run(); await self.redis.flushdb()
        self.assertEqual((await self.http.get(f"/runs/{run['id']}",headers=self.headers)).status_code,401)
        login=(await self.http.post('/auth/session',json={'subject':'alice'},headers=self.headers)).json()
        self.headers['X-Agent-Session']=login['session_token']
        self.assertEqual((await self.http.get(f"/sessions/{session['id']}/history",headers=self.headers)).json()['items'][0]['content_preview'],'hello')
    async def test_rate_limit_and_sse_slots(self):
        auth=self.app.state.auth
        for _ in range(10): await auth.limit(self.identity['user_id'])
        from hosting.repository import Conflict
        with self.assertRaises(Conflict): await auth.limit(self.identity['user_id'])
        for i in range(5): await auth.connect(self.identity['user_id'],str(i))
        with self.assertRaises(Conflict): await auth.connect(self.identity['user_id'],'six')
        await auth.disconnect(self.identity['user_id'],'0'); await auth.connect(self.identity['user_id'],'six')
