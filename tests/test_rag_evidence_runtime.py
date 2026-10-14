import asyncio
import json
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
import api.main as api
from agents.agent_orchestrator import AgentType, OrchestratorResult
from core.intent_recognizer import IntentCategory
from core.rag_evidence import (EvidenceConfig, EvidenceGate, blocked_response,
                               check_evidence, prepare_chunks)
from mcp.tool_manager import MCPToolManager, ToolResult


class Client:
    def __init__(self, outputs):
        self.outputs = iter(outputs)
        self.messages = self
        self.inputs = []

    async def create(self, **kwargs):
        self.inputs.append(json.loads(kwargs["messages"][0]["content"]))
        value = next(self.outputs)
        if isinstance(value, Exception):
            raise value
        return SimpleNamespace(content=[SimpleNamespace(text=json.dumps(value))],
                               usage={"prompt_tokens": 10, "completion_tokens": 20, "cost": 0})


def scores(a=.9, b=.1):
    return {"scores": [{"id": "a", "support": a}, {"id": "b", "support": b}]}


def sufficient(*ids):
    return {"status": "sufficient", "missing_information": [], "evidence_ids": list(ids)}


def insufficient(*missing):
    return {"status": "insufficient", "missing_information": list(missing), "evidence_ids": []}


class RuntimeGateTests(unittest.IsolatedAsyncioTestCase):
    chunks = [{"id": "a", "title": "计费", "content": "每次计费单价固定为 0.2 元"},
              {"id": "b", "title": "出价", "content": "加价可改变排名分数"}]

    def setUp(self):
        self.env = patch.dict(os.environ, {"RAG_EVIDENCE_ENABLED": "true", "RAG_EVIDENCE_THRESHOLD": ".7",
                                          "RAG_EVIDENCE_TIMEOUT_SECONDS": "8"})
        self.env.start()

    def tearDown(self):
        self.env.stop()

    async def test_filter_exact_evidence_and_record_usage(self):
        client = Client([scores(), sufficient("a")])
        with self.assertLogs("core.rag_evidence", level="INFO") as logs:
            decision = await check_evidence(client, "test", "计费单价？", self.chunks, request_id="r1")
        self.assertEqual([x["id"] for x in client.inputs[1]["retained_chunks"]], ["a"])
        self.assertEqual(decision.retained_ids, ["a"])
        self.assertEqual(decision.rejected_ids, ["b"])
        self.assertEqual(decision.calls[0]["provider_reported_cost_usd"], 0)
        self.assertEqual(decision.calls[0]["input_tokens"], 10)
        self.assertIn('"request_id": "r1"', logs.output[0])
        self.assertNotIn("每次计费单价", logs.output[0])

    async def test_all_filtered_still_identifies_specific_missing_information(self):
        client = Client([scores(.1, .2), insufficient("当前 CPS 合同中的佣金比例")])
        decision = await check_evidence(client, "test", "当前合同佣金比例？", self.chunks)
        self.assertEqual(client.inputs[1]["retained_chunks"], [])
        self.assertIn("CPS 合同", blocked_response(decision))

    async def test_business_alone_can_answer(self):
        client = Client([sufficient("business")])
        decision = await check_evidence(client, "test", "所选广告有几个？", [], {"selected_count": 2})
        self.assertEqual(decision.status, "sufficient")
        self.assertEqual(decision.evidence_ids, ["business"])

    async def test_invalid_judgment_does_not_allow_generation(self):
        cases = [scores(float("nan")), scores(True), {"scores": []},
                 {"scores": [{"id": "a", "support": .9}, {"id": "a", "support": .8}]},
                 scores(1.1), scores(-.1)]
        for output in cases:
            with self.subTest(output=output):
                decision = await check_evidence(Client([output]), "test", "q", self.chunks)
                self.assertEqual(decision.status, "unavailable")
                self.assertEqual(decision.error_code, "invalid_response")
                self.assertEqual(decision.retained_ids, [])

    async def test_unknown_or_filtered_citation_is_unavailable(self):
        for citation in ["b", "unknown", "business"]:
            decision = await check_evidence(Client([scores(), sufficient(citation)]), "test", "q", self.chunks)
            self.assertEqual(decision.status, "unavailable")

    async def test_provider_failure_is_not_insufficiency_and_has_unknown_cost(self):
        decision = await check_evidence(Client([RuntimeError("secret-provider-diagnostics")]), "test", "q", self.chunks)
        self.assertEqual(decision.error_code, "provider_error")
        self.assertEqual(decision.calls[0]["status"], "failed")
        self.assertIsNone(decision.calls[0]["provider_reported_cost_usd"])
        self.assertNotIn("secret", json.dumps(decision.to_dict()))

    async def test_timeout_cancels_checker_without_fallback_generation(self):
        stopped = asyncio.Event()
        async def slow(**kwargs):
            try:
                await asyncio.sleep(10)
            finally:
                stopped.set()
        client = SimpleNamespace(messages=SimpleNamespace(create=slow))
        with patch.dict(os.environ, {"RAG_EVIDENCE_TIMEOUT_SECONDS": ".01"}):
            decision = await check_evidence(client, "test", "q", [])
        self.assertEqual(decision.error_code, "timeout")
        self.assertTrue(stopped.is_set())
        self.assertIn("稍后重试", blocked_response(decision))

    async def test_retrieval_failure_can_be_answered_only_by_sufficient_business_data(self):
        decision = await check_evidence(Client([insufficient("合同")]), "test", "q", [], retrieval_error="retrieval_failed")
        self.assertEqual(decision.status, "unavailable")
        decision = await check_evidence(Client([sufficient("business")]), "test", "q", [], {"value": 1}, retrieval_error="retrieval_failed")
        self.assertEqual(decision.status, "sufficient")
        self.assertEqual(decision.retrieval_error, "retrieval_failed")

    async def test_runtime_ab_provider_failure_is_not_a_correct_refusal(self):
        from evaluation.run_rag_evidence_ab import run_arm
        client = Client([RuntimeError("quota")])
        client.calls, client.close = [], AsyncMock()
        with patch("evaluation.run_rag_evidence_ab.RecordedClient", return_value=client), \
             patch("evaluation.run_rag_evidence_ab.AdsAgent") as generator:
            result = await run_arm({"id": "x", "question": "佣金比例？", "chunks": [], "business_data": {}},
                                   True, {"key": "test", "base": None, "model": "test"}, None, None, .7, "runtime-v2")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["gate"]["status"], "unavailable")
        generator.assert_not_called()

    async def test_configured_threshold_and_rollback(self):
        with patch.dict(os.environ, {"RAG_EVIDENCE_THRESHOLD": ".95"}):
            decision = await check_evidence(Client([scores(), insufficient("规则")]), "test", "q", self.chunks)
            self.assertEqual(decision.retained_ids, [])
            self.assertEqual(decision.threshold, .95)
        client = Client([])
        with patch.dict(os.environ, {"RAG_EVIDENCE_ENABLED": "false"}):
            decision = await check_evidence(client, "test", "q", self.chunks)
        self.assertEqual(decision.status, "disabled")
        self.assertEqual(decision.retained_ids, ["a", "b"])
        self.assertEqual(client.inputs, [])

    def test_invalid_configuration_fails_clearly(self):
        for values in [{"RAG_EVIDENCE_THRESHOLD": "nan"}, {"RAG_EVIDENCE_TIMEOUT_SECONDS": "inf"},
                       {"RAG_EVIDENCE_TIMEOUT_SECONDS": "0"}, {"RAG_EVIDENCE_ENABLED": "typo"}]:
            with patch.dict(os.environ, values), self.assertRaises(ValueError):
                EvidenceConfig.from_env()

    def test_ids_stable_across_similarity_scores_and_fallback_excluded(self):
        original = {"content": "a" * 1000, "title": "t", "score": .8}
        changed = {**original, "score": .2}
        chunks = prepare_chunks([original, changed, {"fallback": True, "content": "未连接数据库"}])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(len(chunks[0]["content"]), 600)
        self.assertEqual(chunks[0]["id"], prepare_chunks([changed])[0]["id"])

    def test_chroma_id_preserved(self):
        from mcp.knowledge_base import KnowledgeBase
        kb = KnowledgeBase.__new__(KnowledgeBase)
        kb._collection = SimpleNamespace(count=lambda: 1, query=lambda **kwargs: {
            "ids": [["stable-chroma-id"]], "documents": [["text"]],
            "metadatas": [[{"title": "rule"}]], "distances": [[.2]]})
        self.assertEqual(kb.search("q")[0]["id"], "stable-chroma-id")

    async def test_sync_chroma_call_does_not_block_retrieval_deadline(self):
        import threading
        from mcp.knowledge_base import KnowledgeBase
        kb = KnowledgeBase.__new__(KnowledgeBase)
        release = threading.Event()
        def slow_search(*args, **kwargs):
            release.wait(1)
            return []
        kb.search = slow_search
        try:
            with self.assertRaises(asyncio.TimeoutError):
                await asyncio.wait_for(kb.search_handler({"query": "q"}, None), .02)
        finally:
            release.set()

    def test_insufficient_revenue_keeps_simulation_explicitly_separate(self):
        from core.rag_evidence import EvidenceDecision
        decision = EvidenceDecision("insufficient", missing_information=["转化率和成本数据"])
        response = blocked_response(decision, {"bid_simulation": {
            "strategy": "incremental_bid_test", "candidates": [
                {"id": "ad1", "price": 2, "simulated_price": 2.2, "simulated_score": 11.44}]}})
        self.assertIn("转化率和成本", response)
        self.assertIn("11.44", response)
        self.assertIn("不是收益预测", response)


