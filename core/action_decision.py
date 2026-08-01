"""将意图识别结果转换成执行、追问、拒识、确认四种可控决策。"""

from __future__ import annotations

import hashlib
import json
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Sequence

from core.intent_recognizer import IntentCategory, UrgencyLevel


class DecisionType(Enum):
    """一次请求只能得到四种处理结果之一。"""

    EXECUTE = "execute"
    CLARIFY = "clarify"
    REJECT = "reject"
    CONFIRM = "confirm"


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


@dataclass(frozen=True)
class ActionRule:
    name: str
    patterns: Sequence[str]
    required_fields: Sequence[str] = ()
    risk_level: RiskLevel = RiskLevel.LOW
    required_permission: Optional[str] = None
    confirmation_text: str = ""
    advisory_markers: Sequence[str] = ()

    def matches(self, message: str) -> bool:
        if self.advisory_markers and any(marker in message for marker in self.advisory_markers):
            return False
        return any(re.search(pattern, message, re.IGNORECASE) for pattern in self.patterns)


@dataclass
class PendingAction:
    confirmation_id: str
    user_id: str
    conv_id: str
    message: str
    intent: IntentCategory
    urgency: UrgencyLevel
    entities: Dict[str, List[str]]
    action_name: str
    risk_level: RiskLevel
    required_permission: Optional[str]
    expires_at: float


@dataclass
class PendingClarification:
    clarification_id: str
    user_id: str
    conv_id: str
    message: str
    intent: IntentCategory
    urgency: UrgencyLevel
    entities: Dict[str, List[str]]
    action_name: str
    required_fields: List[str]
    risk_level: RiskLevel
    required_permission: Optional[str]
    confirmation_text: str
    expires_at: float


@dataclass
class ActionDecision:
    decision: DecisionType
    response: str = ""
    reason: str = ""
    missing_fields: List[str] = field(default_factory=list)
    confirmation_id: Optional[str] = None
    risk_level: RiskLevel = RiskLevel.LOW
    action_name: Optional[str] = None
    confirmed: bool = False
    pending_action: Optional[PendingAction] = None


class _RedisBackedStore:
    """Redis 可用时以 Redis 为权威；连接失败时回退到进程内存。"""

    namespace = "miniadswall:pending"

    def __init__(self, ttl_seconds: int, redis_client: Optional[Any] = None):
        self.ttl_seconds = max(30, ttl_seconds)
        self._redis = redis_client
        self._items: Dict[str, str] = {}
        self._lock = threading.RLock()

    def _key(self, user_id: str, conv_id: str) -> str:
        digest = hashlib.sha256(f"{user_id}:{conv_id}".encode("utf-8")).hexdigest()
        return f"{self.namespace}:{digest}"

    def _put_raw(self, user_id: str, conv_id: str, raw: str) -> None:
        key = self._key(user_id, conv_id)
        with self._lock:
            self._items[key] = raw
        if self._redis is not None:
            try:
                self._redis.setex(key, self.ttl_seconds, raw)
            except Exception:
                pass

    def _get_raw(self, user_id: str, conv_id: str) -> Optional[str]:
        key = self._key(user_id, conv_id)
        if self._redis is not None:
            try:
                # Redis 调用成功时，以“存在/不存在”的返回结果为准，不能读取可能过期的本地副本。
                return self._redis.get(key)
            except Exception:
                pass
        with self._lock:
            return self._items.get(key)

    def _pop_raw(self, user_id: str, conv_id: str) -> Optional[str]:
        key = self._key(user_id, conv_id)
        if self._redis is not None:
            try:
                raw = self._redis.eval(
                    "local v=redis.call('GET',KEYS[1]); "
                    "if v then redis.call('DEL',KEYS[1]); end; return v",
                    1,
                    key,
                )
                with self._lock:
                    self._items.pop(key, None)
                return raw
            except Exception:
                pass
        with self._lock:
            return self._items.pop(key, None)


