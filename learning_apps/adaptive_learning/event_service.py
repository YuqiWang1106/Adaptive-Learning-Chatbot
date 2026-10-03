from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any, Dict

from learning_apps.infrastructure.services.trace_context import current_trace_context
from learning_apps.infrastructure.services.task_dispatcher import dispatch_task
from learning_apps.persistence.models import AdaptiveInteractionEvent, AdaptiveProbe, LearnerMasteryState, LearningGoal, UserProfile

from .constants import DIMENSIONS
from .concept_identity_service import (
    canonicalize_concept_key,
    resolution_metadata,
    resolve_concept_identity,
    resolve_trusted_canonical_key,
)
from .grader_service import (
    AdaptiveGradingResult,
    adjust_probe_scores_for_mastery_update,
    grade_student_answer,
    heuristic_grading_from_score,
    normalize_concept_key,
)
from .learner_state_service import observe_misconceptions
from .mastery_service import (
    _emit_adaptive_trace,
    record_event_and_update_state,
)
from .probe_diagnostic_service import normalize_diagnostic_report

logger = logging.getLogger(__name__)


def queue_probe_response_event(
    *,
    username: str,
    learning_goal_id: int,
    probe_id: int,
    concept_key: str,
    target_dimension: str,
    question_text: str,
    student_answer: str,
    expected_rubric: Dict[str, Any] | None = None,
    probe_version: str = "",
    probe_item_id: str = "",
    answer_schema: str = "",
) -> None:
    try:
        from .tasks import grade_adaptive_interaction
    except ImportError:
        grade_adaptive_interaction = None

    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(user=user, id=int(learning_goal_id)).first() if user else None
    original_concept_key = concept_key
    identity_metadata: Dict[str, Any] = {}
    if user and goal:
        canonical_original = canonicalize_concept_key(
            original_concept_key,
            domain=goal.domain or "",
            branch=goal.branch or "",
        )
        resolution = resolve_concept_identity(
            user,
            goal,
            canonical_original,
            source_context="probe_response",
            allow_llm=False,
        )
        concept_key = resolution.concept_key
        identity_metadata = resolution_metadata(resolution, original_concept_key=original_concept_key)
    probe_trace_id = str(
        AdaptiveProbe.objects.filter(
            id=int(probe_id),
            user=user,
            learning_goal=goal,
        ).values_list("trace_id", flat=True).first()
        or ""
    )
    trace_id = current_trace_context().trace_id or probe_trace_id

    payload = {
        "username": username,
        "learning_goal_id": int(learning_goal_id),
        "source": AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE,
        "question_text": question_text or "",
        "tutor_answer": "",
        "student_answer": student_answer or "",
        "score_hint": None,
        "concept_key": concept_key,
        "target_dimension": target_dimension,
        "metadata": {
            "probe_id": probe_id,
            "expected_rubric": expected_rubric or {},
            "probe_version": probe_version,
            "probe_item_id": probe_item_id,
            "answer_schema": answer_schema,
            **identity_metadata,
            "trace_id": trace_id,
            "probe_trace_id": probe_trace_id,
        },
    }
    _emit_adaptive_trace(
        "probe_response_event_queued "
        f"user={username} goal_id={learning_goal_id} probe_id={probe_id} "
        f"concept={concept_key} target_dimension={target_dimension}"
    )
    dispatch_task(
        grade_adaptive_interaction,
        payload,
        fallback=lambda: run_adaptive_grading(payload),
    )


