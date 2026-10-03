"""Pure, fail-closed policy for durable learner memory.

Conversation history, retrieval results, compaction summaries, and model
inferences may propose memory candidates, but they can never activate them.
Only an explicit confirmation event from the scoped owner can create active
memory.  This module has no Django, provider, persistence, or mastery imports.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import hashlib
import json
import math
import re
from typing import Any, Mapping, Optional, Sequence, Tuple


LEARNER_MEMORY_POLICY_VERSION = "p2.4-learner-memory-v1"


class LearnerMemoryKind(str, Enum):
    EXPLICIT_PREFERENCE = "explicit_preference"
    GOAL_CONSTRAINT = "goal_constraint"
    LEARNING_STRATEGY_PREFERENCE = "learning_strategy_preference"


class LearnerMemoryLifecycle(str, Enum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    CONFLICTED = "conflicted"
    REVOKED = "revoked"
    EXPIRED = "expired"


class LearnerMemoryScopeType(str, Enum):
    GOAL = "goal"
    GLOBAL = "global"


class LearnerMemorySource(str, Enum):
    EXPLICIT_USER_STATEMENT = "explicit_user_statement"
    EXPLICIT_USER_CONFIRMATION = "explicit_user_confirmation"
    EXPLICIT_USER_REVOCATION = "explicit_user_revocation"
    RETRIEVAL = "retrieval"
    CONVERSATION_COMPACTION = "conversation_compaction"
    MODEL_INFERENCE = "model_inference"
    CONFLICT_DETECTOR = "conflict_detector"
    RETENTION_EVALUATOR = "retention_evaluator"


class LearnerMemoryDecisionStatus(str, Enum):
    ACCEPTED = "accepted"
    BLOCKED = "blocked"


SAFE_SAFETY_SIGNALS = frozenset(
    {"non_sensitive_preference", "non_diagnostic", "non_mastery_claim"}
)
FORBIDDEN_SAFETY_SIGNALS = frozenset(
    {
        "sensitive_personal_data",
        "medical_or_psychological_diagnosis",
        "disability_diagnosis",
        "mastery_claim",
        "misconception_claim",
        "assessment_score",
        "credential_or_secret",
        "provider_identifier",
        "precise_location",
        "financial_information",
        "biometric_information",
    }
)

GLOBAL_MEMORY_KEYS = frozenset(
    {
        "response_style",
        "explanation_depth",
        "preferred_language",
        "practice_format",
        "learning_pace",
        "learning_strategy",
    }
)

MAX_RETENTION_DAYS = {
    LearnerMemoryLifecycle.PROPOSED: 30,
    LearnerMemoryLifecycle.CONFLICTED: 30,
    LearnerMemoryLifecycle.REVOKED: 90,
    LearnerMemoryKind.EXPLICIT_PREFERENCE: 365,
    LearnerMemoryKind.GOAL_CONSTRAINT: 180,
    LearnerMemoryKind.LEARNING_STRATEGY_PREFERENCE: 180,
}

_MEMORY_KEY_RE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
_LOCAL_EVENT_ID_RE = re.compile(r"^(?:evt|event)_[a-zA-Z0-9][a-zA-Z0-9_.:-]{5,127}$")
_PROVIDER_OR_SECRET_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\b(?:conv|resp|response|file|vs|vector_store|thread|assistant)_[A-Za-z0-9_-]{6,}|\bBearer\s+[A-Za-z0-9._~+/=-]{8,}|(?:password|api[_ -]?key|secret|access[_ -]?token)\s*[:=]\s*\S+)",
    re.IGNORECASE,
)
_FORBIDDEN_FIELD_FRAGMENTS = frozenset(
    {
        "mastery",
        "diagnosis",
        "diagnostic",
        "misconception",
        "assessment_score",
        "password",
        "api_key",
        "secret",
        "access_token",
        "refresh_token",
        "provider_id",
        "conversation_id",
        "response_id",
        "file_id",
        "vector_store_id",
    }
)


@dataclass(frozen=True)
class LearnerMemoryScope:
    owner_id: str
    goal_id: str = ""
    scope_type: LearnerMemoryScopeType = LearnerMemoryScopeType.GOAL

    def __post_init__(self) -> None:
        owner_id = str(self.owner_id or "").strip()
        goal_id = str(self.goal_id or "").strip()
        if not owner_id:
            raise ValueError("owner_id is required")
        try:
            scope_type = LearnerMemoryScopeType(self.scope_type)
        except (TypeError, ValueError) as exc:
            raise ValueError("scope_type must be goal or global") from exc
        if scope_type is LearnerMemoryScopeType.GOAL and not goal_id:
            raise ValueError("goal scope requires goal_id")
        if scope_type is LearnerMemoryScopeType.GLOBAL and goal_id:
            raise ValueError("global scope cannot carry goal_id")
        object.__setattr__(self, "owner_id", owner_id)
        object.__setattr__(self, "goal_id", goal_id)
        object.__setattr__(self, "scope_type", scope_type)

    @property
    def owner_fingerprint(self) -> str:
        return hashlib.sha256(f"owner\0{self.owner_id}".encode()).hexdigest()

    @property
    def goal_fingerprint(self) -> str:
        marker = self.goal_id if self.scope_type is LearnerMemoryScopeType.GOAL else "global"
        return hashlib.sha256(f"goal\0{marker}".encode()).hexdigest()

    @property
    def fingerprint(self) -> str:
        payload = f"{self.owner_id}\0{self.scope_type.value}\0{self.goal_id or 'global'}"
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class LearnerMemorySnapshot:
    """Provider-neutral current-state input used for transitions and conflicts."""

    memory_id: str
    scope_fingerprint: str
    kind: LearnerMemoryKind
    memory_key: str
    value_hash: str
    lifecycle: LearnerMemoryLifecycle
    expires_at: datetime

    def __post_init__(self) -> None:
        if not re.fullmatch(r"mem_[0-9a-f]{32}", str(self.memory_id or "")):
            raise ValueError("memory_id must be a local opaque memory id")
        if not re.fullmatch(r"[0-9a-f]{64}", str(self.scope_fingerprint or "")):
            raise ValueError("scope_fingerprint must be SHA-256")
        try:
            object.__setattr__(self, "kind", LearnerMemoryKind(self.kind))
            object.__setattr__(self, "lifecycle", LearnerMemoryLifecycle(self.lifecycle))
        except (TypeError, ValueError) as exc:
            raise ValueError("snapshot kind or lifecycle is invalid") from exc
        if not _MEMORY_KEY_RE.fullmatch(str(self.memory_key or "")):
            raise ValueError("snapshot memory_key is invalid")
        if not re.fullmatch(r"[0-9a-f]{64}", str(self.value_hash or "")):
            raise ValueError("snapshot value_hash must be SHA-256")
        if not _aware_datetime(self.expires_at):
            raise ValueError("snapshot expires_at must be timezone-aware")


@dataclass(frozen=True)
class LearnerMemoryDecision:
    status: LearnerMemoryDecisionStatus
    reason_code: str
    policy_version: str
    kind: str
    source: str
    lifecycle: str
    memory_key: str
    memory_id: str
    owner_fingerprint: str
    goal_fingerprint: str
    scope_fingerprint: str
    value_hash: str
    request_hash: str
    idempotency_key: str
    confirmation_hash: str
    expires_at: str
    retention_days: int
    conflict_ids: Tuple[str, ...]
    mastery_write_authorized: bool = False

    def __post_init__(self) -> None:
        if self.mastery_write_authorized:
            raise ValueError("learner memory never authorizes mastery writes")

    @property
    def accepted(self) -> bool:
        return self.status is LearnerMemoryDecisionStatus.ACCEPTED

    def to_metadata(self) -> Mapping[str, Any]:
        """Return hashes and local ids only; raw values and provider ids are absent."""

        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "kind": self.kind,
            "source": self.source,
            "lifecycle": self.lifecycle,
            "memory_key": self.memory_key,
            "memory_id": self.memory_id,
            "owner_fingerprint": self.owner_fingerprint,
            "goal_fingerprint": self.goal_fingerprint,
            "scope_fingerprint": self.scope_fingerprint,
            "value_hash": self.value_hash,
            "request_hash": self.request_hash,
            "idempotency_key": self.idempotency_key,
            "confirmation_hash": self.confirmation_hash,
            "expires_at": self.expires_at,
            "retention_days": self.retention_days,
            "conflict_ids": list(self.conflict_ids),
            "mastery_write_authorized": False,
        }


@dataclass(frozen=True)
class MemoryRetentionEvaluation:
    status: LearnerMemoryDecisionStatus
    lifecycle: LearnerMemoryLifecycle
    reason_code: str
    evaluated_at: str
    evaluation_hash: str
    mastery_write_authorized: bool = False

    def __post_init__(self) -> None:
        if self.mastery_write_authorized:
            raise ValueError("retention evaluation never authorizes mastery writes")

    @property
    def accepted(self) -> bool:
        return self.status is LearnerMemoryDecisionStatus.ACCEPTED


def _aware_datetime(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _utc(value: datetime) -> datetime:
    return value.astimezone(timezone.utc)


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        if not _aware_datetime(value):
            return {"__invalid_naive_datetime__": value.isoformat()}
        return _utc(value).isoformat().replace("+00:00", "Z")
    if isinstance(value, float) and not math.isfinite(value):
        return {"__non_finite_float__": repr(value)}
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_json_safe(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "")


def _normalize_scope(scope: Any) -> Tuple[Optional[LearnerMemoryScope], str]:
    if isinstance(scope, LearnerMemoryScope):
        return scope, "ok"
    if not isinstance(scope, Mapping):
        return None, "scope_invalid"
    try:
        return (
            LearnerMemoryScope(
                owner_id=scope.get("owner_id", ""),
                goal_id=scope.get("goal_id", ""),
                scope_type=scope.get("scope_type", ""),
            ),
            "ok",
        )
    except (TypeError, ValueError):
        return None, "scope_invalid"


def _walk_payload(value: Any, *, path: str = "") -> Tuple[str, ...]:
    violations = []
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().casefold().replace("-", "_")
            field_path = f"{path}.{normalized_key}" if path else normalized_key
            if any(fragment in normalized_key for fragment in _FORBIDDEN_FIELD_FRAGMENTS):
                violations.append(f"forbidden_field:{field_path}")
            violations.extend(_walk_payload(item, path=field_path))
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        for index, item in enumerate(value):
            violations.extend(_walk_payload(item, path=f"{path}[{index}]"))
    elif isinstance(value, str) and _PROVIDER_OR_SECRET_RE.search(value):
        violations.append(f"secret_or_provider_id:{path or 'value'}")
    return tuple(violations)


def _normalize_snapshot(value: Any) -> Optional[LearnerMemorySnapshot]:
    if isinstance(value, LearnerMemorySnapshot):
        return value
    if not isinstance(value, Mapping):
        return None
    try:
        return LearnerMemorySnapshot(
            memory_id=value.get("memory_id", ""),
            scope_fingerprint=value.get("scope_fingerprint", ""),
            kind=value.get("kind", ""),
            memory_key=value.get("memory_key", ""),
            value_hash=value.get("value_hash", ""),
            lifecycle=value.get("lifecycle", ""),
            expires_at=value.get("expires_at"),
        )
    except (TypeError, ValueError):
        return None


def detect_memory_conflicts(
    *,
    scope_fingerprint: str,
    kind: LearnerMemoryKind,
    memory_key: str,
    value_hash: str,
    existing_memories: Sequence[Any],
    now: datetime,
) -> Tuple[Tuple[str, ...], str]:
    """Return stable conflicting local memory ids or a fail-closed reason."""

    conflicts = []
    for raw_snapshot in existing_memories:
        snapshot = _normalize_snapshot(raw_snapshot)
        if snapshot is None:
            return (), "existing_memory_invalid"
        if snapshot.scope_fingerprint != scope_fingerprint:
            return (), "existing_scope_mismatch"
        if snapshot.lifecycle in {
            LearnerMemoryLifecycle.REVOKED,
            LearnerMemoryLifecycle.EXPIRED,
        } or _utc(snapshot.expires_at) <= _utc(now):
            continue
        if (
            snapshot.kind is kind
            and snapshot.memory_key == memory_key
            and snapshot.value_hash != value_hash
            and snapshot.lifecycle
            in {LearnerMemoryLifecycle.ACTIVE, LearnerMemoryLifecycle.CONFLICTED}
        ):
            conflicts.append(snapshot.memory_id)
    return tuple(sorted(set(conflicts))), "ok"


def evaluate_memory_retention(
    snapshot: LearnerMemorySnapshot | Mapping[str, Any],
    *,
    now: datetime,
) -> MemoryRetentionEvaluation:
    """Evaluate expiry deterministically without mutating the snapshot."""

    normalized = _normalize_snapshot(snapshot)
    if normalized is None or not _aware_datetime(now):
        return MemoryRetentionEvaluation(
            status=LearnerMemoryDecisionStatus.BLOCKED,
            lifecycle=LearnerMemoryLifecycle.EXPIRED,
            reason_code="retention_input_invalid",
            evaluated_at="",
            evaluation_hash=stable_sha256({"snapshot": snapshot, "now": now}),
        )
    evaluated_at = _utc(now).isoformat().replace("+00:00", "Z")
    if normalized.lifecycle in {
        LearnerMemoryLifecycle.REVOKED,
        LearnerMemoryLifecycle.EXPIRED,
    }:
        lifecycle = normalized.lifecycle
        reason = "terminal_lifecycle"
    elif _utc(now) >= _utc(normalized.expires_at):
        lifecycle = LearnerMemoryLifecycle.EXPIRED
        reason = "retention_expired"
    else:
        lifecycle = normalized.lifecycle
        reason = "retained"
    return MemoryRetentionEvaluation(
        status=LearnerMemoryDecisionStatus.ACCEPTED,
        lifecycle=lifecycle,
        reason_code=reason,
        evaluated_at=evaluated_at,
        evaluation_hash=stable_sha256(
            {
                "policy_version": LEARNER_MEMORY_POLICY_VERSION,
                "memory_id": normalized.memory_id,
                "scope_fingerprint": normalized.scope_fingerprint,
                "lifecycle": lifecycle,
                "expires_at": normalized.expires_at,
                "evaluated_at": evaluated_at,
            }
        ),
    )


def evaluate_learner_memory_admission(
    *,
    scope: LearnerMemoryScope | Mapping[str, Any],
    kind: LearnerMemoryKind | str,
    memory_key: str,
    value_payload: Mapping[str, Any],
    requested_lifecycle: LearnerMemoryLifecycle | str,
    source: LearnerMemorySource | str,
    expires_at: datetime,
    now: datetime,
    safety_signals: Sequence[str],
    explicit_user_confirmation: bool = False,
    confirmation_actor_id: str = "",
    confirmation_event_id: str = "",
    current_memory: Optional[LearnerMemorySnapshot | Mapping[str, Any]] = None,
    existing_memories: Sequence[Any] = (),
    policy_version: str = LEARNER_MEMORY_POLICY_VERSION,
) -> LearnerMemoryDecision:
    """Evaluate a proposed lifecycle transition without persisting memory."""

    normalized_scope, scope_reason = _normalize_scope(scope)
    try:
        normalized_kind = LearnerMemoryKind(kind)
    except (TypeError, ValueError):
        normalized_kind = None
    try:
        normalized_lifecycle = LearnerMemoryLifecycle(requested_lifecycle)
    except (TypeError, ValueError):
        normalized_lifecycle = None
    try:
        normalized_source = LearnerMemorySource(source)
    except (TypeError, ValueError):
        normalized_source = None
    normalized_key = str(memory_key or "").strip().casefold()
    value_hash = stable_sha256(value_payload)
    confirmation_payload = {
        "confirmed": bool(explicit_user_confirmation),
        "actor_id": str(confirmation_actor_id or "").strip(),
        "event_id": str(confirmation_event_id or "").strip(),
    }
    confirmation_hash = (
        stable_sha256(confirmation_payload)
        if explicit_user_confirmation or confirmation_actor_id or confirmation_event_id
        else ""
    )
    safe_expires_at = (
        _utc(expires_at).isoformat().replace("+00:00", "Z")
        if _aware_datetime(expires_at)
        else ""
    )
    request_payload = {
        "policy_version": str(policy_version or "").strip(),
        "scope_fingerprint": normalized_scope.fingerprint if normalized_scope else "",
        "kind": normalized_kind.value if normalized_kind else _enum_value(kind),
        "memory_key": normalized_key,
        "value_hash": value_hash,
        "requested_lifecycle": (
            normalized_lifecycle.value if normalized_lifecycle else _enum_value(requested_lifecycle)
        ),
        "source": normalized_source.value if normalized_source else _enum_value(source),
        "expires_at": safe_expires_at,
        "evaluated_at": (
            _utc(now).isoformat().replace("+00:00", "Z")
            if _aware_datetime(now)
            else ""
        ),
        "confirmation_hash": confirmation_hash,
        "current_memory_hash": stable_sha256(current_memory),
        "existing_state_hash": stable_sha256(existing_memories),
    }
    request_hash = stable_sha256(request_payload)
    memory_identity = {
        "scope_fingerprint": normalized_scope.fingerprint if normalized_scope else "",
        "kind": normalized_kind.value if normalized_kind else "",
        "memory_key": normalized_key,
        "value_hash": value_hash,
    }
    memory_id = f"mem_{stable_sha256(memory_identity)[:32]}"
    idempotency_key = f"lmp_{stable_sha256({'policy': policy_version, 'request': request_hash})[:32]}"

    def decision(
        status: LearnerMemoryDecisionStatus,
        reason: str,
        *,
        lifecycle: Optional[LearnerMemoryLifecycle] = normalized_lifecycle,
        conflicts: Tuple[str, ...] = (),
        retention_days: int = 0,
        effective_expires_at: str = safe_expires_at,
    ) -> LearnerMemoryDecision:
        return LearnerMemoryDecision(
            status=status,
            reason_code=reason,
            policy_version=str(policy_version or "").strip(),
            kind=normalized_kind.value if normalized_kind else _enum_value(kind),
            source=normalized_source.value if normalized_source else _enum_value(source),
            lifecycle=lifecycle.value if lifecycle else _enum_value(requested_lifecycle),
            memory_key=normalized_key,
            memory_id=memory_id,
            owner_fingerprint=(normalized_scope.owner_fingerprint if normalized_scope else ""),
            goal_fingerprint=(normalized_scope.goal_fingerprint if normalized_scope else ""),
            scope_fingerprint=(normalized_scope.fingerprint if normalized_scope else ""),
            value_hash=value_hash,
            request_hash=request_hash,
            idempotency_key=idempotency_key,
            confirmation_hash=confirmation_hash,
            expires_at=effective_expires_at,
            retention_days=retention_days,
            conflict_ids=conflicts,
        )

    if str(policy_version or "").strip() != LEARNER_MEMORY_POLICY_VERSION:
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "policy_version_invalid")
    if normalized_scope is None:
        return decision(LearnerMemoryDecisionStatus.BLOCKED, scope_reason)
    if normalized_kind is None:
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "kind_unknown")
    if normalized_source is None:
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "source_unknown")
    if normalized_lifecycle is None:
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "lifecycle_unknown")
    if not _MEMORY_KEY_RE.fullmatch(normalized_key):
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "memory_key_invalid")
    if not isinstance(value_payload, Mapping) or not value_payload:
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "value_payload_invalid")
    payload_violations = _walk_payload(value_payload)
    if payload_violations:
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "forbidden_value_content")
    if not _aware_datetime(now) or not _aware_datetime(expires_at):
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "expiry_invalid")

    if not isinstance(safety_signals, Sequence) or isinstance(safety_signals, (str, bytes)):
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "safety_signals_invalid")
    signal_set = frozenset(str(item).strip() for item in safety_signals if str(item).strip())
    if signal_set & FORBIDDEN_SAFETY_SIGNALS:
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "forbidden_memory_claim")
    if not signal_set.issubset(SAFE_SAFETY_SIGNALS | FORBIDDEN_SAFETY_SIGNALS):
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "safety_signal_unknown")
    if not SAFE_SAFETY_SIGNALS.issubset(signal_set):
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "safety_boundary_incomplete")

    if normalized_scope.scope_type is LearnerMemoryScopeType.GLOBAL:
        if normalized_kind is LearnerMemoryKind.GOAL_CONSTRAINT:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "goal_constraint_requires_goal_scope")
        if normalized_key not in GLOBAL_MEMORY_KEYS:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "global_memory_key_forbidden")
        if normalized_lifecycle is LearnerMemoryLifecycle.PROPOSED and normalized_source is not LearnerMemorySource.EXPLICIT_USER_STATEMENT:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "global_inference_forbidden")

    current = None
    if current_memory is not None:
        current = _normalize_snapshot(current_memory)
        if current is None:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "current_memory_invalid")
        if current.scope_fingerprint != normalized_scope.fingerprint:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "current_scope_mismatch")
        if (
            current.kind is not normalized_kind
            or current.memory_key != normalized_key
            or current.value_hash != value_hash
            or current.memory_id != memory_id
        ):
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "current_identity_mismatch")

    proposal_sources = {
        LearnerMemorySource.EXPLICIT_USER_STATEMENT,
        LearnerMemorySource.RETRIEVAL,
        LearnerMemorySource.CONVERSATION_COMPACTION,
        LearnerMemorySource.MODEL_INFERENCE,
    }
    if normalized_lifecycle is LearnerMemoryLifecycle.PROPOSED:
        if normalized_source not in proposal_sources:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "source_cannot_propose")
        if explicit_user_confirmation or confirmation_actor_id or confirmation_event_id:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "confirmation_not_applicable")
    elif normalized_lifecycle is LearnerMemoryLifecycle.ACTIVE:
        if normalized_source is not LearnerMemorySource.EXPLICIT_USER_CONFIRMATION:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "source_cannot_activate")
        if not explicit_user_confirmation:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "confirmation_missing")
        if str(confirmation_actor_id or "").strip() != normalized_scope.owner_id:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "confirmation_actor_mismatch")
        if not _LOCAL_EVENT_ID_RE.fullmatch(str(confirmation_event_id or "").strip()):
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "confirmation_event_invalid")
        if current and current.lifecycle not in {
            LearnerMemoryLifecycle.PROPOSED,
            LearnerMemoryLifecycle.CONFLICTED,
            LearnerMemoryLifecycle.ACTIVE,
        }:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "activation_transition_invalid")
    elif normalized_lifecycle is LearnerMemoryLifecycle.REVOKED:
        if normalized_source is not LearnerMemorySource.EXPLICIT_USER_REVOCATION:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "source_cannot_revoke")
        if current is None:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "revocation_target_missing")
        if not explicit_user_confirmation:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "revocation_confirmation_missing")
        if str(confirmation_actor_id or "").strip() != normalized_scope.owner_id:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "revocation_actor_mismatch")
        if not _LOCAL_EVENT_ID_RE.fullmatch(str(confirmation_event_id or "").strip()):
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "revocation_event_invalid")
        if current.lifecycle in {
            LearnerMemoryLifecycle.REVOKED,
            LearnerMemoryLifecycle.EXPIRED,
        }:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "revocation_transition_invalid")
    elif normalized_lifecycle is LearnerMemoryLifecycle.EXPIRED:
        if normalized_source is not LearnerMemorySource.RETENTION_EVALUATOR:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "source_cannot_expire")
        if current is None:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "expiry_target_missing")
        if _utc(now) < _utc(current.expires_at):
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "expiry_not_due")
        if _utc(expires_at) != _utc(current.expires_at):
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "expiry_timestamp_mismatch")
    elif normalized_lifecycle is LearnerMemoryLifecycle.CONFLICTED:
        if normalized_source is not LearnerMemorySource.CONFLICT_DETECTOR:
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "source_cannot_mark_conflict")

    if normalized_lifecycle is LearnerMemoryLifecycle.EXPIRED:
        retention_days = 0
    else:
        retention_limit = (
            MAX_RETENTION_DAYS[normalized_lifecycle]
            if normalized_lifecycle
            in {
                LearnerMemoryLifecycle.PROPOSED,
                LearnerMemoryLifecycle.CONFLICTED,
                LearnerMemoryLifecycle.REVOKED,
            }
            else MAX_RETENTION_DAYS[normalized_kind]
        )
        duration = _utc(expires_at) - _utc(now)
        if duration <= timedelta(0):
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "expiry_not_future")
        if duration > timedelta(days=retention_limit):
            return decision(LearnerMemoryDecisionStatus.BLOCKED, "retention_limit_exceeded")
        retention_days = max(1, math.ceil(duration.total_seconds() / 86400))

    if not isinstance(existing_memories, Sequence) or isinstance(existing_memories, (str, bytes)):
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "existing_memories_invalid")
    conflicts, conflict_reason = detect_memory_conflicts(
        scope_fingerprint=normalized_scope.fingerprint,
        kind=normalized_kind,
        memory_key=normalized_key,
        value_hash=value_hash,
        existing_memories=existing_memories,
        now=now,
    )
    if conflict_reason != "ok":
        return decision(LearnerMemoryDecisionStatus.BLOCKED, conflict_reason)
    if normalized_lifecycle is LearnerMemoryLifecycle.CONFLICTED and not conflicts:
        return decision(LearnerMemoryDecisionStatus.BLOCKED, "conflict_not_found")
    if normalized_lifecycle is LearnerMemoryLifecycle.ACTIVE and conflicts:
        conflict_expiry = min(
            _utc(expires_at),
            _utc(now) + timedelta(days=MAX_RETENTION_DAYS[LearnerMemoryLifecycle.CONFLICTED]),
        ).isoformat().replace("+00:00", "Z")
        return decision(
            LearnerMemoryDecisionStatus.ACCEPTED,
            "activation_conflicted",
            lifecycle=LearnerMemoryLifecycle.CONFLICTED,
            conflicts=conflicts,
            retention_days=min(retention_days, MAX_RETENTION_DAYS[LearnerMemoryLifecycle.CONFLICTED]),
            effective_expires_at=conflict_expiry,
        )
    return decision(
        LearnerMemoryDecisionStatus.ACCEPTED,
        "accepted",
        conflicts=conflicts,
        retention_days=retention_days,
    )


def snapshot_from_decision(decision: LearnerMemoryDecision) -> LearnerMemorySnapshot:
    """Build a provider-neutral snapshot from an accepted non-expired decision."""

    if not decision.accepted:
        raise ValueError("only accepted decisions can become snapshots")
    if decision.lifecycle == LearnerMemoryLifecycle.EXPIRED.value:
        raise ValueError("expired decisions do not create retrievable snapshots")
    expires_at = datetime.fromisoformat(decision.expires_at.replace("Z", "+00:00"))
    return LearnerMemorySnapshot(
        memory_id=decision.memory_id,
        scope_fingerprint=decision.scope_fingerprint,
        kind=LearnerMemoryKind(decision.kind),
        memory_key=decision.memory_key,
        value_hash=decision.value_hash,
        lifecycle=LearnerMemoryLifecycle(decision.lifecycle),
        expires_at=expires_at,
    )


__all__ = [
    "FORBIDDEN_SAFETY_SIGNALS",
    "GLOBAL_MEMORY_KEYS",
    "LEARNER_MEMORY_POLICY_VERSION",
    "LearnerMemoryDecision",
    "LearnerMemoryDecisionStatus",
    "LearnerMemoryKind",
    "LearnerMemoryLifecycle",
    "LearnerMemoryScope",
    "LearnerMemoryScopeType",
    "LearnerMemorySnapshot",
    "LearnerMemorySource",
    "MAX_RETENTION_DAYS",
    "MemoryRetentionEvaluation",
    "SAFE_SAFETY_SIGNALS",
    "detect_memory_conflicts",
    "evaluate_learner_memory_admission",
    "evaluate_memory_retention",
    "snapshot_from_decision",
    "stable_sha256",
]
