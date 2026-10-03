"""Trusted ORM boundary for P2.4 learner memory.

Raw values are stored only after a server-owned, key-specific validation. The
caller cannot provide safety verdicts, owner identity, confirmation actor, or
mastery authority.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
import hashlib
import re
import unicodedata
from typing import Any, Mapping, Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from learning_apps.persistence.models import (
    LearnerMemoryDecisionRecord,
    LearnerMemoryRecord,
    LearningGoal,
    UserProfile,
)

from .learner_memory_policy_service import (
    MAX_RETENTION_DAYS,
    SAFE_SAFETY_SIGNALS,
    LearnerMemoryDecision,
    LearnerMemoryDecisionStatus,
    LearnerMemoryKind,
    LearnerMemoryLifecycle,
    LearnerMemoryScope,
    LearnerMemorySnapshot,
    LearnerMemorySource,
    evaluate_learner_memory_admission,
    stable_sha256,
)


_TEXT_KEYS = {
    "response_style": 160,
    "explanation_depth": 80,
    "preferred_language": 64,
    "practice_format": 120,
    "learning_pace": 80,
    "learning_strategy": 200,
    "goal_constraint": 240,
    "study_schedule_preference": 160,
}
_INTEGER_KEYS = {
    "time_budget_minutes": (1, 1440),
    "session_length_minutes": (5, 480),
    "sessions_per_week": (1, 21),
}
_DATE_KEYS = {"target_date"}
_KEY_KIND = {
    "goal_constraint": {LearnerMemoryKind.GOAL_CONSTRAINT},
    "time_budget_minutes": {LearnerMemoryKind.GOAL_CONSTRAINT},
    "session_length_minutes": {LearnerMemoryKind.GOAL_CONSTRAINT},
    "sessions_per_week": {LearnerMemoryKind.GOAL_CONSTRAINT},
    "target_date": {LearnerMemoryKind.GOAL_CONSTRAINT},
    "learning_strategy": {LearnerMemoryKind.LEARNING_STRATEGY_PREFERENCE},
    "study_schedule_preference": {LearnerMemoryKind.LEARNING_STRATEGY_PREFERENCE},
}
_SENSITIVE_TEXT_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\bBearer\s+\S+|\b(?:password|api[_ -]?key|secret|token)\s*[:=]"
    r"|\b\d{3}-\d{2}-\d{4}\b|\b(?:\d[ -]*?){13,19}\b|[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}"
    r"|\b(?:diagnos(?:is|ed)|adhd|autis(?:m|tic)|bipolar|depression|disability|mastery|misconception|assessment score)\b"
    r"|\b(?:conv|resp|response|file|vs|vector_store|thread|assistant)_[A-Za-z0-9_-]{6,})",
    re.IGNORECASE,
)
_PROMPT_CONTROL_RE = re.compile(
    r"(?:"
    r"\b(?:ignore|disregard|override|bypass|forget)\b.{0,48}\b(?:previous|prior|above|system|developer|instruction|prompt|rule|safety)\b"
    r"|\b(?:system|developer)\s+(?:prompt|message|instruction)\b"
    r"|\b(?:reveal|repeat|print|expose)\b.{0,32}\b(?:system|developer|hidden)\s+(?:prompt|message|instruction)\b"
    r"|\b(?:call|invoke|use)\b.{0,24}\b(?:tool|function)\b"
    r"|\b(?:you\s+are\s+now|act\s+as)\b"
    r"|<\s*/?\s*(?:system|developer|assistant|tool|instructions?)\b"
    r"|(?:忽略|无视|無視|绕过|繞過|覆盖|覆蓋).{0,24}(?:以上|之前|先前|系统|系統|开发者|開發者|指令|规则|規則|提示)"
    r"|(?:显示|顯示|泄露|洩露|输出|輸出).{0,24}(?:系统|系統|开发者|開發者).{0,12}(?:提示|消息|訊息|指令)"
    r"|(?:前の|以前の|システム|開発者).{0,24}(?:指示|命令|プロンプト).{0,12}(?:無視|忘れ|上書き)"
    r"|(?:ignora|ignorez|omita|anula|sobrescribe).{0,48}(?:instrucciones|instructions|sistema|système|desarrollador|développeur|prompt)"
    r")",
    re.IGNORECASE | re.DOTALL,
)


def _normalize_text_value(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value)
    # Strip format controls (zero-width joiners/spaces, bidi overrides, etc.)
    # before policy matching so they cannot split an instruction token.
    normalized = "".join(char for char in normalized if unicodedata.category(char) != "Cf")
    return re.sub(r"\s+", " ", normalized).strip()


def _normalize_value_payload(value_payload: Mapping[str, Any]) -> Mapping[str, Any]:
    if not isinstance(value_payload, Mapping) or set(value_payload) != {"value"}:
        return value_payload
    value = value_payload.get("value")
    if isinstance(value, str):
        return {"value": _normalize_text_value(value)}
    return {"value": value}


class LearnerMemoryScopeError(ValueError):
    """Raised when an owner/goal scope is not owned by the session user."""


def _owned_scope(username: str, learning_goal_id: int, *, lock: bool = False):
    users = UserProfile.objects
    goals = LearningGoal.objects
    if lock:
        users = users.select_for_update()
        goals = goals.select_for_update()
    user = users.filter(username=str(username or "")).first()
    goal = goals.filter(id=int(learning_goal_id), user=user).first() if user else None
    if not user or not goal:
        raise LearnerMemoryScopeError("learner_memory_scope_not_found")
    return user, goal


def _memory_active_scope_key(user_id: int, goal_id: int, kind: str, memory_key: str) -> str:
    digest = hashlib.sha256(
        f"learner-memory\0{user_id}\0{goal_id}\0{kind}\0{memory_key}".encode()
    ).hexdigest()
    return f"memory_scope_{digest}"


def _event_key(username: str, goal_id: int, action: str, idempotency_key: str) -> str:
    digest = stable_sha256(
        {
            "owner": str(username or ""),
            "goal_id": int(goal_id),
            "action": action,
            "idempotency_key": str(idempotency_key or ""),
        }
    )
    return f"evt_{digest[:48]}"


def _validate_value_schema(
    kind: LearnerMemoryKind | str,
    memory_key: str,
    value_payload: Mapping[str, Any],
    *,
    now,
) -> tuple[bool, str]:
    try:
        normalized_kind = LearnerMemoryKind(kind)
    except (TypeError, ValueError):
        return False, "memory_kind_invalid"
    key = str(memory_key or "").strip().casefold()
    if key not in _TEXT_KEYS and key not in _INTEGER_KEYS and key not in _DATE_KEYS:
        return False, "memory_key_not_supported"
    allowed_kinds = _KEY_KIND.get(
        key,
        {LearnerMemoryKind.EXPLICIT_PREFERENCE, LearnerMemoryKind.LEARNING_STRATEGY_PREFERENCE},
    )
    if normalized_kind not in allowed_kinds:
        return False, "memory_key_kind_mismatch"
    if not isinstance(value_payload, Mapping) or set(value_payload) != {"value"}:
        return False, "memory_value_schema_invalid"
    value = value_payload.get("value")
    if key in _TEXT_KEYS:
        if not isinstance(value, str):
            return False, "memory_value_schema_invalid"
        clean = value.strip()
        if not clean or len(clean) > _TEXT_KEYS[key] or any(ord(char) < 32 for char in clean):
            return False, "memory_value_schema_invalid"
        if _SENSITIVE_TEXT_RE.search(clean):
            return False, "memory_value_sensitive"
        if _PROMPT_CONTROL_RE.search(clean):
            return False, "memory_value_prompt_control"
        if key == "preferred_language" and not re.fullmatch(r"[A-Za-z][A-Za-z .'-]{1,63}", clean):
            return False, "memory_value_schema_invalid"
    elif key in _INTEGER_KEYS:
        low, high = _INTEGER_KEYS[key]
        if isinstance(value, bool) or not isinstance(value, int) or not low <= value <= high:
            return False, "memory_value_schema_invalid"
    elif key in _DATE_KEYS:
        if not isinstance(value, str):
            return False, "memory_value_schema_invalid"
        try:
            parsed = datetime.strptime(value, "%Y-%m-%d").date()
        except ValueError:
            return False, "memory_value_schema_invalid"
        if not getattr(now, "tzinfo", None) or parsed < now.date():
            return False, "memory_value_schema_invalid"
    return True, "ok"


def _snapshot(record: LearnerMemoryRecord) -> LearnerMemorySnapshot:
    return LearnerMemorySnapshot(
        memory_id=record.memory_id,
        scope_fingerprint=record.scope_fingerprint,
        kind=LearnerMemoryKind(record.kind),
        memory_key=record.memory_key,
        value_hash=record.value_hash,
        lifecycle=LearnerMemoryLifecycle(record.lifecycle),
        expires_at=record.expires_at,
    )


def _decision_from_metadata(metadata: Mapping[str, Any]) -> LearnerMemoryDecision:
    return LearnerMemoryDecision(
        status=LearnerMemoryDecisionStatus(metadata["status"]),
        reason_code=str(metadata["reason_code"]),
        policy_version=str(metadata["policy_version"]),
        kind=str(metadata["kind"]),
        source=str(metadata["source"]),
        lifecycle=str(metadata["lifecycle"]),
        memory_key=str(metadata["memory_key"]),
        memory_id=str(metadata["memory_id"]),
        owner_fingerprint=str(metadata["owner_fingerprint"]),
        goal_fingerprint=str(metadata["goal_fingerprint"]),
        scope_fingerprint=str(metadata["scope_fingerprint"]),
        value_hash=str(metadata["value_hash"]),
        request_hash=str(metadata["request_hash"]),
        idempotency_key=str(metadata["idempotency_key"]),
        confirmation_hash=str(metadata.get("confirmation_hash") or ""),
        expires_at=str(metadata.get("expires_at") or ""),
        retention_days=int(metadata.get("retention_days") or 0),
        conflict_ids=tuple(str(value) for value in metadata.get("conflict_ids") or ()),
        mastery_write_authorized=False,
    )


def _existing_event_decision(event_key: str) -> Optional[LearnerMemoryDecision]:
    row = LearnerMemoryDecisionRecord.objects.filter(event_key=event_key).first()
    if not row:
        return None
    return _decision_from_metadata(row.decision_metadata or {})


def _append_decision_record(
    *,
    user: UserProfile,
    goal: LearningGoal,
    memory: Optional[LearnerMemoryRecord],
    decision: LearnerMemoryDecision,
    event_key: Optional[str] = None,
) -> LearnerMemoryDecisionRecord:
    metadata = dict(decision.to_metadata())
    # This field is deliberately limited to hashes/local ids from the pure
    # policy. Raw values, usernames and provider identifiers never enter it.
    return LearnerMemoryDecisionRecord.objects.create(
        user=user,
        learning_goal=goal,
        memory=memory,
        event_key=event_key,
        idempotency_key=decision.idempotency_key,
        status=decision.status.value,
        reason_code=decision.reason_code,
        requested_lifecycle=decision.lifecycle,
        source=decision.source,
        policy_version=decision.policy_version,
        request_hash=decision.request_hash,
        decision_hash=stable_sha256(metadata),
        decision_metadata=metadata,
        mastery_write_authorized=False,
    )


def _evaluate(
    *,
    username: str,
    learning_goal_id: int,
    kind: LearnerMemoryKind | str,
    memory_key: str,
    value_payload: Mapping[str, Any],
    requested_lifecycle: LearnerMemoryLifecycle | str,
    source: LearnerMemorySource | str,
    expires_at,
    now,
    current_memory=None,
    existing_memories=(),
    explicit_confirmation: bool = False,
    confirmation_event_id: str = "",
) -> LearnerMemoryDecision:
    valid_schema, schema_reason = _validate_value_schema(
        kind,
        memory_key,
        value_payload,
        now=now,
    )
    decision = evaluate_learner_memory_admission(
        scope=LearnerMemoryScope(owner_id=username, goal_id=str(learning_goal_id)),
        kind=kind,
        memory_key=memory_key,
        value_payload=value_payload,
        requested_lifecycle=requested_lifecycle,
        source=source,
        expires_at=expires_at,
        now=now,
        safety_signals=tuple(SAFE_SAFETY_SIGNALS),
        explicit_user_confirmation=explicit_confirmation,
        confirmation_actor_id=username if explicit_confirmation else "",
        confirmation_event_id=confirmation_event_id if explicit_confirmation else "",
        current_memory=current_memory,
        existing_memories=existing_memories,
    )
    if not valid_schema:
        return replace(
            decision,
            status=LearnerMemoryDecisionStatus.BLOCKED,
            reason_code=schema_reason,
            conflict_ids=(),
            mastery_write_authorized=False,
        )
    return decision


def persist_learner_memory_decision(
    *,
    username: str,
    learning_goal_id: int,
    kind: LearnerMemoryKind | str,
    memory_key: str,
    value_payload: Mapping[str, Any],
    requested_lifecycle: LearnerMemoryLifecycle | str,
    source: LearnerMemorySource | str,
    expires_at,
    now=None,
    idempotency_key: Optional[str] = None,
) -> LearnerMemoryDecision:
    """Persist a proposal decision; direct activation has no confirmation provenance.

    `safety_signals`, actor identity, event ids and mastery flags are purposely
    absent from this API. Confirmation/revocation use dedicated session-owned
    operations below.
    """

    fixed_now = now or timezone.now()
    normalized_value_payload = _normalize_value_payload(value_payload)
    replay_event = (
        _event_key(username, learning_goal_id, "admission", idempotency_key)
        if idempotency_key
        else None
    )
    if replay_event:
        replay = _existing_event_decision(replay_event)
        if replay:
            return replay

    with transaction.atomic():
        user, goal = _owned_scope(username, learning_goal_id, lock=True)
        memories = list(
            LearnerMemoryRecord.objects.select_for_update().filter(
                user=user,
                learning_goal=goal,
                lifecycle__in=[
                    LearnerMemoryRecord.LIFECYCLE_ACTIVE,
                    LearnerMemoryRecord.LIFECYCLE_CONFLICTED,
                ],
                expires_at__gt=fixed_now,
            )
        )
        decision = _evaluate(
            username=username,
            learning_goal_id=goal.id,
            kind=kind,
            memory_key=memory_key,
            value_payload=normalized_value_payload,
            requested_lifecycle=requested_lifecycle,
            source=source,
            expires_at=expires_at,
            now=fixed_now,
            existing_memories=tuple(_snapshot(row) for row in memories),
        )
        existing_decision = LearnerMemoryDecisionRecord.objects.filter(
            idempotency_key=decision.idempotency_key
        ).first()
        if existing_decision:
            return _decision_from_metadata(existing_decision.decision_metadata or {})

        memory = None
        if decision.accepted:
            memory, created = LearnerMemoryRecord.objects.get_or_create(
                memory_id=decision.memory_id,
                defaults={
                    "user": user,
                    "learning_goal": goal,
                    "kind": decision.kind,
                    "memory_key": decision.memory_key,
                    "value_payload": dict(normalized_value_payload),
                    "value_hash": decision.value_hash,
                    "scope_fingerprint": decision.scope_fingerprint,
                    "active_scope_key": None,
                    "lifecycle": decision.lifecycle,
                    "conflict_memory_ids": list(decision.conflict_ids),
                    "source": decision.source,
                    "policy_version": decision.policy_version,
                    "expires_at": datetime.fromisoformat(decision.expires_at.replace("Z", "+00:00")),
                    "mastery_write_authorized": False,
                },
            )
            if not created and (
                memory.user_id != user.user_id
                or memory.learning_goal_id != goal.id
                or memory.value_hash != decision.value_hash
            ):
                raise LearnerMemoryScopeError("memory_identity_collision")
        try:
            _append_decision_record(
                user=user,
                goal=goal,
                memory=memory,
                decision=decision,
                event_key=replay_event,
            )
        except IntegrityError:
            if replay_event:
                replay = _existing_event_decision(replay_event)
                if replay:
                    return replay
            raise
        return decision


def propose_learner_memory(
    *,
    username: str,
    learning_goal_id: int,
    kind: LearnerMemoryKind | str,
    memory_key: str,
    value_payload: Mapping[str, Any],
    source: LearnerMemorySource | str = LearnerMemorySource.EXPLICIT_USER_STATEMENT,
    expires_at=None,
    now=None,
    idempotency_key: Optional[str] = None,
) -> LearnerMemoryDecision:
    fixed_now = now or timezone.now()
    return persist_learner_memory_decision(
        username=username,
        learning_goal_id=learning_goal_id,
        kind=kind,
        memory_key=memory_key,
        value_payload=value_payload,
        requested_lifecycle=LearnerMemoryLifecycle.PROPOSED,
        source=source,
        expires_at=expires_at or fixed_now + timedelta(days=30),
        now=fixed_now,
        idempotency_key=idempotency_key,
    )


def confirm_learner_memory(
    *,
    username: str,
    learning_goal_id: int,
    memory_id: str,
    idempotency_key: Optional[str] = None,
    expires_at=None,
    now=None,
) -> LearnerMemoryDecision:
    fixed_now = now or timezone.now()
    token = idempotency_key or f"confirm:{memory_id}"
    event = _event_key(username, learning_goal_id, "confirm", token)
    replay = _existing_event_decision(event)
    if replay:
        return replay
    with transaction.atomic():
        user, goal = _owned_scope(username, learning_goal_id, lock=True)
        target = LearnerMemoryRecord.objects.select_for_update().filter(
            memory_id=str(memory_id), user=user, learning_goal=goal
        ).first()
        if not target:
            raise LearnerMemoryScopeError("learner_memory_not_found")
        if target.expires_at <= fixed_now:
            raise LearnerMemoryScopeError("learner_memory_expired")
        other_records = list(
            LearnerMemoryRecord.objects.select_for_update()
            .filter(
                user=user,
                learning_goal=goal,
                lifecycle__in=[
                    LearnerMemoryRecord.LIFECYCLE_ACTIVE,
                    LearnerMemoryRecord.LIFECYCLE_CONFLICTED,
                ],
                expires_at__gt=fixed_now,
            )
            .exclude(memory_id=target.memory_id)
        )
        kind = LearnerMemoryKind(target.kind)
        effective_expiry = expires_at or min(
            target.expires_at,
            fixed_now + timedelta(days=MAX_RETENTION_DAYS[kind]),
        )
        decision = _evaluate(
            username=username,
            learning_goal_id=goal.id,
            kind=target.kind,
            memory_key=target.memory_key,
            value_payload=target.value_payload,
            requested_lifecycle=LearnerMemoryLifecycle.ACTIVE,
            source=LearnerMemorySource.EXPLICIT_USER_CONFIRMATION,
            expires_at=effective_expiry,
            now=fixed_now,
            current_memory=_snapshot(target),
            existing_memories=tuple(_snapshot(row) for row in other_records),
            explicit_confirmation=True,
            confirmation_event_id=event,
        )
        existing = LearnerMemoryDecisionRecord.objects.filter(
            idempotency_key=decision.idempotency_key
        ).first()
        if existing:
            return _decision_from_metadata(existing.decision_metadata or {})
        if decision.accepted:
            target.lifecycle = decision.lifecycle
            target.active_scope_key = (
                _memory_active_scope_key(user.user_id, goal.id, target.kind, target.memory_key)
                if decision.lifecycle == LearnerMemoryLifecycle.ACTIVE.value
                else None
            )
            target.conflict_memory_ids = list(decision.conflict_ids)
            target.source = decision.source
            target.expires_at = datetime.fromisoformat(decision.expires_at.replace("Z", "+00:00"))
            target.confirmed_at = fixed_now
            target.save(
                update_fields=[
                    "lifecycle",
                    "active_scope_key",
                    "conflict_memory_ids",
                    "source",
                    "expires_at",
                    "confirmed_at",
                    "updated_at",
                ]
            )
        _append_decision_record(user=user, goal=goal, memory=target, decision=decision, event_key=event)
        return decision


def revoke_learner_memory(
    *,
    username: str,
    learning_goal_id: int,
    memory_id: str,
    idempotency_key: Optional[str] = None,
    now=None,
) -> LearnerMemoryDecision:
    fixed_now = now or timezone.now()
    token = idempotency_key or f"revoke:{memory_id}"
    event = _event_key(username, learning_goal_id, "revoke", token)
    replay = _existing_event_decision(event)
    if replay:
        return replay
    with transaction.atomic():
        user, goal = _owned_scope(username, learning_goal_id, lock=True)
        target = LearnerMemoryRecord.objects.select_for_update().filter(
            memory_id=str(memory_id), user=user, learning_goal=goal
        ).first()
        if not target:
            raise LearnerMemoryScopeError("learner_memory_not_found")
        effective_expiry = fixed_now + timedelta(
            days=MAX_RETENTION_DAYS[LearnerMemoryLifecycle.REVOKED]
        )
        decision = _evaluate(
            username=username,
            learning_goal_id=goal.id,
            kind=target.kind,
            memory_key=target.memory_key,
            value_payload=target.value_payload,
            requested_lifecycle=LearnerMemoryLifecycle.REVOKED,
            source=LearnerMemorySource.EXPLICIT_USER_REVOCATION,
            expires_at=effective_expiry,
            now=fixed_now,
            current_memory=_snapshot(target),
            existing_memories=(),
            explicit_confirmation=True,
            confirmation_event_id=event,
        )
        existing = LearnerMemoryDecisionRecord.objects.filter(
            idempotency_key=decision.idempotency_key
        ).first()
        if existing:
            return _decision_from_metadata(existing.decision_metadata or {})
        if decision.accepted:
            target.lifecycle = LearnerMemoryRecord.LIFECYCLE_REVOKED
            target.active_scope_key = None
            target.source = decision.source
            target.expires_at = effective_expiry
            target.revoked_at = fixed_now
            target.save(
                update_fields=[
                    "lifecycle",
                    "active_scope_key",
                    "source",
                    "expires_at",
                    "revoked_at",
                    "updated_at",
                ]
            )
        _append_decision_record(user=user, goal=goal, memory=target, decision=decision, event_key=event)
        return decision


def expire_due_memories(*, limit: int = 500, now=None) -> int:
    fixed_now = now or timezone.now()
    ids = list(
        LearnerMemoryRecord.objects.filter(
            expires_at__lte=fixed_now,
            lifecycle__in=[
                LearnerMemoryRecord.LIFECYCLE_PROPOSED,
                LearnerMemoryRecord.LIFECYCLE_ACTIVE,
                LearnerMemoryRecord.LIFECYCLE_CONFLICTED,
                LearnerMemoryRecord.LIFECYCLE_REVOKED,
            ],
        )
        .order_by("expires_at", "memory_id")
        .values_list("memory_id", flat=True)[: max(1, int(limit))]
    )
    expired = 0
    for memory_id in ids:
        with transaction.atomic():
            target = LearnerMemoryRecord.objects.select_for_update().select_related(
                "user", "learning_goal"
            ).filter(memory_id=memory_id, expires_at__lte=fixed_now).first()
            if not target or target.lifecycle == LearnerMemoryRecord.LIFECYCLE_EXPIRED:
                continue
            event = _event_key(
                target.user.username,
                target.learning_goal_id,
                "expire",
                f"{target.memory_id}:{target.expires_at.isoformat()}",
            )
            if _existing_event_decision(event):
                continue
            decision = _evaluate(
                username=target.user.username,
                learning_goal_id=target.learning_goal_id,
                kind=target.kind,
                memory_key=target.memory_key,
                value_payload=target.value_payload,
                requested_lifecycle=LearnerMemoryLifecycle.EXPIRED,
                source=LearnerMemorySource.RETENTION_EVALUATOR,
                expires_at=target.expires_at,
                now=fixed_now,
                current_memory=_snapshot(target),
                existing_memories=(),
            )
            if not decision.accepted:
                _append_decision_record(
                    user=target.user,
                    goal=target.learning_goal,
                    memory=target,
                    decision=decision,
                    event_key=event,
                )
                continue
            target.lifecycle = LearnerMemoryRecord.LIFECYCLE_EXPIRED
            target.active_scope_key = None
            target.save(update_fields=["lifecycle", "active_scope_key", "updated_at"])
            _append_decision_record(
                user=target.user,
                goal=target.learning_goal,
                memory=target,
                decision=decision,
                event_key=event,
            )
            expired += 1
    return expired


def _memory_to_public_dict(record: LearnerMemoryRecord) -> dict:
    return {
        "memory_id": record.memory_id,
        "kind": record.kind,
        "memory_key": record.memory_key,
        "value": record.value_payload,
        "lifecycle": record.lifecycle,
        "conflict_memory_ids": list(record.conflict_memory_ids or []),
        "expires_at": record.expires_at.isoformat(),
        "created_at": record.created_at.isoformat(),
        "updated_at": record.updated_at.isoformat(),
    }


def list_learner_memories(
    username: str,
    learning_goal_id: int,
    *,
    include_pending: bool = True,
    now=None,
) -> list[dict]:
    fixed_now = now or timezone.now()
    user, goal = _owned_scope(username, learning_goal_id)
    lifecycles = [LearnerMemoryRecord.LIFECYCLE_ACTIVE]
    if include_pending:
        lifecycles.extend(
            [
                LearnerMemoryRecord.LIFECYCLE_PROPOSED,
                LearnerMemoryRecord.LIFECYCLE_CONFLICTED,
            ]
        )
    rows = LearnerMemoryRecord.objects.filter(
        user=user,
        learning_goal=goal,
        lifecycle__in=lifecycles,
        expires_at__gt=fixed_now,
    ).order_by("memory_key", "created_at", "memory_id")
    return [_memory_to_public_dict(row) for row in rows]


def list_active_learner_memories(
    username: str,
    learning_goal_id: int,
    *,
    now=None,
) -> list[dict]:
    return list_learner_memories(
        username,
        learning_goal_id,
        include_pending=False,
        now=now,
    )


__all__ = [
    "LearnerMemoryScopeError",
    "confirm_learner_memory",
    "expire_due_memories",
    "list_active_learner_memories",
    "list_learner_memories",
    "persist_learner_memory_decision",
    "propose_learner_memory",
    "revoke_learner_memory",
]
