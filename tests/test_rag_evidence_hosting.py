import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from hosting.objects import Objects
from hosting.pipeline import Pipeline
from hosting.repository import Repository, LostLease
from hosting.worker import Worker
from mcp.tool_manager import MCPToolManager, ToolResult


class HostedEvidenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.repo = Repository("sqlite://", Objects(self.folder.name), "s" * 32, test=True)
        self.repo.migrate()
        identity = self.repo.provision("alice")
        self.user = identity["user_id"]
        self.session = self.repo.create_session(self.user, identity["workspace_id"], "test")
        self.env = patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test", "HOSTING_RAG_ENABLED": "false",
                                          "RAG_EVIDENCE_ENABLED": "true", "RAG_EVIDENCE_THRESHOLD": ".7",
                                          "RAG_EVIDENCE_TIMEOUT_SECONDS": "8"})
        self.env.start()
        self.pipeline = Pipeline(self.repo)
        self.pipeline.kb = SimpleNamespace(search=lambda *args: [
            {"id": "a", "title": "rule", "content": "有用规则"},
            {"id": "b", "title": "noise", "content": "不相关干扰"}])
        self.pipeline.rag_manager = MCPToolManager(api_key="test", model="test")
        self.pipeline.rag_manager.rewrite_query = AsyncMock(side_effect=lambda query, n=3: [query, "素材投放规则"])
        self.pipeline.rag_manager._rerank = AsyncMock(side_effect=lambda query, items, k: items[:k])
        self.pipeline.rag_manager.call = AsyncMock(return_value=ToolResult(
            True, self.pipeline.kb.search(), "knowledge_search"))

    async def asyncTearDown(self):
        await self.pipeline.close()
        self.env.stop()
        self.repo.engine.dispose()
        self.folder.cleanup()

    def new_run(self):
        self.repo.create_run(self.user, self.session["id"], "rag-hosted-test-1234", {
            "message": "分析当前广告表现，给出三个优化动作", "ads": [{"id": "1", "price": 2, "clicks": 3}]})
        return self.repo.claim("test-worker")

    async def execute(self, judgment, provider):
        async def model(**kwargs):
            state = json.loads(kwargs["messages"][0]["content"])
            if "chunks" in state:
                value = {"scores": [{"id": "a", "support": .9}, {"id": "b", "support": .1}]}
            else:
                expected = ["a"] if self.pipeline.rag_manager.call.return_value.success else []
                self.assertEqual([item["id"] for item in state["retained_chunks"]], expected)
                value = judgment
            return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(value))])
        patches = []
        for pool in self.pipeline.orchestrator._pool.values():
            for agent in pool:
                patches.append(patch.object(agent._client, "create", AsyncMock(side_effect=model)))
        for p in patches:
            p.start()
        try:
            run = self.new_run()
            with patch("hosting.pipeline.stream_text", provider):
                await Worker(self.repo, self.pipeline).execute(run)
            state = self.repo.get_run(self.user, run["id"])
            trace = json.loads(self.repo.objects.get(state["trace_ref"]))
            return state, trace, self.repo.history(self.user, self.session["id"])["items"][0]["content_preview"]
        finally:
            for p in reversed(patches):
                p.stop()

    async def test_insufficient_skips_stream_and_records_decision_in_persisted_trace(self):
        provider = AsyncMock(side_effect=AssertionError("generation must not start"))
        state, trace, answer = await self.execute({"status": "insufficient",
            "missing_information": ["曝光与转化数据"], "evidence_ids": []}, provider)
        self.assertEqual(state["status"], "succeeded")
        self.assertIn("曝光与转化", answer)
        provider.assert_not_called()
        span = next(x for x in trace["spans"] if x["stage"] == "rag_evidence")
        self.assertEqual(span["evidence"]["rejected_ids"], ["b"])
        self.assertFalse(any(x["stage"] == "model" for x in trace["spans"]))

    async def test_sufficient_stream_receives_only_checked_evidence(self):
        async def provider(*args, **kwargs):
            context = kwargs["messages"][-1]["content"]
            self.assertIn("有用规则", context)
            self.assertNotIn("不相关干扰", context)
            yield "依据已检查规则回答"
        state, trace, answer = await self.execute({"status": "sufficient", "missing_information": [],
                                                  "evidence_ids": ["a"]}, provider)
        self.assertEqual(state["status"], "succeeded")
        self.assertIn("依据已检查规则回答", answer)
        self.assertIn("本轮检索依据", answer)
        self.assertIn("ID: a", answer)
        self.assertNotIn("ID: b", answer)
        self.assertTrue(any(x["stage"] == "model" for x in trace["spans"]))
        self.pipeline.rag_manager.rewrite_query.assert_awaited_once()
        self.assertLessEqual(self.pipeline.rag_manager.call.await_count, 4)
        self.assertEqual(len(self.pipeline.rag_manager._rerank.call_args.args[1]), 2)

    async def test_native_application_memory_reaches_model_and_success_enqueues_job(self):
        from memory.repository import MemoryRepository
        from memory.context_service import Scope
        from memory.durable_schema import jobs
        from sqlalchemy import select
        scope=Scope(self.user,self.session['workspace_id'],self.session['id'])
        store=MemoryRepository(self.repo)
        store.preference(scope,'优化目标',['获取咨询'],0)
        async def provider(*args,**kwargs):
            self.assertIn('获取咨询',kwargs['messages'][-1]['content'])
            yield '根据本轮事实提出测试建议'
        with patch.dict(os.environ,AGENT_MEMORY_READ_ENABLED='true',AGENT_MEMORY_WRITE_ENABLED='true',AGENT_MEMORY_SHADOW_ENABLED='false'):
            state,trace,answer=await self.execute({'status':'sufficient','missing_information':[], 'evidence_ids':['a']},provider)
        self.assertEqual(state['status'],'succeeded')
        span=next(v for v in trace['spans'] if v['stage']=='memory_context')
        snapshot=json.loads(self.repo.objects.get(span['snapshot_ref']))
        self.assertNotIn('text',snapshot)
        self.assertEqual(span['experience_status'],'disabled')
        with self.repo.engine.connect() as c:
            self.assertEqual(c.execute(select(jobs.c.status)).scalar_one(),'pending')

    async def test_explicit_profile_declaration_commits_task_without_business_rag(self):
        def run():
            self.repo.create_run(self.user,self.session['id'],'profile-declaration-key-1234',{'message':'请记住：我的长期素材偏好是真人口播。'})
            return self.repo.claim('profile-test')
        self.new_run=run
        provider=AsyncMock(side_effect=AssertionError('profile declaration must not call generation'))
        with patch.dict(os.environ,AGENT_MEMORY_WRITE_ENABLED='true',AGENT_MEMORY_SUBJECTS=''):
            state,trace,answer=await self.execute({'status':'insufficient','missing_information':['投放数据'],'evidence_ids':[]},provider)
        self.assertEqual(state['status'],'succeeded')
        self.assertIn('后台提取',answer)
        provider.assert_not_called()
        from memory.durable_schema import jobs
        from sqlalchemy import select
        with self.repo.engine.connect() as c:self.assertEqual(c.execute(select(jobs.c.status)).scalar_one(),'pending')

    async def test_memory_timeout_degrades_without_disabling_knowledge_evidence(self):
        import time
        def slow_read(*args,**kwargs):time.sleep(.05);return {}
        async def provider(*args,**kwargs):
            self.assertIn('有用规则',kwargs['messages'][-1]['content'])
            yield '继续依据本轮知识回答'
        with patch.dict(os.environ,AGENT_MEMORY_READ_ENABLED='true',AGENT_MEMORY_SUBJECTS='',AGENT_MEMORY_READ_TIMEOUT_MS='10'), patch('memory.context_service.MemoryContextService.build',side_effect=slow_read):
            state,trace,answer=await self.execute({'status':'sufficient','missing_information':[],'evidence_ids':['a']},provider)
        self.assertEqual(state['status'],'succeeded')
        span=next(s for s in trace['spans'] if s['stage']=='memory_context')
        self.assertEqual(span['status'],'unavailable')
        self.assertIn('继续依据本轮知识回答',answer)

    async def test_full_window_snapshot_and_metric_boundaries_reach_checker_and_model(self):
        original = self.new_run
        def new_run():
            run = original()
            # The payload is frozen before execution, like the authenticated gateway.
            payload = json.loads(self.repo.objects.get(run['input_ref']))
            payload['performance_context'] = {'version':1, 'source':'ad_performance_ledger',
                'window':['2026-10-12','2026-10-13','2026-10-14'], 'scope':'selected_authorized_ads',
                'measured_count':1, 'items':[{'ad_id':'1','spend_minor':42}],
                'padding':'完整快照' * 3000}
            ref = self.repo.objects.put(json.dumps(payload))
            run['input_ref'] = ref
            return run
        self.new_run = new_run
        async def provider(*args, **kwargs):
            text = kwargs['messages'][-1]['content']
            self.assertIn('2026-10-12', text)
            self.assertIn('"spend_minor":42', text)
            self.assertIn('完整快照' * 3000, text)
            self.assertIn('金额单位 fen', kwargs['system'])
            self.assertIn('广告实际投放数据进行完整分析', kwargs['system'])
            yield '模拟数据建议'
        state, trace, answer = await self.execute({'status':'sufficient', 'missing_information':[], 'evidence_ids':['a']}, provider)
        self.assertEqual(state['status'], 'succeeded')
        self.assertEqual(next(s for s in trace['spans'] if s['stage']=='performance_context')['measured_count'],1)

    async def test_lost_lease_is_not_swallowed_as_retrieval_failure(self):
        run = self.new_run()
        from hosting.worker import RunContext
        ctx = RunContext(self.repo, run)
        original = ctx.tool
        async def tool(name, *args, **kwargs):
            if name == "knowledge_search":
                raise LostLease("lease changed")
            return await original(name, *args, **kwargs)
        with patch.object(ctx, "tool", tool), self.assertRaises(LostLease):
            await self.pipeline(ctx)

    async def test_all_recall_failures_record_unavailable_and_skip_generation(self):
        self.pipeline.rag_manager.call.return_value = ToolResult(False, [], "knowledge_search")
        provider = AsyncMock(side_effect=AssertionError("generation must not start"))
        state, trace, answer = await self.execute({"status": "insufficient",
            "missing_information": ["运营知识"], "evidence_ids": []}, provider)
        provider.assert_not_called()
        self.assertEqual(state["status"], "succeeded")
        evidence = next(x for x in trace["spans"] if x["stage"] == "rag_evidence")["evidence"]
        self.assertEqual(evidence["status"], "unavailable")
        self.assertEqual(evidence["retrieval_error"], "retrieval_failed")


if __name__ == "__main__":
    unittest.main()
