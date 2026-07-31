import unittest
from types import SimpleNamespace

from agents.agent_orchestrator import (
    AgentResponse,
    AgentType,
    AgentOrchestrator,
    Request,
)
from core.action_decision import ActionDecisionEngine, DecisionType
from core.intent_recognizer import IntentCategory, UrgencyLevel


class FakeRecognizer:
    def __init__(self, intent: IntentCategory):
        self.intent = intent
        self.calls = 0

    async def recognize(self, message, history=None):
        self.calls += 1
        return SimpleNamespace(
            intent=self.intent,
            urgency=UrgencyLevel.LOW,
            entities={},
            confidence=0.2 if self.intent == IntentCategory.OTHER else 0.95,
        )


class FakeStats:
    @staticmethod
    def routing_score():
        return 1.0


class FakeAgent:
    def __init__(self, agent_type: AgentType):
        self.agent_type = agent_type
        self.stats = FakeStats()
        self.calls = 0
        self.last_request = None

    async def handle(self, request):
        self.calls += 1
        self.last_request = request
        return AgentResponse(
            agent_type=self.agent_type,
            content="fake execution",
            success=True,
        )


def build_orchestrator(intent: IntentCategory):
    orchestrator = AgentOrchestrator.__new__(AgentOrchestrator)
    orchestrator._intent_recognizer = FakeRecognizer(intent)
    orchestrator._decision_engine = ActionDecisionEngine()
    general = FakeAgent(AgentType.GENERAL)
    ads = FakeAgent(AgentType.ADS)
    orchestrator._pool = {
        AgentType.GENERAL: [general],
        AgentType.TECHNICAL: [],
        AgentType.ADS: [ads],
    }
    return orchestrator, general, ads


class OrchestratorDecisionTest(unittest.IsolatedAsyncioTestCase):
    async def test_reject_preflight_never_calls_agent(self):
        orchestrator, general, ads = build_orchestrator(IntentCategory.OTHER)
        request = Request(
            message="%%%...",
            user_id="user-1",
            conv_id="reject-conv",
        )

        result = await orchestrator.preflight(request)

        self.assertIsNotNone(result)
        self.assertEqual(result.decision, DecisionType.REJECT)
        self.assertEqual(general.calls + ads.calls, 0)

    async def test_confirm_preflight_never_calls_agent(self):
        orchestrator, general, ads = build_orchestrator(IntentCategory.REQUEST)
        request = Request(
            message="删除广告 ad-42",
            user_id="user-1",
            conv_id="confirm-conv",
            permissions=["ads.delete"],
        )

        result = await orchestrator.preflight(request)

        self.assertIsNotNone(result)
        self.assertEqual(result.decision, DecisionType.CONFIRM)
        self.assertIsNotNone(result.confirmation_id)
        self.assertEqual(general.calls + ads.calls, 0)

    async def test_safe_execute_uses_prepared_decision_once(self):
        orchestrator, general, ads = build_orchestrator(
            IntentCategory.AD_OPTIMIZATION
        )
        request = Request(
            message="分析当前广告表现",
            user_id="user-1",
            conv_id="execute-conv",
        )

        preflight_result = await orchestrator.preflight(request)
        self.assertIsNone(preflight_result)

        result = await orchestrator.run(request)

        self.assertEqual(result.decision, DecisionType.EXECUTE)
        self.assertEqual(ads.calls, 1)
        self.assertEqual(orchestrator._intent_recognizer.calls, 1)

    async def test_confirmation_restores_original_request(self):
        orchestrator, general, ads = build_orchestrator(IntentCategory.REQUEST)
        initial = Request(
            message="删除广告 ad-7",
            user_id="user-1",
            conv_id="resume-conv",
            permissions=["ads.delete"],
        )
        pending = await orchestrator.preflight(initial)
        self.assertEqual(pending.decision, DecisionType.CONFIRM)

        confirmation = Request(
            message="确认执行",
            user_id="user-1",
            conv_id="resume-conv",
            permissions=["ads.delete"],
            confirmation_id=pending.confirmation_id,
        )
        self.assertIsNone(await orchestrator.preflight(confirmation))

        result = await orchestrator.run(confirmation)

        self.assertEqual(result.decision, DecisionType.EXECUTE)
        self.assertTrue(result.confirmed)
        self.assertTrue(confirmation.confirmed_action)
        self.assertIn("删除广告 ad-7", confirmation.message)
        self.assertEqual(general.calls, 1)


if __name__ == "__main__":
    unittest.main()
