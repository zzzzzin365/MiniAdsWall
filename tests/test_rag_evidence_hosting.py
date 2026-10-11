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
                self.assertEqual([item["id"] for item in state["retained_chunks"]], ["a"])
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
        self.assertEqual(answer, "依据已检查规则回答")
        self.assertTrue(any(x["stage"] == "model" for x in trace["spans"]))

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


if __name__ == "__main__":
    unittest.main()
