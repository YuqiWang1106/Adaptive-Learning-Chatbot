from __future__ import annotations

import math
import logging
from statistics import mean, pstdev
from typing import Any, Dict, Iterable, List

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    ConceptIdentityDecision,
    LearnerMasteryState,
    LearningGoal,
    UserProfile,
)

from .constants import DIMENSION_LABELS, DIMENSION_POLICY, DIMENSION_WEIGHTS, DIMENSIONS, FUSION_WEIGHTS, TIER_POLICY
from .concept_identity_service import (
    IDENTITY_VERSION,
    canonicalize_concept_key,
    concept_is_mastery_eligible,
    normalize_stable_concept_key,
    taxonomy_fingerprint_for_goal,
)
from .grader_service import AdaptiveGradingResult
from learning_apps.infrastructure.services.trace_context import current_trace_context
from .mastery_evidence_policy import (
    MASTERY_EVIDENCE_POLICY_VERSION,
    MasteryEvidenceDecision,
    evaluate_mastery_evidence,
    evidence_is_admitted,
)

logger = logging.getLogger(__name__)

CURVE_ALPHA = 0.45
CURVE_MAX_POINTS = 100
CURVE_CONFIDENCE_THRESHOLD = 0.65
CURVE_ELIGIBLE_SOURCES = {
    AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE,
}


def _adaptive_trace_enabled() -> bool:
    return bool(settings.LEARNING_ADAPTIVE_TRACE)


def _fmt_score(value: Any) -> str:
    try:
        return f"{float(value):.4f}"
    except (TypeError, ValueError):
        return "n/a"


def _emit_adaptive_trace(message: str) -> None:
    if not _adaptive_trace_enabled():
        return
    formatted = f"[AdaptiveMastery] {message}"
    print(formatted, flush=True)
    logger.info(formatted)


def clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    return round(max(0.0, min(1.0, number)), 4)


def normalize_dimension_scores(raw: Dict[str, Any] | None, fallback: float = 0.5) -> Dict[str, float]:
    values = raw if isinstance(raw, dict) else {}
    return {dimension: clamp01(values.get(dimension), fallback) for dimension in DIMENSIONS}


def default_concept_key_for_goal(goal: LearningGoal | None) -> str:
    if not goal:
        return "general"
    return canonicalize_concept_key(
        goal.branch or goal.domain or goal.title or goal.preference_text or "general",
        domain=goal.domain or "",
        branch=goal.branch or "",
    )


def _average_scores(events: Iterable[AdaptiveInteractionEvent], fallback: Dict[str, float]) -> Dict[str, float]:
    buckets: Dict[str, List[float]] = {dimension: [] for dimension in DIMENSIONS}
    for event in events:
        scores = normalize_dimension_scores(event.dimension_scores, event.accuracy_score)
        for dimension in DIMENSIONS:
            buckets[dimension].append(scores[dimension])
    return {
        dimension: round(mean(values), 4) if values else fallback[dimension]
        for dimension, values in buckets.items()
    }


def _dimension_mastery_score(scores: Dict[str, float]) -> float:
    return round(sum(scores[dimension] * DIMENSION_WEIGHTS[dimension] for dimension in DIMENSIONS), 4)


def _apply_dependency_adjustment(scores: Dict[str, float]) -> Dict[str, float]:
    adjusted = dict(scores)
    adjusted["procedures"] = min(adjusted["procedures"], adjusted["facts"] + 0.25)
    adjusted["strategies"] = min(adjusted["strategies"], mean([adjusted["facts"], adjusted["procedures"]]) + 0.25)
    adjusted["rationales"] = min(
        adjusted["rationales"],
        mean([adjusted["facts"], adjusted["procedures"], adjusted["strategies"]]) + 0.25,
    )
    return {dimension: clamp01(value) for dimension, value in adjusted.items()}


def _ema_smooth(values: List[float], alpha: float = CURVE_ALPHA) -> List[float]:
    if not values:
        return []
    smoothed = [clamp01(values[0])]
    for value in values[1:]:
        smoothed.append(round((alpha * clamp01(value)) + ((1.0 - alpha) * smoothed[-1]), 4))
    return smoothed


def _linear_slope(values: List[float]) -> float:
    if len(values) < 2:
        return 0.0
    x_mean = (len(values) - 1) / 2.0
    y_mean = mean(values)
    denominator = sum((idx - x_mean) ** 2 for idx in range(len(values)))
    if denominator == 0:
        return 0.0
    return round(sum((idx - x_mean) * (value - y_mean) for idx, value in enumerate(values)) / denominator, 4)


