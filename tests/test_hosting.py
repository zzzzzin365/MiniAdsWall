import asyncio
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
from sqlalchemy import select, update, delete
import httpx
from hosting.repository import Repository, Conflict, LostLease
from hosting.objects import Objects
from hosting import schema as s
from hosting.worker import Worker

class Fixture:
    def setup_repo(self,**kwargs):
        self.temp=tempfile.TemporaryDirectory()
        self.repo=Repository('sqlite://',Objects(self.temp.name),'s'*32,test=True,**kwargs)
        self.repo.migrate()
        self.identity=self.repo.provision('alice'); self.user=self.identity['user_id']
        self.session=self.repo.create_session(self.user,self.identity['workspace_id'],'hello')
    def teardown_repo(self): self.repo.engine.dispose(); self.temp.cleanup()
    def new(self,message='hello',session=None,key=None):
        import uuid
        return self.repo.create_run(self.user,(session or self.session)['id'],key or str(uuid.uuid4()),{'message':message})

class RepositoryTests(Fixture,unittest.TestCase):
    def setUp(self): self.setup_repo()
    def tearDown(self): self.teardown_repo()
    def test_idempotency_and_request_mismatch(self):
        a=self.new(key='a'*20); b=self.new(key='a'*20); self.assertEqual(a['id'],b['id'])
        with self.assertRaises(Conflict): self.new('different',key='a'*20)
    def test_same_session_is_exclusive(self):
        self.new()
        with self.assertRaises(Conflict): self.new()
    def test_other_user_cannot_read_cancel_or_page(self):
        other=self.repo.provision('bob')['user_id']; r=self.new()
        for fn,args in [(self.repo.get_run,(other,r['id'])),(self.repo.cancel,(other,r['id'])),(self.repo.history,(other,self.session['id']))]:
            with self.assertRaises(Conflict) as error: fn(*args)
            self.assertEqual(error.exception.status,404)
    def test_cancel_wins_over_success_and_releases_session(self):
        a=self.new(); r=self.repo.claim('worker')
        self.repo.cancel(self.user,r['id'])
        result=self.repo.finish(r['id'],r['fence_token'],'succeeded','late reply')
        self.assertEqual(result['status'],'cancelled')
        self.new()
    def test_terminal_is_immutable(self):
        self.new(); r=self.repo.claim('worker'); self.repo.finish(r['id'],r['fence_token'],'succeeded','ok')
        self.assertEqual(self.repo.cancel(self.user,r['id'])['status'],'succeeded')
        with self.assertRaises(LostLease): self.repo.event(r['id'],r['fence_token'],'assistant.delta',{'text':'late'})
    def test_stale_worker_cannot_write(self):
        self.new(); r=self.repo.claim('worker')
        with self.repo.engine.begin() as c: c.execute(update(s.runs).where(s.runs.c.id==r['id']).values(lease_until=time.time()-1))
        self.repo.recover(r['id'],r['fence_token'])
        with self.assertRaises(LostLease): self.repo.finish(r['id'],r['fence_token'],'succeeded','late')
        self.assertEqual(self.repo.get_run(self.user,r['id'])['status'],'interrupted')
    def test_user_concurrency_is_global(self):
        for i in range(4):
            session=self.repo.create_session(self.user,self.identity['workspace_id'],str(i)); self.new(session=session)
        self.assertIsNotNone(self.repo.claim('one')); self.assertIsNotNone(self.repo.claim('two')); self.assertIsNone(self.repo.claim('three'))
    def test_queue_limit(self):
        self.repo.user_waiting=1; self.new()
        session=self.repo.create_session(self.user,self.identity['workspace_id'],'second')
        with self.assertRaises(Conflict) as error: self.new(session=session)
        self.assertEqual(error.exception.status,429)
    def test_history_keyset_with_tool_records_and_signed_cursor(self):
        self.new(); r=self.repo.claim('worker')
        tid=self.repo.tool_start(r['id'],r['fence_token'],'test_read',{},30)
        self.repo.tool_finish(r['id'],r['fence_token'],tid,{'output':'tool'})
        self.repo.finish(r['id'],r['fence_token'],'succeeded','answer')
        first=self.repo.history(self.user,self.session['id'],2)
        second=self.repo.history(self.user,self.session['id'],2,first['next_cursor'])
        self.assertEqual([v['seq'] for v in first['items']+second['items']],[3,2,1])
        with self.assertRaises(Conflict): self.repo.history(self.user,self.session['id'],2,first['next_cursor']+'x')
        with self.assertRaises(Conflict): self.repo.history(self.user,self.session['id'],2,first['next_cursor'],'message')
    def test_event_replay_and_expiration(self):
        a=self.new(); r=self.repo.claim('worker'); self.repo.event(r['id'],r['fence_token'],'assistant.delta',{'text':'hi'})
        _,rows=self.repo.read_events(self.user,r['id'],1); self.assertEqual([e['event_seq'] for e in rows],[2,3])
        with self.repo.engine.begin() as c: c.execute(delete(s.events).where(s.events.c.event_seq<3))
        with self.assertRaises(Conflict) as error: self.repo.read_events(self.user,r['id'],0)
        self.assertEqual(error.exception.status,410)
    def test_large_message_is_bounded_and_referenced(self):
        text='中'*4000; self.new(text)
        record=self.repo.history(self.user,self.session['id'])['items'][0]
        self.assertLessEqual(len(record['content_preview'].encode()),8192)
        self.assertEqual(self.repo.objects.get(record['content_ref']).decode(),text)
    def test_resume_keeps_old_run_and_checkpoint(self):
        self.new(); r=self.repo.claim('worker'); self.repo.checkpoint(r['id'],r['fence_token'],{'tools':{'read':'ok'}})
        self.repo.finish(r['id'],r['fence_token'],'interrupted')
        resumed=self.repo.create_run(self.user,self.session['id'],'resume-key-123456',{'message':'hello'},r['id'])
        self.assertNotEqual(resumed['id'],r['id']); self.assertIsNotNone(resumed['checkpoint_ref'])
        self.assertEqual(self.repo.get_run(self.user,r['id'])['status'],'interrupted')
    def test_pending_state_survives_adapter_restart(self):
        from hosting.pipeline import SQLActions
        first=SQLActions(self.repo); first._put_raw('user','session','{"value":1}')
        second=SQLActions(self.repo); self.assertEqual(second._pop_raw('user','session'),'{"value":1}'); self.assertIsNone(first._get_raw('user','session'))
    def test_approval_is_bound_and_single_use(self):
        import hashlib
        from hosting.repository import dumps
        self.new(); r=self.repo.claim('worker')
        self.repo.wait_approval(r['id'],r['fence_token'],{'action_name':'test','confirmation_id':'abc','args_hash':hashlib.sha256(dumps({'message':'hello'}).encode()).hexdigest(),'response':'confirm'})
        with self.repo.engine.connect() as c: aid=c.execute(select(s.approvals.c.id)).scalar_one()
        self.assertEqual(self.repo.approve(self.user,aid,True)['status'],'queued')
        self.assertEqual(self.repo.approve(self.user,aid,True)['status'],'queued')
        claimed=self.repo.claim('worker2'); self.assertEqual(claimed['deadline_at'],r['deadline_at'])
    def test_object_key_traversal_rejected(self):
        with self.assertRaises(ValueError): self.repo.objects.get('../secret')