class PendingActionStore(_RedisBackedStore):
    """按用户和会话隔离的待确认操作存储。"""

    namespace = "miniadswall:pending-confirmation"

    def __init__(self, ttl_seconds: int = 300, redis_client: Optional[Any] = None):
        super().__init__(ttl_seconds, redis_client)

    def put(
        self,
        *,
        user_id: str,
        conv_id: str,
        message: str,
        intent: IntentCategory,
        urgency: UrgencyLevel,
        entities: Dict[str, List[str]],
        action_name: str,
        risk_level: RiskLevel,
        required_permission: Optional[str] = None,
    ) -> PendingAction:
        pending = PendingAction(
            confirmation_id=uuid.uuid4().hex[:12],
            user_id=user_id,
            conv_id=conv_id,
            message=message,
            intent=intent,
            urgency=urgency,
            entities={key: list(values) for key, values in entities.items()},
            action_name=action_name,
            risk_level=risk_level,
            required_permission=required_permission,
            expires_at=time.time() + self.ttl_seconds,
        )
        self._put_raw(user_id, conv_id, self._serialize(pending))
        return pending

    def get(self, user_id: str, conv_id: str) -> Optional[PendingAction]:
        raw = self._get_raw(user_id, conv_id)
        if not raw:
            return None
        pending = self._deserialize(raw)
        if pending.expires_at <= time.time():
            self._pop_raw(user_id, conv_id)
            return None
        return pending

    def pop(self, user_id: str, conv_id: str) -> Optional[PendingAction]:
        raw = self._pop_raw(user_id, conv_id)
        if not raw:
            return None
        pending = self._deserialize(raw)
        return pending if pending.expires_at > time.time() else None

    @staticmethod
    def _serialize(pending: PendingAction) -> str:
        return json.dumps({
            "confirmation_id": pending.confirmation_id,
            "user_id": pending.user_id,
            "conv_id": pending.conv_id,
            "message": pending.message,
            "intent": pending.intent.value,
            "urgency": pending.urgency.value,
            "entities": pending.entities,
            "action_name": pending.action_name,
            "risk_level": pending.risk_level.value,
            "required_permission": pending.required_permission,
            "expires_at": pending.expires_at,
        }, ensure_ascii=False)

    @staticmethod
    def _deserialize(raw: str) -> PendingAction:
        data = json.loads(raw)
        return PendingAction(
            confirmation_id=data["confirmation_id"],
            user_id=data["user_id"],
            conv_id=data["conv_id"],
            message=data["message"],
            intent=IntentCategory(data["intent"]),
            urgency=UrgencyLevel(data["urgency"]),
            entities=data.get("entities", {}),
            action_name=data["action_name"],
            risk_level=RiskLevel(data["risk_level"]),
            required_permission=data.get("required_permission"),
            expires_at=float(data["expires_at"]),
        )