def _resolve_payload_concept(
    *,
    user: UserProfile | None,
    goal: LearningGoal | None,
    payload: Dict[str, Any],
    source: str,
    expected_rubric: Dict[str, Any] | None = None,
) -> tuple[str, Dict[str, Any]]:
    original = str(payload.get("concept_key") or (expected_rubric or {}).get("concept_key") or "").strip()
    if not user or not goal:
        return normalize_concept_key(original, "general"), {}
    canonical_original = (
        canonicalize_concept_key(original, domain=goal.domain or "", branch=goal.branch or "")
        if original
        else ""
    )
    text = "\n".join(
        part
        for part in [
            original,
            str((expected_rubric or {}).get("concept_label") or ""),
            str((expected_rubric or {}).get("question_text") or ""),
            str(payload.get("question_text") or ""),
            str(payload.get("tutor_answer") or ""),
        ]
        if str(part or "").strip()
    )
    resolution = None
    rubric_key = str((expected_rubric or {}).get("concept_key") or "").strip()
    # Probe rubrics are server-generated canonical identities.  Resolve that
    # key through the trusted path before considering learner-facing text;
    # arbitrary/static aliases remain candidate-only and cannot grant mastery.
    if (
        canonical_original
        and rubric_key
        and canonicalize_concept_key(rubric_key, domain=goal.domain or "", branch=goal.branch or "")
        == canonical_original
        and source == AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE
    ):
        resolution = resolve_trusted_canonical_key(
            user,
            goal,
            canonical_original,
            source_context=source,
        )
    if canonical_original:
        resolution = resolution or resolve_concept_identity(
            user,
            goal,
            canonical_original,
            source_context=source,
            allow_llm=False,
        )
    if resolution is None:
        resolution = resolve_concept_identity(
            user,
            goal,
            text,
            source_context=source,
            allow_llm=False,
        )
    resolved_key = resolution.concept_key
    metadata = resolution_metadata(resolution, original_concept_key=original)
    metadata["resolved_concept_key"] = resolved_key
    return resolved_key, metadata


