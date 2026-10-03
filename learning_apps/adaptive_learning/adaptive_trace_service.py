"""Privacy-safe, replayable projection of one Adaptive Core decision trace."""

from __future__ import annotations

from typing import Any, Iterable

from django.db.models import Q

from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    AdaptiveProbe,
    ConceptIdentityDecision,
    LearnerBehaviorEvidence,
    LearnerMasteryState,
    LearningGoal,
    UserProfile,
)


ADAPTIVE_TRACE_SCHEMA_VERSION = "adaptive_trace_v1"


def _score(value: Any) -> float:
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return 0.0


def _identity_decision_ids(events: Iterable[AdaptiveInteractionEvent]) -> list[int]:
    ids: set[int] = set()
    for event in events:
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        try:
            decision_id = int(metadata.get("identity_decision_id") or 0)
        except (TypeError, ValueError):
            decision_id = 0
        if decision_id:
            ids.add(decision_id)
    return sorted(ids)


def build_adaptive_trace_bundle(
    *,
    user: UserProfile,
    goal: LearningGoal,
    trace_id: str,
    required_stages: Iterable[str] = (),
) -> dict[str, Any]:
    """Build a structured trace without exposing question or answer text.

    Identity rows are linked through the admitted/rejected event metadata;
    mastery and teaching decisions are linked through ``last_event``; review
    decisions use the probe's durable trace id.  The result is a read-only
    audit projection, so replaying it cannot alter learner state.
    """

    if goal.user_id != user.user_id:
        raise ValueError("learning goal does not belong to the user")
    clean_trace_id = str(trace_id or "").strip()[:128]
    probes = list(
        AdaptiveProbe.objects.filter(
            user=user,
            learning_goal=goal,
            trace_id=clean_trace_id,
        ).order_by("created_at", "id")
    )
    probe_ids = {probe.id for probe in probes}
    events = list(
        AdaptiveInteractionEvent.objects.filter(
            user=user,
            learning_goal=goal,
            trace_id=clean_trace_id,
        ).order_by("created_at", "id")
    )
    event_id_set = {event.id for event in events}
    # An answer arrives in a later HTTP/Celery trace, but its durable probe id
    # still links it to the original review decision.  Follow that parent edge
    # so one journey bundle remains end-to-end without overwriting either
    # request trace.
    if probe_ids:
        for event in AdaptiveInteractionEvent.objects.filter(
            user=user,
            learning_goal=goal,
        ).exclude(id__in=event_id_set).order_by("created_at", "id"):
            metadata = event.metadata if isinstance(event.metadata, dict) else {}
            try:
                linked_probe_id = int(metadata.get("probe_id") or 0)
            except (TypeError, ValueError):
                linked_probe_id = 0
            if linked_probe_id in probe_ids:
                events.append(event)
                event_id_set.add(event.id)
        events.sort(key=lambda event: (event.created_at, event.id))
    event_ids = [event.id for event in events]
    decision_ids = _identity_decision_ids(events)
    decisions = list(
        ConceptIdentityDecision.objects.filter(
            user=user,
            learning_goal=goal,
            id__in=decision_ids,
        ).select_related("selected_concept").order_by("created_at", "id")
    )
    states = list(
        LearnerMasteryState.objects.filter(
            Q(last_event_id__in=event_ids) | Q(last_eligible_evidence_id__in=event_ids),
            user=user,
            learning_goal=goal,
        ).order_by("concept_key", "id")
    ) if event_ids else []
    behavior = list(
        LearnerBehaviorEvidence.objects.filter(
            user=user,
            learning_goal=goal,
            trace_id=clean_trace_id,
        ).order_by("occurred_at", "id")
    )

    stages: list[dict[str, Any]] = []
    policy_versions: set[str] = set()
    for decision in decisions:
        stages.append({
            "stage": "concept_identity",
            "source_id": decision.id,
            "concept_key": decision.selected_concept.concept_key if decision.selected_concept else "",
            "decision_status": decision.decision_status,
            "admission_status": decision.admission_status,
            "admission_reason": decision.admission_reason,
            "relation": decision.relation,
            "confidence_band": decision.confidence_band,
            "resolver_version": decision.resolver_version,
            "prompt_version": decision.prompt_version,
            "taxonomy_fingerprint": decision.taxonomy_fingerprint,
            "request_hash": decision.request_hash,
        })
        if decision.resolver_version:
            policy_versions.add(decision.resolver_version)

    for event in events:
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        policy_version = str(metadata.get("mastery_policy_version") or "")
        if policy_version:
            policy_versions.add(policy_version)
        stages.append({
            "stage": "mastery_admission",
            "source_id": event.id,
            "concept_key": event.concept_key,
            "source": event.source,
            "event_trace_id": event.trace_id,
            "probe_trace_id": metadata.get("probe_trace_id") or "",
            "confidence": _score(event.confidence),
            "admission_status": metadata.get("mastery_admission_status") or "blocked",
            "admission_reason": metadata.get("mastery_admission_reason") or "",
            "identity_decision_id": metadata.get("identity_decision_id"),
            "policy_version": policy_version,
            "idempotency_key": event.idempotency_key,
        })

    for state in states:
        if state.policy_version:
            policy_versions.add(state.policy_version)
        mastery_vector = {
            "facts": _score(state.facts_mastery),
            "procedures": _score(state.procedures_mastery),
            "strategies": _score(state.strategies_mastery),
            "rationales": _score(state.rationales_mastery),
        }
        stages.append({
            "stage": "mastery_state",
            "source_id": state.id,
            "source_event_id": state.last_event_id,
            "concept_key": state.concept_key,
            "mastery": mastery_vector,
            "mastery_confidence": _score(state.mastery_confidence),
            "eligible_evidence_count": int(state.eligible_evidence_count),
            "weakest_dimension": state.weakest_dimension,
            "policy_version": state.policy_version,
        })
        stages.append({
            "stage": "teaching_policy",
            "source_id": state.id,
            "source_event_id": state.last_event_id,
            "concept_key": state.concept_key,
            "feedback_tier": state.feedback_tier,
            "weakest_dimension": state.weakest_dimension,
            "response_policy": state.response_policy if isinstance(state.response_policy, dict) else {},
            "policy_version": state.policy_version,
        })

    for probe in probes:
        rubric = probe.expected_rubric if isinstance(probe.expected_rubric, dict) else {}
        scheduler_version = str(rubric.get("scheduler_version") or "")
        if scheduler_version:
            policy_versions.add(scheduler_version)
        stages.append({
            "stage": "review_decision",
            "source_id": probe.id,
            "concept_key": probe.concept_key,
            "target_dimension": probe.target_dimension,
            "trigger": rubric.get("trigger") or "",
            "due_reason": rubric.get("due_reason") or "",
            "due_decision": rubric.get("due_decision") if isinstance(rubric.get("due_decision"), dict) else {},
            "scheduler_version": scheduler_version,
        })
        stages.append({
            "stage": "probe_action",
            "source_id": probe.id,
            "concept_key": probe.concept_key,
            "target_dimension": probe.target_dimension,
            "status": probe.status,
            "scheduler_version": scheduler_version,
            "probe_version": rubric.get("probe_version") or "",
        })

    for evidence in behavior:
        stages.append({
            "stage": "behavior_evidence",
            "source_id": evidence.id,
            "concept_key": evidence.concept_key,
            "event_type": evidence.event_type,
            "source": evidence.source,
            "confidence": _score(evidence.confidence),
            "schema_version": evidence.schema_version,
            "idempotency_key": evidence.idempotency_key,
        })
        if evidence.schema_version:
            policy_versions.add(evidence.schema_version)
    for sequence, stage in enumerate(stages, start=1):
        stage["sequence"] = sequence
    stage_names = [str(stage["stage"]) for stage in stages]
    required = list(dict.fromkeys(str(stage) for stage in required_stages if str(stage or "").strip()))
    missing = [stage for stage in required if stage not in stage_names]
    return {
        "schema_version": ADAPTIVE_TRACE_SCHEMA_VERSION,
        "trace_id": clean_trace_id,
        "linked_trace_ids": sorted({event.trace_id for event in events if event.trace_id and event.trace_id != clean_trace_id}),
        "user_id": user.user_id,
        "learning_goal_id": goal.id,
        "stage_names": stage_names,
        "required_stages": required,
        "missing_stages": missing,
        "trace_complete": not missing,
        "policy_versions": sorted(policy_versions),
        "stages": stages,
        "privacy": {
            "raw_question_retained": False,
            "raw_student_answer_retained": False,
            "identity_input_retained": False,
        },
    }