class WorkerTests(Fixture,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.setup_repo()
    async def asyncTearDown(self): self.teardown_repo()
    async def test_stream_visible_before_completion(self):
        finish=asyncio.Event(); emitted=asyncio.Event()
        async def pipeline(ctx):
            await ctx.delta('first'); emitted.set(); await finish.wait(); await ctx.delta(' second'); return ctx.text
        self.new(); r=self.repo.claim('w'); worker=Worker(self.repo,pipeline)
        task=asyncio.create_task(worker.execute(r)); await emitted.wait(); await asyncio.sleep(.2)
        current,events=self.repo.read_events(self.user,r['id'],0)
        self.assertEqual(current['status'],'running'); self.assertTrue(any(e['type']=='assistant.delta' for e in events))
        finish.set(); await task
        self.assertEqual(self.repo.get_run(self.user,r['id'])['status'],'succeeded')
    async def test_cancel_stops_inflight_work(self):
        started=asyncio.Event(); stopped=asyncio.Event()
        async def pipeline(ctx):
            started.set()
            try: await asyncio.Event().wait()
            finally: stopped.set()
        self.new(); r=self.repo.claim('w'); worker=Worker(self.repo,pipeline)
        task=asyncio.create_task(worker.execute(r)); await started.wait(); self.repo.cancel(self.user,r['id'])
        await asyncio.wait_for(task,2); self.assertTrue(stopped.is_set())
        self.assertEqual(self.repo.get_run(self.user,r['id'])['status'],'cancelled')
    async def test_run_timeout_stops_work(self):
        self.repo.run_seconds=.1
        async def pipeline(ctx): await asyncio.sleep(10)
        self.new(); r=self.repo.claim('w'); await asyncio.wait_for(Worker(self.repo,pipeline).execute(r),2)
        self.assertEqual(self.repo.get_run(self.user,r['id'])['status'],'timed_out')
    async def test_tool_timeout_records_failure(self):
        async def pipeline(ctx):
            async def tool(): await asyncio.sleep(10)
            await ctx.tool('slow',{},tool,.05)
        self.new(); r=self.repo.claim('w'); await Worker(self.repo,pipeline).execute(r)
        history=self.repo.history(self.user,r['session_id'])['items']
        self.assertEqual(history[0]['status'],'timed_out')
        self.assertEqual(self.repo.get_run(self.user,r['id'])['status'],'timed_out')
    async def test_cleanup_failure_holds_capacity(self):
        class Broken:
            async def cleanup(self,rid): raise RuntimeError('cannot confirm death')
        async def pipeline(ctx): raise TimeoutError()
        self.new(); r=self.repo.claim('w'); await Worker(self.repo,pipeline,Broken()).execute(r)
        self.assertEqual(self.repo.get_run(self.user,r['id'])['status'],'stopping')
    async def test_failed_pipeline_cannot_report_success(self):
        async def pipeline(ctx): raise ValueError('bad')
        self.new(); r=self.repo.claim('w'); await Worker(self.repo,pipeline).execute(r)
        self.assertEqual(self.repo.get_run(self.user,r['id'])['status'],'failed')

class ProviderStreamTests(unittest.IsolatedAsyncioTestCase):
    async def test_native_delta_and_truncated_rejection(self):
        from core.model_client import OpenRouterClient,GovernedClient,ModelResponseError
        from hosting.streaming import stream_text
        async def handler(request):
            body=json.loads(request.content); self.assertTrue(body['stream'])
            return httpx.Response(200,content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\ndata: [DONE]\n\n')
        client=GovernedClient(OpenRouterClient('test',httpx.MockTransport(handler)))
        parts=[t async for t in stream_text(client,model='test',messages=[],system='test')]
        self.assertEqual(parts,['hi'])
        async def broken(request): return httpx.Response(200,content=b'data: {"choices":[{"delta":{"content":"hi"}}]}\n\n')
        client=GovernedClient(OpenRouterClient('test',httpx.MockTransport(broken)))
        with self.assertRaises(ModelResponseError):
            async for _ in stream_text(client,model='test',messages=[],system='test'): pass

if __name__=='__main__': unittest.main()

class PipelineIntegrationTests(Fixture,unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self): self.setup_repo()
    async def asyncTearDown(self): self.teardown_repo()
    async def test_ad_task_uses_real_preflight_tools_and_stream_adapter(self):
        from hosting.pipeline import Pipeline
        async def provider(*args,**kwargs):
            self.assertIn('ads_summary',kwargs['messages'][-1]['content'])
            yield '根据广告数据'; await asyncio.sleep(.15); yield '，建议优化素材。'
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':'test','HOSTING_RAG_ENABLED':'false'}), patch('hosting.pipeline.stream_text',provider):
            pipeline=Pipeline(self.repo)
            try:
                r=self.repo.create_run(self.user,self.session['id'],'pipeline-operation-1',{'message':'分析当前广告表现，给出三个优化动作','ads':[{'id':'1','price':2,'clicks':3,'title':'test'}]})
                r=self.repo.claim('w'); await Worker(self.repo,pipeline).execute(r)
                state=self.repo.get_run(self.user,r['id']); self.assertEqual(state['status'],'succeeded')
                history=self.repo.history(self.user,self.session['id'])['items']
                self.assertEqual(sum(i['record_type']=='tool_call' for i in history),2)
                self.assertEqual(history[0]['content_preview'],'根据广告数据，建议优化素材。')
                trace=json.loads(self.repo.objects.get(state['trace_ref'])); self.assertEqual(trace['tool_calls'],2)
                self.assertIsNotNone(trace['first_delta_ms'])
            finally: await pipeline.close()

class WorkspaceTests(Fixture,unittest.TestCase):
    def setUp(self): self.setup_repo()
    def tearDown(self): self.teardown_repo()
    def archive(self,name='test.txt',text=b'hello'):
        import io,tarfile
        buffer=io.BytesIO()
        with tarfile.open(fileobj=buffer,mode='w') as tar:
            entry=tarfile.TarInfo(name); entry.size=len(text); tar.addfile(entry,io.BytesIO(text))
        return buffer.getvalue()
    def test_snapshot_input_and_version_conflict(self):
        self.repo.workspace_snapshot(self.user,self.identity['workspace_id'],self.archive())
        self.new(); r=self.repo.claim('w'); self.assertIsNotNone(r['checkpoint_ref'])
        ref=self.repo.objects.put(self.archive('changed.txt',b'changed'))
        self.repo.checkpoint(r['id'],r['fence_token'],{'workspace_ref':ref})
        self.repo.finish(r['id'],r['fence_token'],'succeeded','ok')
        self.repo.workspace_snapshot(self.user,self.identity['workspace_id'],self.archive('other.txt'))
        with self.assertRaises(Conflict): self.repo.commit_workspace(self.user,r['id'])
    def test_snapshot_path_traversal(self):
        with self.assertRaises(Conflict): self.repo.workspace_snapshot(self.user,self.identity['workspace_id'],self.archive('../escape'))
    def test_server_snapshot_change_does_not_duplicate_a_retry(self):
        a=self.repo.create_run(self.user,self.session['id'],'trusted-snapshot-key',{'message':'hello','ads':[{'id':'1'}]})
        b=self.repo.create_run(self.user,self.session['id'],'trusted-snapshot-key',{'message':'hello','ads':[{'id':'2'}]})
        self.assertEqual(a['id'],b['id'])
        self.assertEqual(json.loads(self.repo.objects.get(b['input_ref']))['ads'][0]['id'],'1')