class PendingClarificationStore(_RedisBackedStore):
    """保存待补充字段，使下一轮可以恢复原任务。"""

    namespace = "miniadswall:pending-clarification"

    def __init__(self, ttl_seconds: int = 600, redis_client: Optional[Any] = None):
        super().__init__(ttl_seconds, redis_client)

    def put(
        self,
        *,
        user_id: str,
        conv_id: str,
        message: str,
        intent: IntentCategory,
        urgency: UrgencyLevel,
        entities: Dict[str, List[str]],
        action_name: str,
        required_fields: Sequence[str],
        risk_level: RiskLevel,
        required_permission: Optional[str],
        confirmation_text: str,
    ) -> PendingClarification:
        pending = PendingClarification(
            clarification_id=uuid.uuid4().hex[:12],
            user_id=user_id,
            conv_id=conv_id,
            message=message,
            intent=intent,
            urgency=urgency,
            entities={key: list(values) for key, values in entities.items()},
            action_name=action_name,
            required_fields=list(required_fields),
            risk_level=risk_level,
            required_permission=required_permission,
            confirmation_text=confirmation_text,
            expires_at=time.time() + self.ttl_seconds,
        )
        self._put_raw(user_id, conv_id, self._serialize(pending))
        return pending

    def get(self, user_id: str, conv_id: str) -> Optional[PendingClarification]:
        raw = self._get_raw(user_id, conv_id)
        if not raw:
            return None
        pending = self._deserialize(raw)
        if pending.expires_at <= time.time():
            self._pop_raw(user_id, conv_id)
            return None
        return pending

    def pop(self, user_id: str, conv_id: str) -> Optional[PendingClarification]:
        raw = self._pop_raw(user_id, conv_id)
        if not raw:
            return None
        pending = self._deserialize(raw)
        return pending if pending.expires_at > time.time() else None

    @staticmethod
    def _serialize(pending: PendingClarification) -> str:
        return json.dumps({
            "clarification_id": pending.clarification_id,
            "user_id": pending.user_id,
            "conv_id": pending.conv_id,
            "message": pending.message,
            "intent": pending.intent.value,
            "urgency": pending.urgency.value,
            "entities": pending.entities,
            "action_name": pending.action_name,
            "required_fields": pending.required_fields,
            "risk_level": pending.risk_level.value,
            "required_permission": pending.required_permission,
            "confirmation_text": pending.confirmation_text,
            "expires_at": pending.expires_at,
        }, ensure_ascii=False)

    @staticmethod
    def _deserialize(raw: str) -> PendingClarification:
        data = json.loads(raw)
        return PendingClarification(
            clarification_id=data["clarification_id"],
            user_id=data["user_id"],
            conv_id=data["conv_id"],
            message=data["message"],
            intent=IntentCategory(data["intent"]),
            urgency=UrgencyLevel(data["urgency"]),
            entities=data.get("entities", {}),
            action_name=data["action_name"],
            required_fields=data.get("required_fields", []),
            risk_level=RiskLevel(data["risk_level"]),
            required_permission=data.get("required_permission"),
            confirmation_text=data.get("confirmation_text", ""),
            expires_at=float(data["expires_at"]),
        )