def _sign(delta: float, threshold: float = 0.015) -> int:
    if delta > threshold:
        return 1
    if delta < -threshold:
        return -1
    return 0


def _curve_analysis_from_values(values: List[float], confidences: List[float] | None = None) -> Dict[str, Any]:
    recent_raw = [clamp01(value) for value in values[-CURVE_MAX_POINTS:]]
    evidence_count = len(recent_raw)
    if not recent_raw:
        return {
            "pattern": "insufficient_data",
            "confidence": 0.0,
            "evidence_count": 0,
            "reason": {
                "reason": "no_curve_evidence",
                "values": [],
            },
        }

    smoothed = _ema_smooth(recent_raw)
    deltas = [round(smoothed[idx] - smoothed[idx - 1], 4) for idx in range(1, len(smoothed))]
    raw_deltas = [round(recent_raw[idx] - recent_raw[idx - 1], 4) for idx in range(1, len(recent_raw))]
    slope = _linear_slope(smoothed)
    raw_slope = _linear_slope(recent_raw)
    total_delta = round(smoothed[-1] - smoothed[0], 4)
    recent_delta = deltas[-1] if deltas else 0.0
    volatility = round(pstdev(deltas), 4) if len(deltas) >= 2 else 0.0
    signs = [_sign(delta) for delta in deltas]
    sign_changes = sum(
        1
        for idx in range(1, len(signs))
        if signs[idx] and signs[idx - 1] and signs[idx] != signs[idx - 1]
    )
    negative_steps = sum(1 for delta in deltas if delta <= -0.02)
    positive_steps = sum(1 for delta in deltas if delta >= 0.02)
    curve_range = round(max(smoothed) - min(smoothed), 4)
    latest_drawdown = round(max(smoothed[:-1], default=smoothed[-1]) - smoothed[-1], 4)
    raw_drawdown = round(max(recent_raw[:-1], default=recent_raw[-1]) - recent_raw[-1], 4)
    raw_recent_delta = raw_deltas[-1] if raw_deltas else 0.0
    recent_four_range = round(max(smoothed[-4:]) - min(smoothed[-4:]), 4) if len(smoothed) >= 4 else curve_range
    recent_confidences = [clamp01(value, 0.65) for value in (confidences or [])[-evidence_count:]]
    mean_grader_confidence = round(mean(recent_confidences), 4) if recent_confidences else 0.65
    evidence_score = min(1.0, evidence_count / 6.0)
    stability_score = 1.0 - clamp01(volatility / 0.30)
    confidence = round(
        (0.45 * evidence_score) + (0.30 * stability_score) + (0.25 * mean_grader_confidence),
        4,
    )

    if evidence_count < 3:
        pattern = "insufficient_data"
        reason_code = "less_than_3_points"
    elif evidence_count < 5:
        pattern = "developing"
        reason_code = "3_or_4_points_no_strong_pattern"
    elif (
        (raw_drawdown >= 0.20 and raw_recent_delta <= -0.08)
        or (raw_drawdown >= 0.35 and max(recent_raw) - min(recent_raw) >= 0.50)
    ):
        pattern = "fluctuating"
        reason_code = "large_recent_drawdown"
    elif (slope <= -0.045 or raw_slope <= -0.04) and total_delta <= -0.12 and negative_steps >= max(2, math.ceil(len(deltas) / 2)):
        pattern = "declining"
        reason_code = "negative_slope_and_repeated_declines"
    elif sign_changes >= 2 and volatility >= 0.10 and curve_range >= 0.20:
        pattern = "fluctuating"
        reason_code = "high_volatility_and_direction_changes"
    elif abs(slope) <= 0.015 and recent_four_range <= 0.08 and smoothed[-1] < 0.75:
        pattern = "plateau"
        reason_code = "flat_low_mastery_window"
    elif slope >= 0.07 and total_delta >= 0.25 and recent_delta >= -0.03:
        pattern = "fast_growth"
        reason_code = "strong_positive_slope"
    elif slope >= 0.02 and total_delta >= 0.08:
        pattern = "gradual_growth"
        reason_code = "moderate_positive_slope"
    else:
        pattern = "developing"
        reason_code = "no_stable_pattern_yet"

    return {
        "pattern": pattern,
        "confidence": confidence,
        "evidence_count": evidence_count,
        "reason": {
            "reason": reason_code,
            "values": recent_raw,
            "smoothed_values": smoothed,
            "slope": slope,
            "raw_slope": raw_slope,
            "total_delta": total_delta,
            "recent_delta": recent_delta,
            "volatility": volatility,
            "sign_changes": sign_changes,
            "positive_steps": positive_steps,
            "negative_steps": negative_steps,
            "range": curve_range,
            "drawdown": latest_drawdown,
            "raw_drawdown": raw_drawdown,
            "mean_grader_confidence": mean_grader_confidence,
            "evidence_score": round(evidence_score, 4),
            "stability_score": round(stability_score, 4),
        },
    }


