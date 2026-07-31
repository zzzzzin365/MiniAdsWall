import unittest

from core.action_decision import (
    ActionDecisionEngine,
    DecisionType,
    PendingActionStore,
    PendingClarificationStore,
    RiskLevel,
)
from core.intent_recognizer import IntentCategory, UrgencyLevel


class FakeRedis:
    def __init__(self):
        self.data = {}

    def setex(self, key, ttl, value):
        self.data[key] = value

    def get(self, key):
        return self.data.get(key)

    def eval(self, script, key_count, key):
        return self.data.pop(key, None)


class ActionDecisionEngineTest(unittest.TestCase):
    ALL_PERMISSIONS = [
        "ads.delete",
        "ads.asset.delete",
        "ads.budget.write",
    ]

    def setUp(self):
        self.engine = ActionDecisionEngine()

    def decide(
        self,
        message: str,
        intent: IntentCategory,
        conv_id: str = "conv",
        permissions=None,
    ):
        return self.engine.decide(
            message=message,
            user_id="user-1",
            conv_id=conv_id,
            intent=intent,
            urgency=UrgencyLevel.LOW,
            permissions=permissions if permissions is not None else self.ALL_PERMISSIONS,
        )

    def test_safe_request_executes(self):
        result = self.decide(
            "分析当前广告点击表现",
            IntentCategory.AD_OPTIMIZATION,
        )
        self.assertEqual(result.decision, DecisionType.EXECUTE)
        self.assertEqual(result.risk_level, RiskLevel.LOW)

    def test_low_confidence_other_is_rejected(self):
        result = self.decide("%%%...", IntentCategory.OTHER)
        self.assertEqual(result.decision, DecisionType.REJECT)
        self.assertEqual(result.reason, "unrecognized_intent")

    def test_underspecified_request_clarifies(self):
        result = self.decide("帮我处理一下", IntentCategory.REQUEST)
        self.assertEqual(result.decision, DecisionType.CLARIFY)
        self.assertEqual(result.missing_fields, ["target", "expected_result"])

    def test_explicit_high_risk_rule_survives_other_intent(self):
        result = self.decide(
            "删除广告 ad-42",
            IntentCategory.OTHER,
            "explicit-risk",
        )
        self.assertEqual(result.decision, DecisionType.CONFIRM)
        self.assertEqual(result.action_name, "delete_ad")

    def test_missing_permission_rejects_before_confirmation(self):
        result = self.decide(
            "删除广告 ad-99",
            IntentCategory.REQUEST,
            "permission",
            permissions=[],
        )
        self.assertEqual(result.decision, DecisionType.REJECT)
        self.assertEqual(result.reason, "permission_denied:ads.delete")

    def test_missing_field_clarifies_then_confirms_then_executes(self):
        first = self.decide(
            "删除广告",
            IntentCategory.REQUEST,
            "delete-flow",
        )
        self.assertEqual(first.decision, DecisionType.CLARIFY)
        self.assertEqual(first.missing_fields, ["ad_id"])

        second = self.engine.resolve_clarification(
            user_id="user-1",
            conv_id="delete-flow",
            message="ad-123",
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertIsNotNone(second)
        self.assertEqual(second.decision, DecisionType.CONFIRM)
        self.assertIsNotNone(second.confirmation_id)

        third = self.engine.resolve_pending(
            user_id="user-1",
            conv_id="delete-flow",
            message="确认执行",
            confirmation_id=second.confirmation_id,
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertIsNotNone(third)
        self.assertEqual(third.decision, DecisionType.EXECUTE)
        self.assertTrue(third.confirmed)
        self.assertIn("ad-123", third.pending_action.message)

    def test_multiple_missing_fields_are_collected_across_turns(self):
        first = self.decide(
            "调整预算",
            IntentCategory.BID_STRATEGY,
            "budget-flow",
        )
        self.assertEqual(first.decision, DecisionType.CLARIFY)
        self.assertEqual(first.missing_fields, ["ad_id", "amount"])

        second = self.engine.resolve_clarification(
            user_id="user-1",
            conv_id="budget-flow",
            message="广告 ad-5",
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertEqual(second.decision, DecisionType.CLARIFY)
        self.assertEqual(second.missing_fields, ["amount"])

        third = self.engine.resolve_clarification(
            user_id="user-1",
            conv_id="budget-flow",
            message="提高 10%",
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertEqual(third.decision, DecisionType.CONFIRM)

    def test_advisory_bid_request_does_not_require_confirmation(self):
        result = self.decide(
            "哪些广告应该提高出价？",
            IntentCategory.BID_STRATEGY,
        )
        self.assertEqual(result.decision, DecisionType.EXECUTE)

    def test_confirmation_is_bound_to_user_and_conversation(self):
        requested = self.decide(
            "删除广告 ad-77",
            IntentCategory.REQUEST,
            "owner-conv",
        )
        result = self.engine.resolve_pending(
            user_id="user-1",
            conv_id="other-conv",
            message="确认执行",
            confirmation_id=requested.confirmation_id,
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertEqual(result.decision, DecisionType.REJECT)
        self.assertEqual(result.reason, "invalid_or_expired_confirmation")

    def test_permission_is_checked_again_when_confirming(self):
        requested = self.decide(
            "删除广告 ad-66",
            IntentCategory.REQUEST,
            "permission-expired",
        )
        result = self.engine.resolve_pending(
            user_id="user-1",
            conv_id="permission-expired",
            message="确认执行",
            confirmation_id=requested.confirmation_id,
            permissions=[],
        )
        self.assertEqual(result.decision, DecisionType.REJECT)
        self.assertEqual(result.reason, "permission_denied:ads.delete")

    def test_cancellation_consumes_pending_action(self):
        requested = self.decide(
            "删除素材 asset-12",
            IntentCategory.REQUEST,
            "cancel-flow",
        )
        cancelled = self.engine.resolve_pending(
            user_id="user-1",
            conv_id="cancel-flow",
            message="取消操作",
            confirmation_id=requested.confirmation_id,
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertEqual(cancelled.decision, DecisionType.REJECT)
        self.assertEqual(cancelled.reason, "user_cancelled")
        self.assertIsNone(
            self.engine.pending_store.get("user-1", "cancel-flow")
        )

    def test_redis_state_is_shared_and_consumed_exactly_once(self):
        backend = FakeRedis()
        first = ActionDecisionEngine(
            pending_store=PendingActionStore(redis_client=backend),
            clarification_store=PendingClarificationStore(redis_client=backend),
        )
        second = ActionDecisionEngine(
            pending_store=PendingActionStore(redis_client=backend),
            clarification_store=PendingClarificationStore(redis_client=backend),
        )

        requested = first.decide(
            message="删除广告 ad-88",
            user_id="user-1",
            conv_id="shared-conv",
            intent=IntentCategory.REQUEST,
            urgency=UrgencyLevel.LOW,
            permissions=self.ALL_PERMISSIONS,
        )
        resumed = second.resolve_pending(
            user_id="user-1",
            conv_id="shared-conv",
            message="确认执行",
            confirmation_id=requested.confirmation_id,
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertEqual(resumed.decision, DecisionType.EXECUTE)

        duplicate = first.resolve_pending(
            user_id="user-1",
            conv_id="shared-conv",
            message="确认执行",
            confirmation_id=requested.confirmation_id,
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertEqual(duplicate.decision, DecisionType.REJECT)
        self.assertEqual(duplicate.reason, "invalid_or_expired_confirmation")

    def test_clarification_can_resume_from_another_instance(self):
        backend = FakeRedis()
        first = ActionDecisionEngine(
            pending_store=PendingActionStore(redis_client=backend),
            clarification_store=PendingClarificationStore(redis_client=backend),
        )
        second = ActionDecisionEngine(
            pending_store=PendingActionStore(redis_client=backend),
            clarification_store=PendingClarificationStore(redis_client=backend),
        )

        requested = first.decide(
            message="删除广告",
            user_id="user-1",
            conv_id="shared-clarification",
            intent=IntentCategory.REQUEST,
            urgency=UrgencyLevel.LOW,
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertEqual(requested.decision, DecisionType.CLARIFY)

        resumed = second.resolve_clarification(
            user_id="user-1",
            conv_id="shared-clarification",
            message="ad-101",
            permissions=self.ALL_PERMISSIONS,
        )
        self.assertEqual(resumed.decision, DecisionType.CONFIRM)
        self.assertIsNotNone(resumed.confirmation_id)


if __name__ == "__main__":
    unittest.main()