class ActionDecisionEngine:
    """LLM 负责理解语义，代码规则负责决定是否允许进入执行链路。"""

    FIELD_LABELS = {
        "ad_id": "广告 ID",
        "asset_id": "素材 ID",
        "amount": "金额或调整幅度",
        "target": "操作对象",
        "expected_result": "期望结果",
    }

    AFFIRMATIVE = {
        "确认", "确认执行", "我确认", "确定", "继续", "继续执行",
        "是", "是的", "yes", "confirm", "proceed",
    }
    NEGATIVE = {
        "取消", "取消操作", "不要", "不执行", "算了", "否",
        "no", "cancel", "stop",
    }

    ACTION_RULES = (
        ActionRule(
            name="delete_ad",
            patterns=(
                r"(删除|移除|下线|停用).{0,8}(广告|计划|campaign)",
                r"delete\s+(ad|campaign)",
            ),
            required_fields=("ad_id",),
            risk_level=RiskLevel.HIGH,
            required_permission="ads.delete",
            confirmation_text="这会删除、下线或停用指定广告，可能影响正在进行的投放。",
        ),
        ActionRule(
            name="delete_asset",
            patterns=(
                r"(删除|移除).{0,8}(素材|视频|图片)",
                r"delete\s+(asset|creative|video|image)",
            ),
            required_fields=("asset_id",),
            risk_level=RiskLevel.HIGH,
            required_permission="ads.asset.delete",
            confirmation_text="这会删除指定素材，删除后可能无法恢复。",
        ),
        ActionRule(
            name="change_budget_or_bid",
            patterns=(
                r"(修改|调整|设置|提高|降低|增加|减少).{0,10}(预算|出价)",
                r"(预算|出价).{0,10}(修改|调整|设置|提高|降低|增加|减少)",
            ),
            required_fields=("ad_id", "amount"),
            risk_level=RiskLevel.HIGH,
            required_permission="ads.budget.write",
            confirmation_text="这会修改真实广告预算或出价，可能直接产生费用或影响投放效果。",
            advisory_markers=("哪些", "建议", "怎么", "如何", "策略", "模拟", "应该", "分析"),
        ),
    )

    GENERIC_UNDERSPECIFIED = (
        r"^(帮我|请帮我|麻烦)(处理|弄|操作|看|搞)(一下)?[。.!！]?$",
        r"^(我要|我想)(办理|处理|操作)[。.!！]?$",
        r"^(怎么办|帮帮我|help)[。.!！]?$",
    )

    # 这些字段会触发真实广告操作，绝不能由画像、历史或 LLM 猜测补全。
    # amount 是当前实现中预算、出价和调整幅度共用的请求字段。
    CURRENT_REQUEST_ONLY_FIELDS = {"ad_id", "budget", "bid", "amount"}

    def __init__(
        self,
        pending_store: Optional[PendingActionStore] = None,
        clarification_store: Optional[PendingClarificationStore] = None,
        confirmation_ttl_seconds: int = 300,
        clarification_ttl_seconds: int = 600,
        redis_url: Optional[str] = None,
    ):
        redis_client = None
        if redis_url:
            try:
                import redis

                redis_client = redis.from_url(redis_url, decode_responses=True)
            except Exception:
                redis_client = None
        self.pending_store = pending_store or PendingActionStore(
            confirmation_ttl_seconds,
            redis_client=redis_client,
        )
        self.clarification_store = clarification_store or PendingClarificationStore(
            clarification_ttl_seconds,
            redis_client=redis_client,
        )

    def resolve_pending(
        self,
        *,
        user_id: str,
        conv_id: str,
        message: str,
        confirmation_id: Optional[str] = None,
        permissions: Optional[Sequence[str]] = None,
    ) -> Optional[ActionDecision]:
        """优先恢复确认或取消，避免把“确认执行”识别成新意图。"""
        normalized = self._normalize(message)
        pending = self.pending_store.get(user_id, conv_id)

        if confirmation_id and (
            pending is None or pending.confirmation_id != confirmation_id
        ):
            return ActionDecision(
                decision=DecisionType.REJECT,
                response="该确认请求不存在、已过期，或不属于当前会话。请重新发起操作。",
                reason="invalid_or_expired_confirmation",
                confirmation_id=confirmation_id,
            )
        if pending is None:
            return None

        if (
            pending.required_permission
            and pending.required_permission not in set(permissions or ())
        ):
            self.pending_store.pop(user_id, conv_id)
            return ActionDecision(
                decision=DecisionType.REJECT,
                response="你的操作权限已失效，请重新获取权限后发起请求。",
                reason=f"permission_denied:{pending.required_permission}",
                confirmation_id=pending.confirmation_id,
                risk_level=pending.risk_level,
                action_name=pending.action_name,
            )

        if normalized in self.NEGATIVE:
            consumed = self.pending_store.pop(user_id, conv_id)
            if consumed is None:
                return self._expired_confirmation(pending.confirmation_id)
            return ActionDecision(
                decision=DecisionType.REJECT,
                response="已取消该操作，没有执行任何变更。",
                reason="user_cancelled",
                confirmation_id=consumed.confirmation_id,
                risk_level=consumed.risk_level,
                action_name=consumed.action_name,
            )

        if normalized in self.AFFIRMATIVE:
            consumed = self.pending_store.pop(user_id, conv_id)
            if consumed is None:
                return self._expired_confirmation(pending.confirmation_id)
            return ActionDecision(
                decision=DecisionType.EXECUTE,
                reason="user_confirmed",
                confirmation_id=consumed.confirmation_id,
                risk_level=consumed.risk_level,
                action_name=consumed.action_name,
                confirmed=True,
                pending_action=consumed,
            )
        return None

    @staticmethod
    def _expired_confirmation(confirmation_id: Optional[str]) -> ActionDecision:
        return ActionDecision(
            decision=DecisionType.REJECT,
            response="该确认请求已处理或已过期，请重新发起操作。",
            reason="invalid_or_expired_confirmation",
            confirmation_id=confirmation_id,
        )

    def resolve_clarification(
        self,
        *,
        user_id: str,
        conv_id: str,
        message: str,
        permissions: Optional[Sequence[str]] = None,
    ) -> Optional[ActionDecision]:
        """合并用户补充字段，补齐后恢复执行或进入高风险确认。"""
        clarification = self.clarification_store.pop(user_id, conv_id)
        if clarification is None:
            return None

        if (
            clarification.required_permission
            and clarification.required_permission not in set(permissions or ())
        ):
            return ActionDecision(
                decision=DecisionType.REJECT,
                response="你的操作权限已失效，请重新获取权限后发起请求。",
                reason=f"permission_denied:{clarification.required_permission}",
                risk_level=clarification.risk_level,
                action_name=clarification.action_name,
            )

        normalized = self._normalize(message)
        if normalized in self.NEGATIVE:
            return ActionDecision(
                decision=DecisionType.REJECT,
                response="已取消该请求，没有执行任何操作。",
                reason="user_cancelled_clarification",
                risk_level=clarification.risk_level,
                action_name=clarification.action_name,
            )

        extracted = self.extract_entities(message)
        missing_before = [
            name
            for name in clarification.required_fields
            if not clarification.entities.get(name)
        ]
        extracted = self._accept_single_field_answer(
            message,
            missing_before,
            extracted,
        )
        merged_entities = self._merge_entities(clarification.entities, extracted)
        missing_after = [
            name
            for name in clarification.required_fields
            if not merged_entities.get(name)
        ]

        if missing_after == missing_before:
            self._restore_clarification(clarification)
            return None

        combined_message = f"{clarification.message}\n用户补充信息：{message}"
        if missing_after:
            self.clarification_store.put(
                user_id=user_id,
                conv_id=conv_id,
                message=combined_message,
                intent=clarification.intent,
                urgency=clarification.urgency,
                entities=merged_entities,
                action_name=clarification.action_name,
                required_fields=clarification.required_fields,
                risk_level=clarification.risk_level,
                required_permission=clarification.required_permission,
                confirmation_text=clarification.confirmation_text,
            )
            labels = [self.FIELD_LABELS.get(name, name) for name in missing_after]
            return ActionDecision(
                decision=DecisionType.CLARIFY,
                response=f"还需要补充：{'、'.join(labels)}。",
                reason=f"missing_fields:{clarification.action_name}",
                missing_fields=missing_after,
                risk_level=clarification.risk_level,
                action_name=clarification.action_name,
            )

        if clarification.risk_level == RiskLevel.HIGH:
            return self._request_confirmation(
                user_id=user_id,
                conv_id=conv_id,
                message=combined_message,
                intent=clarification.intent,
                urgency=clarification.urgency,
                entities=merged_entities,
                action_name=clarification.action_name,
                risk_level=clarification.risk_level,
                required_permission=clarification.required_permission,
                confirmation_text=clarification.confirmation_text,
            )

        resumed = PendingAction(
            confirmation_id="",
            user_id=user_id,
            conv_id=conv_id,
            message=combined_message,
            intent=clarification.intent,
            urgency=clarification.urgency,
            entities=merged_entities,
            action_name=clarification.action_name,
            risk_level=clarification.risk_level,
            required_permission=clarification.required_permission,
            expires_at=time.time(),
        )
        return ActionDecision(
            decision=DecisionType.EXECUTE,
            reason=f"clarification_completed:{clarification.action_name}",
            risk_level=clarification.risk_level,
            action_name=clarification.action_name,
            pending_action=resumed,
        )

    def decide(
        self,
        *,
        message: str,
        user_id: str,
        conv_id: str,
        intent: IntentCategory,
        urgency: UrgencyLevel,
        entities: Optional[Dict[str, List[str]]] = None,
        permissions: Optional[Sequence[str]] = None,
    ) -> ActionDecision:
        normalized = self._normalize(message)
        supplied_entities = {
            key: values
            for key, values in (entities or {}).items()
            if key not in self.CURRENT_REQUEST_ONLY_FIELDS
        }
        # 高风险执行参数只信任当前请求文本的确定性提取结果。
        # 即使上游 LLM 或画像返回 ad_id/预算/出价，也不能进入决策链。
        merged_entities = self._merge_entities(
            supplied_entities,
            self.extract_entities(message),
        )
        granted_permissions = set(permissions or ())

        # 显式高风险动作优先于低置信度拒识，不能因意图被降级为 OTHER 而绕过安全规则。
        for rule in self.ACTION_RULES:
            if not rule.matches(normalized):
                continue

            if (
                rule.required_permission
                and rule.required_permission not in granted_permissions
            ):
                return ActionDecision(
                    decision=DecisionType.REJECT,
                    response="你当前没有执行该操作所需的权限，请联系有权限的管理员。",
                    reason=f"permission_denied:{rule.required_permission}",
                    risk_level=rule.risk_level,
                    action_name=rule.name,
                )

            missing = [
                name for name in rule.required_fields
                if not merged_entities.get(name)
            ]
            if missing:
                self.clarification_store.put(
                    user_id=user_id,
                    conv_id=conv_id,
                    message=message,
                    intent=intent,
                    urgency=urgency,
                    entities=merged_entities,
                    action_name=rule.name,
                    required_fields=rule.required_fields,
                    risk_level=rule.risk_level,
                    required_permission=rule.required_permission,
                    confirmation_text=rule.confirmation_text,
                )
                labels = [self.FIELD_LABELS.get(name, name) for name in missing]
                return ActionDecision(
                    decision=DecisionType.CLARIFY,
                    response=f"执行前还需要你补充：{'、'.join(labels)}。",
                    reason=f"missing_fields:{rule.name}",
                    missing_fields=missing,
                    risk_level=rule.risk_level,
                    action_name=rule.name,
                )

            if rule.risk_level == RiskLevel.HIGH:
                return self._request_confirmation(
                    user_id=user_id,
                    conv_id=conv_id,
                    message=message,
                    intent=intent,
                    urgency=urgency,
                    entities=merged_entities,
                    action_name=rule.name,
                    risk_level=rule.risk_level,
                    required_permission=rule.required_permission,
                    confirmation_text=rule.confirmation_text,
                )

            return ActionDecision(
                decision=DecisionType.EXECUTE,
                reason=f"ready:{rule.name}",
                risk_level=rule.risk_level,
                action_name=rule.name,
            )

        if self._is_unrecognizable(normalized, intent):
            return ActionDecision(
                decision=DecisionType.REJECT,
                response="我没理解你的意思，请换一种更具体的说法。",
                reason="unrecognized_intent",
            )

        if any(
            re.search(pattern, normalized, re.IGNORECASE)
            for pattern in self.GENERIC_UNDERSPECIFIED
        ):
            return ActionDecision(
                decision=DecisionType.CLARIFY,
                response="你希望我具体处理什么？请补充对象和期望结果。",
                reason="underspecified_request",
                missing_fields=["target", "expected_result"],
            )

        return ActionDecision(
            decision=DecisionType.EXECUTE,
            reason="intent_clear_and_safe",
        )

    def _request_confirmation(
        self,
        *,
        user_id: str,
        conv_id: str,
        message: str,
        intent: IntentCategory,
        urgency: UrgencyLevel,
        entities: Dict[str, List[str]],
        action_name: str,
        risk_level: RiskLevel,
        required_permission: Optional[str],
        confirmation_text: str,
    ) -> ActionDecision:
        pending = self.pending_store.put(
            user_id=user_id,
            conv_id=conv_id,
            message=message,
            intent=intent,
            urgency=urgency,
            entities=entities,
            action_name=action_name,
            risk_level=risk_level,
            required_permission=required_permission,
        )
        return ActionDecision(
            decision=DecisionType.CONFIRM,
            response=(
                f"{confirmation_text}\n"
                "确认执行请回复“确认执行”，取消请回复“取消操作”。"
            ),
            reason=f"high_risk:{action_name}",
            confirmation_id=pending.confirmation_id,
            risk_level=risk_level,
            action_name=action_name,
        )

    def _restore_clarification(self, pending: PendingClarification) -> None:
        self.clarification_store.put(
            user_id=pending.user_id,
            conv_id=pending.conv_id,
            message=pending.message,
            intent=pending.intent,
            urgency=pending.urgency,
            entities=pending.entities,
            action_name=pending.action_name,
            required_fields=pending.required_fields,
            risk_level=pending.risk_level,
            required_permission=pending.required_permission,
            confirmation_text=pending.confirmation_text,
        )

    @classmethod
    def extract_entities(cls, message: str) -> Dict[str, List[str]]:
        """本地提取决策必需字段，LLM 实体提取失败时仍能安全工作。"""
        text = message or ""
        entities: Dict[str, List[str]] = {}
        cls._add_match(
            entities,
            "ad_id",
            text,
            r"(?:广告|计划|campaign|ad)\s*(?:ID|编号|号|#|：|:)?\s*([A-Za-z0-9][A-Za-z0-9_-]{0,63})",
        )
        cls._add_match(
            entities,
            "asset_id",
            text,
            r"(?:素材|视频|图片|asset|creative)\s*(?:ID|编号|号|#|：|:)?\s*([A-Za-z0-9][A-Za-z0-9_-]{0,63})",
        )
        cls._add_match(
            entities,
            "amount",
            text,
            r"(?:¥|￥|\$)?\s*(\d+(?:\.\d+)?)\s*(?:元|块|人民币|美元|%|％)",
        )
        return entities

    @classmethod
    def _accept_single_field_answer(
        cls,
        message: str,
        missing_fields: List[str],
        extracted: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        if len(missing_fields) != 1 or extracted.get(missing_fields[0]):
            return extracted
        value = (message or "").strip().strip("“”\"'")
        if not value or len(value) > 500:
            return extracted
        if re.search(r"[？?]", value) or value in cls.AFFIRMATIVE:
            return extracted
        field_name = missing_fields[0]
        if field_name in {"ad_id", "asset_id"}:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}", value):
                extracted[field_name] = [value]
        elif field_name == "amount" and re.search(r"\d", value):
            extracted[field_name] = [value]
        return extracted

    @staticmethod
    def _add_match(
        entities: Dict[str, List[str]],
        field_name: str,
        text: str,
        pattern: str,
    ) -> None:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            value = match.group(1).strip()
            if value:
                entities[field_name] = [value]

    @staticmethod
    def _merge_entities(
        primary: Dict[str, List[str]],
        fallback: Dict[str, List[str]],
    ) -> Dict[str, List[str]]:
        merged: Dict[str, List[str]] = {}
        for source in (fallback, primary):
            for key, values in source.items():
                clean_values = [
                    str(value).strip()
                    for value in values
                    if str(value).strip()
                ]
                if clean_values:
                    merged[key] = clean_values
        return merged

    @staticmethod
    def _normalize(message: str) -> str:
        return re.sub(r"\s+", " ", (message or "").strip().lower())

    @staticmethod
    def _is_unrecognizable(message: str, intent: IntentCategory) -> bool:
        if not message or intent == IntentCategory.OTHER:
            return True
        meaningful = re.sub(r"[\W_]+", "", message, flags=re.UNICODE)
        if not meaningful:
            return True
        return len(message) >= 4 and len(set(message.replace(" ", ""))) <= 1