def _curve_pattern(values: List[float]) -> str:
    return _curve_analysis_from_values(values)["pattern"]


def _base_feedback_tier(quality_score: float) -> str:
    if quality_score >= 0.75:
        return "REINFORCE"
    if quality_score >= 0.45:
        return "CONSOLIDATE"
    return "SCAFFOLD"


def _feedback_tier(quality_score: float, curve_pattern: str, curve_confidence: float = 0.0) -> str:
    if curve_confidence >= CURVE_CONFIDENCE_THRESHOLD:
        if curve_pattern == "declining":
            return "REVIEW"
        if curve_pattern == "plateau":
            return "PLATEAU"
        if curve_pattern == "fast_growth" and quality_score >= 0.70:
            return "REINFORCE"
        if curve_pattern == "fluctuating":
            return "CONSOLIDATE"
    return _base_feedback_tier(quality_score)


def _curve_intervention(curve_pattern: str, curve_confidence: float) -> str:
    if curve_confidence < CURVE_CONFIDENCE_THRESHOLD:
        return "Curve evidence is still low-confidence; adapt mainly from quality score and weakest dimension."
    if curve_pattern == "declining":
        return "Lower difficulty, review prerequisites, and rebuild facts/procedures before advancing."
    if curve_pattern == "plateau":
        return "Change explanation mode with analogy, visual framing, counterexample, or worked example."
    if curve_pattern == "fluctuating":
        return "Use a short, low-ambiguity diagnostic question before increasing difficulty."
    if curve_pattern == "fast_growth":
        return "Add a related transfer or challenge task while staying anchored to the current question."
    return "Continue normal weakest-dimension support while collecting more trend evidence."


def _response_policy(
    weakest_dimension: str,
    feedback_tier: str,
    *,
    curve_pattern: str = "insufficient_data",
    curve_confidence: float = 0.0,
    curve_reason: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "tier": feedback_tier,
        "tier_policy": TIER_POLICY.get(feedback_tier, TIER_POLICY["CONSOLIDATE"]),
        "weakest_dimension": weakest_dimension,
        "dimension_label": DIMENSION_LABELS.get(weakest_dimension, weakest_dimension.title()),
        "dimension_policy": DIMENSION_POLICY.get(weakest_dimension, "Use the current evidence to adapt the explanation."),
        "curve_pattern": curve_pattern,
        "curve_confidence": clamp01(curve_confidence),
        "curve_intervention": _curve_intervention(curve_pattern, curve_confidence),
        "curve_reason": curve_reason or {},
    }


def _score_series(events: Iterable[AdaptiveInteractionEvent], dimension: str) -> List[float]:
    return [
        _event_curve_scores(event)[dimension]
        for event in events
        if event.source in CURVE_ELIGIBLE_SOURCES
    ]


def _event_curve_scores(event: AdaptiveInteractionEvent) -> Dict[str, float]:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    mastery_after = metadata.get("mastery_after") if isinstance(metadata.get("mastery_after"), dict) else None
    if mastery_after:
        return normalize_dimension_scores(mastery_after, event.accuracy_score)
    return normalize_dimension_scores(event.dimension_scores, event.accuracy_score)


def _curve_events(events: Iterable[AdaptiveInteractionEvent]) -> List[AdaptiveInteractionEvent]:
    return [
        event
        for event in events
        if event.source in CURVE_ELIGIBLE_SOURCES
        and evidence_is_admitted(event.metadata)
    ]


def _dimension_curve_analysis(
    events: Iterable[AdaptiveInteractionEvent],
    *,
    current_event: AdaptiveInteractionEvent | None = None,
    current_fused: Dict[str, float] | None = None,
) -> Dict[str, Dict[str, Any]]:
    eligible = _curve_events(events)
    results: Dict[str, Dict[str, Any]] = {}
    for dimension in DIMENSIONS:
        values: List[float] = []
        confidences: List[float] = []
        for event in eligible:
            if current_event and current_fused and event.id == current_event.id:
                scores = current_fused
            else:
                scores = _event_curve_scores(event)
            values.append(scores[dimension])
            confidences.append(clamp01(event.confidence, 0.65))
        results[dimension] = _curve_analysis_from_values(values, confidences)
    return results


