from __future__ import annotations

import json
from typing import Any, Iterable

from django.db import transaction
from django.utils import timezone

from learning_apps.infrastructure.services.trace_context import current_trace_context
from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    LearnerBehaviorEvidence,
    LearnerMisconceptionState,
    LearningGoal,
    UserProfile,
)

from .grader_service import normalize_concept_key


LEARNER_STATE_SCHEMA_VERSION = "learner_state_v1"
MAX_STATE_JSON_BYTES = 128 * 1024


class LearnerStateValidationError(ValueError):
    """Raised when a learner-state write violates the typed service contract."""


def _validated_dict(value: Any, field_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise LearnerStateValidationError(f"{field_name} must be an object")
    try:
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise LearnerStateValidationError(f"{field_name} must be JSON serializable") from exc
    if len(encoded) > MAX_STATE_JSON_BYTES:
        raise LearnerStateValidationError(f"{field_name} exceeds {MAX_STATE_JSON_BYTES} bytes")
    return dict(value)



def _validated_confidence(value: Any, default: float = 0.7) -> float:
    try:
        confidence = float(value)
    except (TypeError, ValueError):
        confidence = default
    return round(max(0.0, min(1.0, confidence)), 4)



@transaction.atomic
def record_behavior_evidence(
    *,
    user: UserProfile,
    event_type: str,
    source: str,
    payload: dict[str, Any],
    learning_goal: LearningGoal | None = None,
    concept_key: str = "",
    confidence: float = 1.0,
    idempotency_key: str = "",
) -> LearnerBehaviorEvidence:
    if event_type not in {choice[0] for choice in LearnerBehaviorEvidence.EVENT_TYPE_CHOICES}:
        raise LearnerStateValidationError(f"unsupported behavior event_type: {event_type}")
    if learning_goal and learning_goal.user_id != user.user_id:
        raise LearnerStateValidationError("behavior evidence goal does not belong to the user")
    clean_payload = _validated_dict(payload, "payload")
    clean_idempotency_key = str(idempotency_key or "").strip()[:160]
    if clean_idempotency_key:
        existing = LearnerBehaviorEvidence.objects.filter(
            user=user,
            idempotency_key=clean_idempotency_key,
        ).first()
        if existing:
            return existing
    trace = current_trace_context()
    return LearnerBehaviorEvidence.objects.create(
        user=user,
        learning_goal=learning_goal,
        concept_key=normalize_concept_key(concept_key, "") if concept_key else "",
        event_type=event_type,
        source=str(source or "learner_state_service")[:80],
        payload=clean_payload,
        confidence=_validated_confidence(confidence, 1.0),
        idempotency_key=clean_idempotency_key,
        trace_id=trace.trace_id,
        schema_version="learner_behavior_v1",
    )



def _misconception_key(value: Any) -> str:
    key = normalize_concept_key(value, "")
    if not key:
        raise LearnerStateValidationError("misconception key cannot be empty")
    return key[:160]


@transaction.atomic
def observe_misconceptions(
    *,
    event: AdaptiveInteractionEvent,
    tags: Iterable[str],
    confidence: float | None = None,
) -> list[LearnerMisconceptionState]:
    if not event.learning_goal_id:
        raise LearnerStateValidationError("misconception evidence requires a learning goal")
    clean_tags = list(dict.fromkeys(_misconception_key(tag) for tag in tags if str(tag or "").strip()))
    observed: list[LearnerMisconceptionState] = []
    event_confidence = _validated_confidence(confidence if confidence is not None else event.confidence, 0.5)
    concept_key = normalize_concept_key(event.concept_key, "general")
    for tag in clean_tags:
        LearnerMisconceptionState.objects.get_or_create(
            user=event.user,
            learning_goal=event.learning_goal,
            concept_key=concept_key,
            misconception_key=tag,
            defaults={
                "description": tag.replace("_", " "),
                "confidence": event_confidence,
                "occurrence_count": 0,
                "first_evidence": event,
                "last_evidence": event,
                "source_summary": {},
            },
        )
        state = LearnerMisconceptionState.objects.select_for_update().get(
            user=event.user,
            learning_goal=event.learning_goal,
            concept_key=concept_key,
            misconception_key=tag,
        )
        summary = dict(state.source_summary or {})
        source_counts = dict(summary.get("source_counts") or {})
        source_counts[event.source] = int(source_counts.get(event.source, 0)) + 1
        summary.update({
            "source_counts": source_counts,
            "last_event_id": event.id,
            "last_event_confidence": event_confidence,
        })
        if not state.first_evidence_id:
            state.first_evidence = event
        state.last_evidence = event
        state.status = LearnerMisconceptionState.STATUS_ACTIVE
        state.resolved_at = None
        state.resolved_by_evidence = None
        state.occurrence_count = int(state.occurrence_count or 0) + 1
        state.confidence = round(max(float(state.confidence or 0.0), event_confidence), 4)
        state.source_summary = summary
        state.save(update_fields=[
            "first_evidence",
            "last_evidence",
            "status",
            "resolved_at",
            "resolved_by_evidence",
            "occurrence_count",
            "confidence",
            "source_summary",
            "updated_at",
        ])
        observed.append(state)
    return observed


@transaction.atomic
def resolve_misconception(
    *,
    user: UserProfile,
    learning_goal: LearningGoal,
    concept_key: str,
    misconception_key: str,
    evidence_event: AdaptiveInteractionEvent,
) -> LearnerMisconceptionState:
    if learning_goal.user_id != user.user_id or evidence_event.user_id != user.user_id:
        raise LearnerStateValidationError("misconception resolution ownership mismatch")
    if evidence_event.learning_goal_id != learning_goal.id:
        raise LearnerStateValidationError("resolution evidence belongs to another learning goal")
    state = LearnerMisconceptionState.objects.select_for_update().get(
        user=user,
        learning_goal=learning_goal,
        concept_key=normalize_concept_key(concept_key, "general"),
        misconception_key=_misconception_key(misconception_key),
    )
    state.status = LearnerMisconceptionState.STATUS_RESOLVED
    state.resolved_by_evidence = evidence_event
    state.resolved_at = timezone.now()
    state.last_evidence = evidence_event
    summary = dict(state.source_summary or {})
    summary["resolved_by_event_id"] = evidence_event.id
    summary["resolved_by_source"] = evidence_event.source
    state.source_summary = summary
    state.save(update_fields=[
        "status",
        "resolved_by_evidence",
        "resolved_at",
        "last_evidence",
        "source_summary",
        "updated_at",
    ])
    return state


def build_learner_state_snapshot(user: UserProfile, learning_goal: LearningGoal | None = None) -> dict[str, Any]:
    mastery_qs = user.mastery_states.all()
    misconception_qs = user.misconception_states.all()
    behavior_qs = user.behavior_evidence.all()
    if learning_goal:
        if learning_goal.user_id != user.user_id:
            raise LearnerStateValidationError("learning goal does not belong to the user")
        mastery_qs = mastery_qs.filter(learning_goal=learning_goal)
        misconception_qs = misconception_qs.filter(learning_goal=learning_goal)
        behavior_qs = behavior_qs.filter(learning_goal=learning_goal)
    return {
        "schema_version": LEARNER_STATE_SCHEMA_VERSION,
        "mastery_state_count": mastery_qs.count(),
        "active_misconception_count": misconception_qs.filter(
            status=LearnerMisconceptionState.STATUS_ACTIVE
        ).count(),
        "behavior_evidence_count": behavior_qs.count(),
    }