class Memory:
    def __init__(self):
        self.messages = []
        self.update_profile = AsyncMock()

    async def get_context(self, *args, **kwargs):
        return SimpleNamespace(recent_messages=[], to_prompt_text=lambda: "previous assistant guessed 50%")

    async def add_message(self, *args, **kwargs):
        self.messages.append(kwargs.get("metadata", {}))


class ChatIntegrationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {"RAG_EVIDENCE_ENABLED": "true", "RAG_EVIDENCE_THRESHOLD": ".7",
                                          "RAG_EVIDENCE_TIMEOUT_SECONDS": "8", "AGENT_SERVICE_TOKEN": "test-service-token"})
        self.env.start()
        self.manager = MCPToolManager.__new__(MCPToolManager)
        self.manager._model = "test"
        self.manager.rewrite_query = AsyncMock(side_effect=lambda query, n=3: [query, query + " 运营规则"])
        self.manager._rerank = AsyncMock(side_effect=lambda query, items, k: items[:k])
        self.manager.call = AsyncMock(return_value=ToolResult(True, RuntimeGateTests.chunks, "knowledge_search"))
        self.memory = Memory()
        async def preflight(request):
            request.intent = IntentCategory.AD_OPTIMIZATION
            return None
        self.orchestrator = SimpleNamespace(preflight=AsyncMock(side_effect=preflight), run=AsyncMock(
            return_value=OrchestratorResult("r", "事实答案", AgentType.ADS, IntentCategory.AD_OPTIMIZATION)))
        self.patches = [patch.object(api, "_tool_manager", self.manager), patch.object(api, "_memory", self.memory),
                        patch.object(api, "_orchestrator", self.orchestrator), patch.object(api, "_faq_cache", None)]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in reversed(self.patches):
            p.stop()
        self.env.stop()

    async def chat(self, **kwargs):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test",
                                     headers={"Authorization": "Bearer test-service-token"}) as client:
            return await client.post("/chat", json={"message": "广告加价 10% 收益增加多少？", "user_id": "u", **kwargs})

    async def test_http_sufficient_passes_only_retained_chunks_to_agent_and_memory(self):
        self.manager._client = Client([scores(), sufficient("a")])
        response = await self.chat()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["knowledge_evidence"]["retained_ids"], ["a"])
        context = self.orchestrator.run.call_args.args[0].context
        self.assertIn("计费单价固定", context)
        self.assertNotIn("加价可改变", context)
        self.assertEqual(self.memory.messages[-1]["knowledge_evidence"]["rejected_ids"], ["b"])
        self.manager.rewrite_query.assert_awaited_once()
        self.assertEqual(self.manager.call.await_count, 2)
        self.assertEqual(len(self.manager._rerank.call_args.args[1]), 2)
        # Exactly the two evidence calls, after retrieval and with business data.
        self.assertEqual(len(self.manager._client.inputs), 2)

    async def test_creative_request_also_uses_enhanced_retrieval(self):
        async def preflight(request):
            request.intent = IntentCategory.CREATIVE_GENERATION
            return None
        self.orchestrator.preflight.side_effect = preflight
        self.manager._client = Client([scores(), sufficient("a")])
        response = await self.chat()
        self.assertEqual(response.status_code, 200)
        self.manager.rewrite_query.assert_awaited_once()
        self.assertIn("creative_brief", self.manager._client.inputs[-1]["business_data"])
        self.orchestrator.run.assert_awaited_once()

    async def test_rewrite_timeout_falls_back_to_original_recall(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
        self.manager.rewrite_query.side_effect = slow
        result = await self.manager.search_with_rewrite("knowledge_search", "广告", top_k=3,
            check_retrieved_evidence=False, stage_timeout=.01)
        self.assertTrue(result.success)
        self.manager.call.assert_awaited_once_with("knowledge_search", {"query": "广告", "top_k": 5},
                                                 None, use_cache=True)

    async def test_rerank_timeout_keeps_recalled_evidence(self):
        async def slow(*args, **kwargs):
            await asyncio.sleep(1)
        self.manager._rerank.side_effect = slow
        result = await self.manager.search_with_rewrite("knowledge_search", "广告", top_k=3,
            check_retrieved_evidence=False, stage_timeout=.01)
        self.assertTrue(result.success)
        self.assertEqual([x["id"] for x in result.data], ["a", "b"])
        self.assertFalse(result.reranked)

    async def test_one_failed_recall_keeps_other_query_results(self):
        self.manager.call.side_effect = [RuntimeError("offline"),
                                        ToolResult(True, RuntimeGateTests.chunks, "knowledge_search")]
        result = await self.manager.search_with_rewrite("knowledge_search", "广告", top_k=3,
                                                       check_retrieved_evidence=False)
        self.assertTrue(result.success)
        self.assertEqual([x["id"] for x in result.data], ["a", "b"])

    async def test_http_insufficient_skips_agent_and_profile_and_reports_missing(self):
        self.manager._client = Client([scores(), insufficient("转化数据", "成本数据")])
        response = await self.chat()
        body = response.json()
        self.assertEqual(body["decision_reason"], "rag_evidence_insufficient")
        self.assertIn("转化数据", body["response"])
        self.assertFalse(body["knowledge_used"])
        self.orchestrator.run.assert_not_awaited()
        self.memory.update_profile.assert_not_awaited()

    async def test_provider_failure_is_visible_and_skips_agent(self):
        self.manager._client = Client([RuntimeError("busy")])
        response = await self.chat()
        self.assertEqual(response.json()["knowledge_evidence"]["status"], "unavailable")
        self.assertEqual(response.json()["decision_reason"], "rag_evidence_unavailable")
        self.orchestrator.run.assert_not_awaited()

    async def test_all_recall_failures_are_visible_and_skip_agent(self):
        self.manager.call.side_effect = RuntimeError("offline")
        self.manager._client = Client([insufficient("知识规则")])
        response = await self.chat()
        self.assertEqual(response.json()["knowledge_evidence"]["status"], "unavailable")
        self.orchestrator.run.assert_not_awaited()

    async def test_successful_empty_retrieval_is_not_a_failure(self):
        self.manager.call = AsyncMock(return_value=ToolResult(True, [], "knowledge_search"))
        self.manager._client = Client([insufficient("当前合同")])
        response = await self.chat()
        self.assertEqual(response.json()["knowledge_evidence"]["status"], "insufficient")

    async def test_fallback_diagnostic_is_never_evidence(self):
        self.manager.call = AsyncMock(return_value=ToolResult(True, [{"content": "fallback", "fallback": True}], "knowledge_search"))
        self.manager._client = Client([insufficient("规则")])
        response = await self.chat()
        self.assertEqual(response.json()["knowledge_evidence"]["status"], "unavailable")
        self.assertEqual(self.manager._client.inputs[0]["retained_chunks"], [])

    async def test_real_tools_keep_ranking_simulation_when_revenue_is_unknown(self):
        from mcp.ads_tools import ads_summary_handler, ad_performance_search_handler, bid_simulation_handler
        handlers = {"ads_summary": ads_summary_handler, "ad_performance_search": ad_performance_search_handler,
                    "bid_simulation": bid_simulation_handler}
        async def call(name, params, context=None, **kwargs):
            if name == "knowledge_search":
                return ToolResult(True, [], name)
            return ToolResult(True, await handlers[name](params, context), name)
        self.manager.call = AsyncMock(side_effect=call)
        self.manager._client = Client([insufficient("转化和成本数据")])
        response = await self.chat(ads=[{"id": "ad1", "price": 2, "clicks": 10}])
        body = response.json()
        self.assertIn("2.2", body["response"])
        self.assertIn("11.44", body["response"])
        self.assertIn("不是收益预测", body["response"])
        self.orchestrator.run.assert_not_awaited()
        self.assertNotIn("previous assistant", json.dumps(self.manager._client.inputs))

    async def test_http_search_checked_after_stable_id_dedupe(self):
        self.manager.rewrite_query = AsyncMock(return_value=["q", "q2"])
        self.manager._rerank = AsyncMock(side_effect=lambda query, items, k: items[:k])
        self.manager.call = AsyncMock(side_effect=[ToolResult(True, RuntimeGateTests.chunks, "knowledge_search"),
            ToolResult(True, [{**x, "score": .01} for x in RuntimeGateTests.chunks], "knowledge_search")])
        self.manager._client = Client([scores(), sufficient("a")])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test",
                                     headers={"Authorization": "Bearer test-service-token"}) as client:
            response = await client.post("/search", params={"query": "q", "top_k": 3})
        self.assertEqual(response.status_code, 200)
        self.assertEqual([x["id"] for x in response.json()["results"]], ["a"])
        self.assertEqual(len(self.manager._rerank.call_args.args[1]), 2)

    async def test_http_search_unavailable_is_503(self):
        self.manager.rewrite_query = AsyncMock(return_value=["q"])
        self.manager._rerank = AsyncMock(side_effect=lambda query, items, k: items[:k])
        self.manager._client = Client([RuntimeError("unavailable")])
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.app), base_url="http://test",
                                     headers={"Authorization": "Bearer test-service-token"}) as client:
            response = await client.post("/search", params={"query": "q"})
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["evidence"]["status"], "unavailable")


if __name__ == "__main__":
    unittest.main()