def _mastery_vector_from_state(state: LearnerMasteryState) -> Dict[str, float]:
    return {
        "facts": clamp01(state.facts_mastery),
        "procedures": clamp01(state.procedures_mastery),
        "strategies": clamp01(state.strategies_mastery),
        "rationales": clamp01(state.rationales_mastery),
    }


@transaction.atomic
def record_event_and_update_state(
    *,
    username: str,
    learning_goal_id: int,
    source: str,
    question_text: str,
    student_answer: str,
    grading: AdaptiveGradingResult,
    metadata: Dict[str, Any] | None = None,
) -> AdaptiveInteractionEvent | None:
    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(user=user, id=int(learning_goal_id)).first() if user else None
    if not user or not goal:
        return None

    event_metadata = dict(metadata or {})
    idempotency_key = str(event_metadata.get("idempotency_key") or "").strip()[:160]
    if not idempotency_key:
        # Probe items have stable producer identifiers. Use them when
        # available, while leaving unsupported/legacy callers append-only.
        if source == AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE and event_metadata.get("probe_id"):
            item_id = str(event_metadata.get("probe_item_id") or "single")
            idempotency_key = f"probe:{event_metadata['probe_id']}:{item_id}"
    if idempotency_key:
        existing_event = (
            AdaptiveInteractionEvent.objects.select_for_update()
            .filter(user=user, learning_goal=goal, idempotency_key=idempotency_key)
            .order_by("id")
            .first()
        )
        if existing_event:
            return existing_event
        event_metadata["idempotency_key"] = idempotency_key
    # A concept key in an identity resolution is a stable identifier, not a
    # learner-facing label.  Never run it through the linguistic normalizer a
    # second time (that can change e.g. ``homeostasis`` on replay).
    raw_concept_key = str(grading.concept_key or "").strip()
    concept_key = normalize_stable_concept_key(
        event_metadata.get("resolved_concept_key") or raw_concept_key,
        default_concept_key_for_goal(goal),
    )
    scores = normalize_dimension_scores(grading.dimension_scores, grading.accuracy_score)
    event_metadata.setdefault("resolved_concept_key", concept_key)
    event_metadata.setdefault("original_concept_key", str(grading.concept_key or ""))
    identity_version_valid = event_metadata.get("identity_version") == IDENTITY_VERSION
    identity_decision_accepted = event_metadata.get("identity_decision_status") == "accepted"
    identity_admission_accepted = event_metadata.get("identity_admission_status") == "accepted"
    identity_declared_eligible = event_metadata.get("identity_mastery_eligible") is True
    resolved_key_matches = normalize_stable_concept_key(
        event_metadata.get("resolved_concept_key") or ""
    ) == concept_key
    declared_fingerprint = str(event_metadata.get("identity_taxonomy_fingerprint") or "")
    current_fingerprint = taxonomy_fingerprint_for_goal(user, goal)
    taxonomy_matches = bool(
        declared_fingerprint
        and current_fingerprint
        and declared_fingerprint == current_fingerprint
    )
    registry_eligible = concept_is_mastery_eligible(user, goal, concept_key)
    # Admission is bound to the exact persisted decision.  Status flags in
    # event metadata are necessary but not sufficient: callers must not be
    # able to forge an ``accepted`` event by copying those flags.
    decision_id_raw = event_metadata.get("identity_decision_id")
    try:
        decision_id = int(decision_id_raw)
    except (TypeError, ValueError):
        decision_id = 0
    identity_decision = (
        ConceptIdentityDecision.objects.filter(
            id=decision_id,
            user=user,
            learning_goal=goal,
            decision_status=ConceptIdentityDecision.DECISION_ACCEPTED,
            admission_status=ConceptIdentityDecision.ADMISSION_ACCEPTED,
            relation=ConceptIdentityDecision.RELATION_SAME,
            taxonomy_fingerprint=declared_fingerprint,
            selected_concept__concept_key=concept_key,
        ).first()
        if decision_id
        else None
    )
    persisted_decision_valid = identity_decision is not None
    evidence_decision: MasteryEvidenceDecision = evaluate_mastery_evidence(
        source=source,
        confidence=grading.confidence,
        dimension_scores=grading.dimension_scores,
        metadata=event_metadata,
    )
    identity_admission_accepted = bool(
        identity_version_valid
        and identity_decision_accepted
        and identity_admission_accepted
        and identity_declared_eligible
        and resolved_key_matches
        and taxonomy_matches
        and registry_eligible
        and persisted_decision_valid
    )
    mastery_update_accepted = bool(
        identity_admission_accepted
        and evidence_decision.accepted
    )
    rejection_checks = [
        (identity_version_valid, "missing_or_stale_identity_version"),
        (identity_decision_accepted, "identity_decision_not_accepted"),
        (identity_admission_accepted, "identity_admission_blocked"),
        (identity_declared_eligible, "identity_not_mastery_eligible"),
        (resolved_key_matches, "identity_key_mismatch"),
        (taxonomy_matches, "identity_taxonomy_changed"),
        (registry_eligible, "unverified_concept_identity"),
        (persisted_decision_valid, "identity_decision_missing_or_mismatched"),
    ]
    rejection_reason = next(
        (reason for passed, reason in rejection_checks if not passed),
        "",
    )
    if not rejection_reason and not evidence_decision.accepted:
        rejection_reason = evidence_decision.reason_code
    event_metadata["identity_registry_eligible"] = registry_eligible
    event_metadata["identity_decision_persisted"] = persisted_decision_valid
    event_metadata["identity_current_taxonomy_fingerprint"] = current_fingerprint
    event_metadata["mastery_policy_version"] = MASTERY_EVIDENCE_POLICY_VERSION
    event_metadata["mastery_evidence_source"] = source
    event_metadata["mastery_evidence_confidence"] = evidence_decision.confidence
    event_metadata["mastery_evidence_admission"] = evidence_decision.admission_status
    event_metadata["mastery_evidence_rejection_reason"] = (
        "" if evidence_decision.accepted else evidence_decision.reason_code
    )
    event_metadata["mastery_update_accepted"] = mastery_update_accepted
    event_metadata["mastery_admission_status"] = "accepted" if mastery_update_accepted else "blocked"
    event_metadata["mastery_admission_reason"] = rejection_reason or "admitted"
    event_metadata["mastery_update_rejection_reason"] = rejection_reason
    trace_id = str(event_metadata.get("trace_id") or current_trace_context().trace_id or "")[:128]
    event_metadata["trace_id"] = trace_id
    event = AdaptiveInteractionEvent.objects.create(
        user=user,
        learning_goal=goal,
        concept_key=concept_key,
        source=source,
        question_text=question_text or "",
        student_answer=student_answer or "",
        accuracy_score=clamp01(grading.accuracy_score),
        dimension_scores=scores,
        grader_labels=grading.labels or {},
        evidence=grading.evidence or "",
        confidence=clamp01(grading.confidence),
        latency_ms=max(0, int(grading.latency_ms or 0)),
        idempotency_key=idempotency_key or None,
        trace_id=trace_id,
        metadata=event_metadata,
    )
    event.metadata = {
        **(event.metadata or {}),
        "mastery_source_event_id": event.id,
    }
    event.save(update_fields=["metadata"])
    _emit_adaptive_trace(
        "1 event_saved "
        f"user={username} goal_id={learning_goal_id} event_id={event.id} "
        f"source={source} concept={concept_key} "
        f"accuracy_score={_fmt_score(event.accuracy_score)} "
        "original_dimension_scores="
        + ", ".join(f"{dimension}={_fmt_score(scores[dimension])}" for dimension in DIMENSIONS)
    )
    if not mastery_update_accepted:
        _emit_adaptive_trace(
            "2 mastery_update_rejected "
            f"user={username} goal_id={learning_goal_id} event_id={event.id} concept={concept_key} "
            f"reason={rejection_reason}"
        )
        return event
    state = recompute_mastery_state(user=user, goal=goal, concept_key=concept_key, current_event=event)
    event.metadata = {
        **(event.metadata or {}),
        "mastery_after": _mastery_vector_from_state(state),
        "dimension_mastery_score_after": clamp01(state.dimension_mastery_score),
        "quality_score_after": clamp01(state.quality_score),
        "weakest_dimension_after": state.weakest_dimension,
        "curve_pattern_after": state.curve_pattern,
        "curve_confidence_after": clamp01(state.curve_confidence),
        "feedback_tier_after": state.feedback_tier,
    }
    event.save(update_fields=["metadata"])
    return event


