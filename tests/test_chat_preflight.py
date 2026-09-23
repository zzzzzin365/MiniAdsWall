import asyncio
import os
import unittest
from unittest.mock import AsyncMock, patch

import api.main as api
from agents.agent_orchestrator import (
    AgentType,
    OrchestratorResult,
)
from core.action_decision import DecisionType, RiskLevel
from core.intent_recognizer import IntentCategory


class FakeMemoryContext:
    recent_messages = []

    @staticmethod
    def to_prompt_text():
        return "memory context"


class FakeMemory:
    def __init__(self):
        self.messages = []
        self.profile_updates = 0

    async def get_context(self, user_id, conv_id, query=""):
        return FakeMemoryContext()

    async def add_message(
        self,
        user_id,
        conv_id,
        role,
        content,
        metadata=None,
    ):
        self.messages.append((role.value, content, metadata or {}))

    async def update_profile(self, user_id, conv_id):
        self.profile_updates += 1


class StubOrchestrator:
    def __init__(self, preflight_result, run_result=None):
        self.preflight_result = preflight_result
        self.run_result = run_result
        self.preflight_request = None
        self.run_request = None

    async def preflight(self, request):
        self.preflight_request = request
        return self.preflight_result

    async def run(self, request):
        self.run_request = request
        return self.run_result


class ChatPreflightTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.old_orchestrator = api._orchestrator
        self.old_memory = api._memory

    def tearDown(self):
        api._orchestrator = self.old_orchestrator
        api._memory = self.old_memory

    async def test_confirm_stops_before_tools_rag_and_profile_update(self):
        decision = OrchestratorResult(
            request_id="request-1",
            response="请确认",
            agent_type=AgentType.GENERAL,
            intent=IntentCategory.REQUEST,
            decision=DecisionType.CONFIRM,
            decision_reason="high_risk:delete_ad",
            confirmation_id="confirm-1",
            risk_level=RiskLevel.HIGH,
        )
        orchestrator = StubOrchestrator(decision)
        memory = FakeMemory()
        api._orchestrator = orchestrator
        api._memory = memory

        ads_context = AsyncMock()
        knowledge_context = AsyncMock()
        with patch.object(api, "_build_ads_context", ads_context), patch.object(
            api,
            "_build_knowledge_context",
            knowledge_context,
        ):
            response = await api.chat(
                api.ChatRequest(
                    message="删除广告 ad-42",
                    user_id="user-1",
                    conv_id="conv-1",
                    ads=[{"id": "ad-42"}],
                )
            )

        self.assertEqual(response.decision, "confirm")
        self.assertEqual(response.tools_used, [])
        ads_context.assert_not_awaited()
        knowledge_context.assert_not_awaited()
        self.assertEqual(memory.profile_updates, 0)

    async def test_execute_uses_server_permissions_then_builds_tools_and_rag(self):
        executed = OrchestratorResult(
            request_id="request-2",
            response="done",
            agent_type=AgentType.ADS,
            intent=IntentCategory.AD_OPTIMIZATION,
            decision=DecisionType.EXECUTE,
            decision_reason="intent_clear_and_safe",
        )
        orchestrator = StubOrchestrator(None, executed)
        memory = FakeMemory()
        api._orchestrator = orchestrator
        api._memory = memory

        permission_map = '{"user-1":["ads.delete","ads.delete","ads.budget.write"]}'
        with patch.dict(os.environ, {"ACTION_PERMISSION_MAP": permission_map}), patch.object(
            api,
            "_build_ads_context",
            AsyncMock(return_value=("ads context", ["ads_summary"])),
        ) as ads_context, patch.object(
            api,
            "_build_knowledge_context",
            AsyncMock(return_value=("knowledge context", True)),
        ) as knowledge_context:
            response = await api.chat(
                api.ChatRequest(
                    message="分析当前广告",
                    user_id="user-1",
                    conv_id="conv-2",
                    ads=[{"id": "ad-1"}],
                )
            )
            await asyncio.sleep(0)

        self.assertEqual(
            orchestrator.preflight_request.permissions,
            ["ads.budget.write", "ads.delete"],
        )
        self.assertEqual(response.decision, "execute")
        self.assertEqual(
            response.tools_used,
            ["ads_summary", "knowledge_search"],
        )
        ads_context.assert_awaited_once()
        knowledge_context.assert_awaited_once()
        self.assertIn("ads context", orchestrator.run_request.context)
        self.assertIn("knowledge context", orchestrator.run_request.context)
        self.assertEqual(memory.profile_updates, 1)

    async def test_model_cannot_claim_business_mutation_completed(self):
        result = OrchestratorResult(
            request_id="mutation-test", response="广告已删除",
            agent_type=AgentType.ADS, intent=IntentCategory.REQUEST,
            decision=DecisionType.EXECUTE, action_name="delete_ad", confirmed=True,
        )
        api._orchestrator = StubOrchestrator(None, result)
        memory = FakeMemory()
        api._memory = memory
        with patch.object(api, "_build_ads_context", AsyncMock(return_value=("", []))), patch.object(
            api, "_build_knowledge_context", AsyncMock(return_value=("", False))
        ):
            response = await api.chat(api.ChatRequest(message="确认", user_id="operator"))
        self.assertEqual(response.decision, "reject")
        self.assertEqual(response.decision_reason, "business_execution_unavailable")
        self.assertFalse(response.confirmed)
        self.assertIn("未执行", response.response)
        self.assertEqual(memory.profile_updates, 0)
        self.assertNotIn("广告已删除", str(memory.messages))

    def test_invalid_permission_map_fails_closed(self):
        with patch.dict(os.environ, {"ACTION_PERMISSION_MAP": "not-json"}):
            self.assertEqual(api._permissions_for("user-1"), [])


if __name__ == "__main__":
    unittest.main()