def run_adaptive_grading(payload: Dict[str, Any]) -> None:
    username = str(payload.get("username") or "")
    learning_goal_id = int(payload.get("learning_goal_id") or 0)
    if not username or not learning_goal_id:
        return
    source = str(payload.get("source") or "")
    if source != AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE:
        _emit_adaptive_trace(
            f"grading_blocked user={username} goal_id={learning_goal_id} reason=unsupported_source"
        )
        return
    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(user=user, id=learning_goal_id).first() if user else None
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    expected_rubric = metadata.get("expected_rubric") if isinstance(metadata.get("expected_rubric"), dict) else {}
    resolved_concept_key, identity_metadata = _resolve_payload_concept(
        user=user,
        goal=goal,
        payload=payload,
        source=source,
        expected_rubric=expected_rubric,
    )
    _emit_adaptive_trace(
        "grading_start "
        f"user={username} goal_id={learning_goal_id} source={source} "
        f"concept_hint={normalize_concept_key(payload.get('concept_key'), 'general')} "
        f"resolved_concept={resolved_concept_key} "
        f"target_dimension={str(payload.get('target_dimension') or '') or 'not_specified'} "
        f"score_hint={payload.get('score_hint')}"
    )
    try:
        grading = grade_student_answer(
            question_text=str(payload.get("question_text") or ""),
            student_answer=str(payload.get("student_answer") or ""),
            tutor_answer=str(payload.get("tutor_answer") or ""),
            concept_key=resolved_concept_key,
            target_dimension=str(payload.get("target_dimension") or ""),
            score_hint=payload.get("score_hint"),
            learning_goal=(goal.preference_text or goal.title or "") if goal else "",
            expected_rubric=expected_rubric,
        )
    except Exception as exc:
        logger.warning("Adaptive grading failed unexpectedly; using heuristic fallback: %s", exc)
        _emit_adaptive_trace(
            "grading_fallback "
            f"user={username} goal_id={learning_goal_id} source={source} error={str(exc)[:180]}"
        )
        grading = heuristic_grading_from_score(
            payload.get("score_hint"),
            concept_key=resolved_concept_key,
            target_dimension=str(payload.get("target_dimension") or ""),
            evidence=str(exc)[:300],
            expected_rubric=expected_rubric,
            student_answer=str(payload.get("student_answer") or ""),
        )
    if grading.concept_key != resolved_concept_key:
        grading = replace(grading, concept_key=resolved_concept_key)
    _emit_adaptive_trace(
        "grading_result "
        f"user={username} goal_id={learning_goal_id} source={source} "
        f"accuracy_score={grading.accuracy_score:.4f} confidence={grading.confidence:.4f} "
        "dimension_scores="
        + ", ".join(
            f"{dimension}={grading.dimension_scores.get(dimension, 0.0):.4f}"
            for dimension in sorted(grading.dimension_scores)
        )
    )
    event_metadata = {**dict(metadata), **identity_metadata}
    raw_payload = grading.raw_payload if isinstance(grading.raw_payload, dict) else {}
    target_dimension = str(payload.get("target_dimension") or "")
    concept_key = resolved_concept_key
    if source == AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE and target_dimension in DIMENSIONS and user and goal:
        raw_dimension_scores = dict(grading.dimension_scores)
        previous_state = LearnerMasteryState.objects.filter(
            user=user,
            learning_goal=goal,
            concept_key=concept_key,
        ).first()
        previous_scores = None
        if previous_state:
            previous_scores = {
                "facts": previous_state.facts_mastery,
                "procedures": previous_state.procedures_mastery,
                "strategies": previous_state.strategies_mastery,
                "rationales": previous_state.rationales_mastery,
            }
        mastery_update_scores = adjust_probe_scores_for_mastery_update(
            raw_dimension_scores,
            target_dimension=target_dimension,
            previous_scores=previous_scores,
        )
        event_metadata["target_dimension"] = target_dimension
        event_metadata["raw_dimension_scores"] = raw_dimension_scores
        event_metadata["mastery_update_scores"] = mastery_update_scores
        grading = AdaptiveGradingResult(
            accuracy_score=grading.accuracy_score,
            dimension_scores=mastery_update_scores,
            labels=grading.labels,
            concept_key=resolved_concept_key,
            evidence=grading.evidence,
            confidence=grading.confidence,
            latency_ms=grading.latency_ms,
            raw_payload=grading.raw_payload,
        )
        _emit_adaptive_trace(
            "probe_mastery_update_scores "
            f"user={username} goal_id={learning_goal_id} concept={concept_key} target_dimension={target_dimension} "
            "raw="
            + ", ".join(f"{dimension}={raw_dimension_scores.get(dimension, 0.0):.4f}" for dimension in DIMENSIONS)
            + " adjusted="
            + ", ".join(f"{dimension}={mastery_update_scores.get(dimension, 0.0):.4f}" for dimension in DIMENSIONS)
        )
    probe_grading = {
        "matched_checks": raw_payload.get("matched_checks") or [],
        "missing_checks": raw_payload.get("missing_checks") or [],
        "misconception_tags": raw_payload.get("misconception_tags") or [],
    }
    if source == AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE:
        diagnostic_report = normalize_diagnostic_report(
            raw_payload,
            expected_rubric,
            str(payload.get("student_answer") or ""),
        )
        event_metadata["diagnostic_report"] = diagnostic_report
        event_metadata["primary_gap"] = diagnostic_report["primary_gap"]
        event_metadata["recommended_follow_up"] = diagnostic_report["recommended_follow_up"]
        event_metadata["feedback_summary"] = diagnostic_report["feedback_summary"]
        if raw_payload.get("fallback") or diagnostic_report.get("fallback"):
            event_metadata["fallback"] = True
        probe_grading = {
            "matched_checks": diagnostic_report.get("matched_checks") or [],
            "missing_checks": diagnostic_report.get("missing_checks") or [],
            "misconception_tags": diagnostic_report.get("misconception_tags") or [],
            "missing_steps": diagnostic_report.get("missing_steps") or [],
            "incorrect_steps": diagnostic_report.get("incorrect_steps") or [],
        }
    if any(probe_grading.values()):
        event_metadata["probe_grading"] = probe_grading
        event_metadata["matched_checks"] = probe_grading["matched_checks"]
        event_metadata["missing_checks"] = probe_grading["missing_checks"]
        event_metadata["misconception_tags"] = probe_grading["misconception_tags"]
    event = record_event_and_update_state(
        username=username,
        learning_goal_id=learning_goal_id,
        source=source,
        question_text=str(payload.get("question_text") or ""),
        student_answer=str(payload.get("student_answer") or ""),
        grading=grading,
        metadata=event_metadata,
    )
    misconception_tags = probe_grading.get("misconception_tags") or []
    if event and misconception_tags:
        try:
            observe_misconceptions(
                event=event,
                tags=misconception_tags,
                confidence=grading.confidence,
            )
        except Exception as exc:
            logger.error(
                "Failed to persist misconception state for event=%s: %s",
                event.id,
                exc,
            )
    _emit_adaptive_trace(
        "grading_done "
        f"user={username} goal_id={learning_goal_id} source={source} event_id={getattr(event, 'id', None)}"
    )