def recompute_mastery_state(
    *,
    user: UserProfile,
    goal: LearningGoal,
    concept_key: str,
    current_event: AdaptiveInteractionEvent,
) -> LearnerMasteryState:
    # Long-term mastery is concept-scoped.  Raw events remain append-only for
    # audit/diagnostics, but only an explicit, current policy admission may
    # enter the fusion input.  In particular, understanding checks and events
    # from sibling concepts are never allowed to alter this state.
    concept_events = [
        event
        for event in AdaptiveInteractionEvent.objects.filter(
            user=user,
            learning_goal=goal,
            concept_key=concept_key,
        ).order_by("created_at", "id")
        if evidence_is_admitted(event.metadata)
    ]
    goal_events: list[AdaptiveInteractionEvent] = []
    current_scores = normalize_dimension_scores(current_event.dimension_scores, current_event.accuracy_score)
    local_recent = _average_scores(concept_events[-5:], current_scores)
    # Keep the legacy diagnostic field/weights readable while making its
    # source explicit: the historical component is now the same concept's
    # admitted evidence, never the whole goal.
    global_history = _average_scores(concept_events, current_scores)

    previous = LearnerMasteryState.objects.filter(user=user, learning_goal=goal, concept_key=concept_key).first()
    prior_evidence_at = None
    if previous and previous.last_eligible_evidence_id:
        prior_evidence_at = previous.last_eligible_evidence.created_at
    if prior_evidence_at is None:
        prior_events = [event for event in concept_events if event.id != current_event.id]
        prior_evidence_at = prior_events[-1].created_at if prior_events else None
    current_as_of = current_event.created_at or timezone.now()
    if previous and prior_evidence_at:
        # Decay is derived from persisted evidence time, not the wall clock at
        # recomputation.  The same ordered event history therefore yields the
        # same state even when it is replayed on a later day.
        days_since = max(0.0, (current_as_of - prior_evidence_at).total_seconds() / 86400.0)
        decay_factor = math.exp(-days_since / 14.0)
        previous_scores = {
            "facts": previous.facts_mastery,
            "procedures": previous.procedures_mastery,
            "strategies": previous.strategies_mastery,
            "rationales": previous.rationales_mastery,
        }
        time_decay = {dimension: clamp01(previous_scores[dimension] * decay_factor) for dimension in DIMENSIONS}
    else:
        time_decay = current_scores

    _emit_adaptive_trace(
        "2 fusion_inputs "
        f"user={user.username} goal_id={goal.id} event_id={current_event.id} concept={concept_key} "
        f"concept_events={len(concept_events)} goal_events={len(goal_events)} "
        f"weights=LR:{_fmt_score(FUSION_WEIGHTS['local_recent'])},"
        f"GH:{_fmt_score(FUSION_WEIGHTS['global_history'])},"
        f"CS:{_fmt_score(FUSION_WEIGHTS['current_score'])},"
        f"TD:{_fmt_score(FUSION_WEIGHTS['time_decay'])}"
    )

    fused_raw = {}
    for dimension in DIMENSIONS:
        fused_raw[dimension] = (
            FUSION_WEIGHTS["local_recent"] * local_recent[dimension]
            + FUSION_WEIGHTS["global_history"] * global_history[dimension]
            + FUSION_WEIGHTS["current_score"] * current_scores[dimension]
            + FUSION_WEIGHTS["time_decay"] * time_decay[dimension]
        )
    fused = _apply_dependency_adjustment(fused_raw)
    for dimension in DIMENSIONS:
        _emit_adaptive_trace(
            "3 dimension_mastery "
            f"dimension={dimension} "
            f"local_recent={_fmt_score(local_recent[dimension])} "
            f"global_history={_fmt_score(global_history[dimension])} "
            f"current_score={_fmt_score(current_scores[dimension])} "
            f"time_decay={_fmt_score(time_decay[dimension])} "
            f"fused_raw={_fmt_score(fused_raw[dimension])} "
            f"mastery_after_dependency_adjustment={_fmt_score(fused[dimension])}"
        )
    dimension_mastery = _dimension_mastery_score(fused)
    quality_score = round((0.40 * clamp01(current_event.accuracy_score)) + (0.60 * dimension_mastery), 4)
    weakest_dimension = min(DIMENSIONS, key=lambda dimension: (fused[dimension], list(DIMENSIONS).index(dimension)))
    dimension_curve_analysis = _dimension_curve_analysis(
        concept_events,
        current_event=current_event,
        current_fused=fused if current_event.source in CURVE_ELIGIBLE_SOURCES else None,
    )
    selected_curve = dimension_curve_analysis.get(weakest_dimension) or _curve_analysis_from_values([])
    pattern = str(selected_curve.get("pattern") or "insufficient_data")
    curve_confidence = clamp01(selected_curve.get("confidence"), 0.0)
    curve_evidence_count = int(selected_curve.get("evidence_count") or 0)
    dimension_curve_patterns = {
        dimension: str(dimension_curve_analysis[dimension].get("pattern") or "insufficient_data")
        for dimension in DIMENSIONS
    }
    dimension_curve_confidences = {
        dimension: clamp01(dimension_curve_analysis[dimension].get("confidence"), 0.0)
        for dimension in DIMENSIONS
    }
    curve_reason = {
        "selected_dimension": weakest_dimension,
        "selected_pattern": pattern,
        "selected_confidence": curve_confidence,
        "selected_reason": selected_curve.get("reason") or {},
        "dimensions": {
            dimension: dimension_curve_analysis[dimension].get("reason") or {}
            for dimension in DIMENSIONS
        },
    }
    tier = _feedback_tier(quality_score, pattern, curve_confidence)
    response_policy = _response_policy(
        weakest_dimension,
        tier,
        curve_pattern=pattern,
        curve_confidence=curve_confidence,
        curve_reason=curve_reason,
    )

    eligible_evidence_count = len(concept_events)
    source_summary: Dict[str, Dict[str, Any]] = {}
    for event in concept_events:
        bucket = source_summary.setdefault(
            event.source,
            {"count": 0, "mean_confidence": 0.0, "last_event_id": None},
        )
        bucket["count"] += 1
        bucket["mean_confidence"] += clamp01(event.confidence)
        bucket["last_event_id"] = event.id
    for bucket in source_summary.values():
        if bucket["count"]:
            bucket["mean_confidence"] = round(bucket["mean_confidence"] / bucket["count"], 4)

    dimension_confidences: Dict[str, float] = {}
    for dimension in DIMENSIONS:
        values = [
            normalize_dimension_scores(event.dimension_scores, event.accuracy_score)[dimension]
            for event in concept_events
        ]
        confidences = [clamp01(event.confidence) for event in concept_events]
        evidence_score = min(1.0, len(values) / 5.0)
        mean_confidence = mean(confidences) if confidences else 0.0
        # One observation has no measurable stability.  Treating its standard
        # deviation as zero previously awarded a full 0.20 stability bonus and
        # made the heuristic confidence look better calibrated than it was.
        stability = (
            1.0 - clamp01(pstdev(values) / 0.35)
            if len(values) >= 2
            else 0.0
        )
        dimension_confidences[dimension] = round(
            (0.45 * evidence_score) + (0.35 * mean_confidence) + (0.20 * stability),
            4,
        )
    mastery_confidence = round(mean(dimension_confidences.values()), 4) if dimension_confidences else 0.0
    response_policy = {
        **response_policy,
        "mastery_confidence_semantics": {
            "method": "heuristic_evidence_stability_v1",
            "calibration_status": "uncalibrated",
            "is_probability": False,
        },
    }

    _emit_adaptive_trace(
        "4 decision "
        f"dimension_mastery_score={_fmt_score(dimension_mastery)} "
        f"quality_score={_fmt_score(quality_score)} weakest_dimension={weakest_dimension} "
        f"curve_pattern={pattern} curve_confidence={_fmt_score(curve_confidence)} "
        f"curve_evidence_count={curve_evidence_count} feedback_tier={tier}"
    )
    _emit_adaptive_trace(
        "5 response_policy "
        f"tier={response_policy.get('tier')} "
        f"tier_policy={response_policy.get('tier_policy')} "
        f"weakest_dimension={response_policy.get('weakest_dimension')} "
        f"dimension_policy={response_policy.get('dimension_policy')} "
        f"curve_intervention={response_policy.get('curve_intervention')}"
    )

    state, _ = LearnerMasteryState.objects.update_or_create(
        user=user,
        learning_goal=goal,
        concept_key=concept_key,
        defaults={
            "facts_mastery": fused["facts"],
            "procedures_mastery": fused["procedures"],
            "strategies_mastery": fused["strategies"],
            "rationales_mastery": fused["rationales"],
            "dimension_mastery_score": dimension_mastery,
            "quality_score": quality_score,
            "weakest_dimension": weakest_dimension,
            "curve_pattern": pattern,
            "curve_confidence": curve_confidence,
            "curve_evidence_count": curve_evidence_count,
            "dimension_curve_patterns": dimension_curve_patterns,
            "dimension_curve_confidences": dimension_curve_confidences,
            "curve_reason": curve_reason,
            "feedback_tier": tier,
            "response_policy": response_policy,
            "last_event": current_event,
            "last_eligible_evidence": current_event,
            "event_count": len(concept_events),
            "eligible_evidence_count": eligible_evidence_count,
            "mastery_confidence": mastery_confidence,
            "dimension_confidences": dimension_confidences,
            "evidence_source_summary": source_summary,
            "policy_version": MASTERY_EVIDENCE_POLICY_VERSION,
        },
    )
    return state


def scores_from_structured_report(
    structured_report: Dict[str, Any] | None,
    *,
    evidence_sufficiency: Dict[str, str] | None = None,
) -> Dict[str, float]:
    report = structured_report if isinstance(structured_report, dict) else {}
    label_scores = {
        # Slightly wider spread than v1 so baseline can express finer separation.
        "Know-Know": 0.88,
        "Know-Don't Know": 0.58,
        "Omission": 0.36,
        "False Knowledge": 0.22,
        "Irrelevant Knowledge": 0.31,
    }
    negative_labels = {"Omission", "False Knowledge", "Irrelevant Knowledge"}

    def _flatten_labels(raw_labels: Any) -> List[str]:
        labels: List[str] = []
        if isinstance(raw_labels, str):
            raw_labels = [raw_labels]
        if isinstance(raw_labels, list):
            for item in raw_labels:
                if not isinstance(item, str):
                    continue
                # Accept both ["Know-Know", "Omission"] and ["Know-Know, Omission"].
                for token in [part.strip() for part in item.split(",")]:
                    if token:
                        labels.append(token)
        return labels

    def _normalize_confidence(value: Any) -> float | None:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if number > 1.0 and number <= 100.0:
            number = number / 100.0
        return max(0.0, min(1.0, number))

    def _severity_penalty(value: Any) -> float:
        try:
            severity = float(value)
        except (TypeError, ValueError):
            return 0.0
        severity = max(1.0, min(5.0, severity))
        # 1..5 -> 0..0.08
        return ((severity - 1.0) / 4.0) * 0.08

    scores = {}
    sufficiency = evidence_sufficiency if isinstance(evidence_sufficiency, dict) else {}
    dimension_map = {
        "Facts": "facts",
        "Strategies": "strategies",
        "Procedures": "procedures",
        "Rationales": "rationales",
    }
    for raw_name, dimension in dimension_map.items():
        dimension_sufficiency = str(
            sufficiency.get(dimension) or sufficiency.get(raw_name) or ""
        )
        if dimension_sufficiency == "explicit_not_started":
            scores[dimension] = 0.25
            continue
        parsed = report.get(raw_name) or report.get(dimension) or {}
        aspects = parsed.get("aspects") if isinstance(parsed, dict) else []
        values: List[float] = []
        weights: List[float] = []
        for aspect in aspects or []:
            if not isinstance(aspect, dict):
                continue
            labels = _flatten_labels(aspect.get("labels"))
            if not labels:
                continue
            # Defense in depth for legacy/imported reports: every label other
            # than a true omission asserts something the learner expressed.
            # Material/general reference text alone must never contribute a
            # positive (or fabricated negative) learner-state observation.
            learner_expression_labels = {
                label for label in labels if label != "Omission"
            }
            if learner_expression_labels and (
                str(aspect.get("basis") or "") not in {"student_text", "mixed"}
                or not str(aspect.get("student_quote") or "").strip()
            ):
                values.append(0.0)
                weights.append(1.0)
                continue
            mapped = [label_scores[label] for label in labels if label in label_scores]
            if not mapped:
                continue
            aspect_score = mean(mapped)
            severity_penalty = _severity_penalty(aspect.get("severity"))
            confidence = _normalize_confidence(aspect.get("confidence"))
            effective_confidence = confidence if confidence is not None else 0.5
            if dimension_sufficiency == "thin":
                effective_confidence = min(effective_confidence, 0.4)
            aspect_score = 0.5 + (effective_confidence * (aspect_score - 0.5))
            if any(label in negative_labels for label in labels):
                aspect_score -= severity_penalty * effective_confidence
            final_score = clamp01(aspect_score, default=0.5)
            values.append(final_score)
            weights.append(max(0.25, effective_confidence))

        if values:
            denominator = sum(weights) if sum(weights) > 0 else float(len(values))
            weighted_sum = sum(value * weight for value, weight in zip(values, weights))
            scores[dimension] = round(max(0.0, min(1.0, weighted_sum / denominator)), 4)
        else:
            scores[dimension] = 0.50
    return scores
