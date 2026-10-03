from __future__ import annotations

import logging
from statistics import mean
from typing import Any, Dict, List

from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    AdaptiveProbe,
    ChatConceptSignal,
    ConceptRegistryEntry,
    LearnerMasteryState,
    LearningGoal,
    UserProfile,
)

from .constants import DIMENSION_LABELS, DIMENSIONS, FUSION_WEIGHTS
from .concept_service import _existing_mastery_surface_match, extract_concept_signal
from .mastery_service import (
    _apply_dependency_adjustment,
    _curve_analysis_from_values,
    _dimension_mastery_score,
    default_concept_key_for_goal,
    normalize_dimension_scores,
)
from .mastery_evidence_policy import evidence_is_admitted

logger = logging.getLogger(__name__)


def _label_from_key(concept_key: str) -> str:
    return (concept_key or "general").replace("_", " ").strip().title()


def _concept_label_for_state(state: LearnerMasteryState) -> str:
    entry = ConceptRegistryEntry.objects.filter(
        user_id=state.user_id,
        learning_goal_id=state.learning_goal_id,
        concept_key=state.concept_key,
    ).only("concept_label").first()
    return (entry.concept_label if entry and entry.concept_label else _label_from_key(state.concept_key))[:180]


def _concept_label_map(user: UserProfile, goal: LearningGoal) -> Dict[str, str]:
    return {
        entry.concept_key: (entry.concept_label or _label_from_key(entry.concept_key))[:180]
        for entry in ConceptRegistryEntry.objects.filter(user=user, learning_goal=goal).only("concept_key", "concept_label")
    }


def _state_to_dict(state: LearnerMasteryState) -> Dict[str, Any]:
    mastery = {
        "facts": round(state.facts_mastery, 4),
        "procedures": round(state.procedures_mastery, 4),
        "strategies": round(state.strategies_mastery, 4),
        "rationales": round(state.rationales_mastery, 4),
    }
    return {
        "concept_key": state.concept_key,
        "concept_label": _concept_label_for_state(state),
        "mastery_vector": mastery,
        "dimension_mastery_score": round(state.dimension_mastery_score, 4),
        "quality_score": round(state.quality_score, 4),
        "weakest_dimension": state.weakest_dimension,
        "curve_pattern": state.curve_pattern,
        "curve_confidence": round(float(state.curve_confidence or 0.0), 4),
        "curve_evidence_count": int(state.curve_evidence_count or 0),
        "dimension_curve_patterns": state.dimension_curve_patterns or {},
        "dimension_curve_confidences": state.dimension_curve_confidences or {},
        "curve_reason": state.curve_reason or {},
        "mastery_confidence": round(float(state.mastery_confidence or 0.0), 4),
        "dimension_confidences": state.dimension_confidences or {},
        "eligible_evidence_count": int(state.eligible_evidence_count or 0),
        "evidence_source_summary": state.evidence_source_summary or {},
        "policy_version": state.policy_version or "",
        "feedback_tier": state.feedback_tier,
        "response_policy": state.response_policy or {},
        "event_count": state.event_count,
        "updated_at": state.updated_at.isoformat() if state.updated_at else "",
    }


def _average_score_dicts(items: List[Dict[str, float]], fallback: Dict[str, float]) -> Dict[str, float]:
    return {
        dimension: round(mean([item[dimension] for item in items]), 4) if items else fallback[dimension]
        for dimension in DIMENSIONS
    }


def _display_scores_for_events(events: List[AdaptiveInteractionEvent]) -> List[tuple[AdaptiveInteractionEvent, Dict[str, float]]]:
    rows: List[tuple[AdaptiveInteractionEvent, Dict[str, float]]] = []
    concept_score_history: Dict[str, List[Dict[str, float]]] = {}
    display_state_by_concept: Dict[str, Dict[str, float]] = {}
    for event in events:
        raw_scores = normalize_dimension_scores(
            event.dimension_scores if isinstance(event.dimension_scores, dict) else {},
            event.accuracy_score,
        )
        concept_key = event.concept_key or "general"
        admitted = evidence_is_admitted(event.metadata)
        concept_scores = concept_score_history.setdefault(concept_key, [])
        if admitted:
            concept_scores.append(raw_scores)

        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        mastery_after = metadata.get("mastery_after") if isinstance(metadata.get("mastery_after"), dict) else {}
        if mastery_after:
            display_scores = normalize_dimension_scores(mastery_after, event.accuracy_score)
        elif not admitted:
            # Rejected evidence can still be displayed as an audit/diagnostic
            # row, but it must not be fused with another concept's history.
            display_scores = raw_scores
        else:
            local_recent = _average_score_dicts(concept_scores[-5:], raw_scores)
            # The historical display component is also concept-scoped.  Do
            # not let a sibling concept's accepted evidence leak into a
            # context row when an older event lacks ``mastery_after``.
            global_history = _average_score_dicts(concept_scores, raw_scores)
            previous = display_state_by_concept.get(concept_key, raw_scores)
            display_scores = {}
            for dimension in DIMENSIONS:
                display_scores[dimension] = (
                    FUSION_WEIGHTS["local_recent"] * local_recent[dimension]
                    + FUSION_WEIGHTS["global_history"] * global_history[dimension]
                    + FUSION_WEIGHTS["current_score"] * raw_scores[dimension]
                    + FUSION_WEIGHTS["time_decay"] * previous[dimension]
                )
            display_scores = _apply_dependency_adjustment(display_scores)
        display_state_by_concept[concept_key] = display_scores
        rows.append((event, display_scores))
    return rows


def _compact_curve_reason(analysis: Dict[str, Any]) -> Dict[str, Any]:
    reason = analysis.get("reason") if isinstance(analysis.get("reason"), dict) else {}
    allowed = [
        "reason",
        "slope",
        "raw_slope",
        "total_delta",
        "recent_delta",
        "volatility",
        "sign_changes",
        "positive_steps",
        "negative_steps",
        "range",
        "drawdown",
        "raw_drawdown",
        "mean_grader_confidence",
        "evidence_score",
        "stability_score",
    ]
    return {key: reason[key] for key in allowed if key in reason}


def _build_goal_trend(display_rows: List[tuple[AdaptiveInteractionEvent, Dict[str, float]]]) -> Dict[str, Any]:
    eligible_rows = [
        (event, scores)
        for event, scores in display_rows
        if evidence_is_admitted(event.metadata) and event.source in {
            AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE,
        }
    ]
    if not eligible_rows:
        empty_analysis = _curve_analysis_from_values([])
        return {
            "curve_pattern": empty_analysis["pattern"],
            "curve_confidence": empty_analysis["confidence"],
            "curve_evidence_count": empty_analysis["evidence_count"],
            "curve_reason": _compact_curve_reason(empty_analysis),
            "latest_mastery_score": 0.0,
            "latest_mastery_vector": {dimension: 0.0 for dimension in DIMENSIONS},
            "weakest_dimension": "",
            "dimension_curve_patterns": {dimension: "insufficient_data" for dimension in DIMENSIONS},
            "dimension_curve_confidences": {dimension: 0.0 for dimension in DIMENSIONS},
        }

    confidences = [float(event.confidence or 0.65) for event, _scores in eligible_rows]
    aggregate_values = [_dimension_mastery_score(scores) for _event, scores in eligible_rows]
    aggregate_analysis = _curve_analysis_from_values(aggregate_values, confidences)
    dimension_analysis = {
        dimension: _curve_analysis_from_values(
            [scores[dimension] for _event, scores in eligible_rows],
            confidences,
        )
        for dimension in DIMENSIONS
    }
    latest_scores = eligible_rows[-1][1]
    weakest_dimension = min(DIMENSIONS, key=lambda dimension: (latest_scores[dimension], list(DIMENSIONS).index(dimension)))
    return {
        "curve_pattern": aggregate_analysis["pattern"],
        "curve_confidence": aggregate_analysis["confidence"],
        "curve_evidence_count": aggregate_analysis["evidence_count"],
        "curve_reason": _compact_curve_reason(aggregate_analysis),
        "latest_mastery_score": _dimension_mastery_score(latest_scores),
        "latest_mastery_vector": {dimension: round(float(latest_scores[dimension]), 4) for dimension in DIMENSIONS},
        "weakest_dimension": weakest_dimension,
        "dimension_curve_patterns": {
            dimension: str(dimension_analysis[dimension].get("pattern") or "insufficient_data")
            for dimension in DIMENSIONS
        },
        "dimension_curve_confidences": {
            dimension: round(float(dimension_analysis[dimension].get("confidence") or 0.0), 4)
            for dimension in DIMENSIONS
        },
        "dimension_curve_reasons": {
            dimension: _compact_curve_reason(dimension_analysis[dimension])
            for dimension in DIMENSIONS
        },
    }


def _recent_related_state(
    *,
    user: UserProfile,
    goal: LearningGoal,
    states_by_concept: Dict[str, LearnerMasteryState],
    related_concepts: list[str] | None = None,
) -> tuple[LearnerMasteryState | None, str]:
    for concept_key in related_concepts or []:
        if concept_key in states_by_concept:
            return states_by_concept[concept_key], "current_concept_related"
    recent_signals = (
        ChatConceptSignal.objects.filter(user=user, learning_goal=goal)
        .order_by("-created_at", "-id")[:6]
    )
    for signal in recent_signals:
        if signal.concept_key in states_by_concept:
            return states_by_concept[signal.concept_key], "recent_chat_related"
    return None, ""


def _select_primary_state(
    *,
    user: UserProfile,
    goal: LearningGoal,
    states: list[LearnerMasteryState],
    current_question: str = "",
) -> tuple[LearnerMasteryState, Dict[str, Any]]:
    baseline_key = default_concept_key_for_goal(goal)
    states_by_concept = {state.concept_key: state for state in states}
    requested: Dict[str, Any] = {
        "concept_key": "",
        "concept_label": "",
        "confidence": 0.0,
        "source": "",
    }
    if current_question:
        signal = extract_concept_signal(goal, current_question, "", user=user, allow_llm=False)
        requested = {
            "concept_key": signal.concept_key,
            "concept_label": signal.concept_label,
            "confidence": signal.confidence,
            "source": signal.source,
            "related_concepts": signal.related_concepts,
        }
        if signal.concept_key in states_by_concept and not (
            signal.concept_key == baseline_key and signal.source == ChatConceptSignal.SOURCE_FALLBACK
        ):
            return states_by_concept[signal.concept_key], {
                "selection_reason": "current_concept_exact",
                "requested_concept": requested,
                "baseline_concept": baseline_key,
            }
        # If the identity layer correctly abstained on a free-form sentence,
        # retain continuity with an already tracked concept using the local
        # mastery vocabulary.  This is selection-only; it never verifies or
        # admits a new identity.
        surface_state = _existing_mastery_surface_match(user, goal, current_question)
        if surface_state is not None and surface_state.concept_key in states_by_concept:
            requested = {
                **requested,
                "concept_key": surface_state.concept_key,
                "concept_label": _concept_label_for_state(surface_state),
                "confidence": max(float(requested.get("confidence") or 0.0), 0.82),
                "source": ChatConceptSignal.SOURCE_VECTOR_MATCH,
            }
            return states_by_concept[surface_state.concept_key], {
                "selection_reason": "current_concept_exact",
                "requested_concept": requested,
                "baseline_concept": baseline_key,
            }
        # Preserve a recent, goal-scoped chat topic when the current sentence
        # has no accepted identity and there is no stronger exact match.
        recent_signal = (
            ChatConceptSignal.objects.filter(user=user, learning_goal=goal)
            .order_by("-created_at", "-id")
            .first()
        )
        if recent_signal and recent_signal.concept_key in states_by_concept:
            return states_by_concept[recent_signal.concept_key], {
                "selection_reason": "recent_chat_related",
                "requested_concept": requested,
                "baseline_concept": baseline_key,
            }
        related_state, related_reason = _recent_related_state(
            user=user,
            goal=goal,
            states_by_concept=states_by_concept,
            related_concepts=signal.related_concepts,
        )
        if related_state:
            return related_state, {
                "selection_reason": related_reason,
                "requested_concept": requested,
                "baseline_concept": baseline_key,
            }

    non_baseline_states = [state for state in states if state.concept_key != baseline_key]
    if non_baseline_states:
        return min(non_baseline_states, key=lambda state: (state.quality_score, -state.updated_at.timestamp() if state.updated_at else 0)), {
            "selection_reason": "lowest_quality_non_baseline",
            "requested_concept": requested,
            "baseline_concept": baseline_key,
        }

    if baseline_key in states_by_concept:
        return states_by_concept[baseline_key], {
            "selection_reason": "baseline_fallback",
            "requested_concept": requested,
            "baseline_concept": baseline_key,
        }

    return states[0], {
        "selection_reason": "lowest_quality_fallback",
        "requested_concept": requested,
        "baseline_concept": baseline_key,
    }


def _safe_prompt_text(value: Any, limit: int = 240) -> str:
    text = " ".join(str(value or "").split())
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)].rstrip() + "..."


def _safe_prompt_list(value: Any, *, limit: int, item_limit: int = 180) -> List[str]:
    if not isinstance(value, list):
        return []
    safe_items: List[str] = []
    for item in value:
        if isinstance(item, dict):
            text = item.get("tag") or item.get("label") or item.get("name") or item.get("evidence_pattern") or ""
        else:
            text = item
        clean = _safe_prompt_text(text, item_limit)
        if clean:
            safe_items.append(clean)
        if len(safe_items) >= limit:
            break
    return safe_items


def _metadata_list(metadata: Dict[str, Any], report: Dict[str, Any], key: str) -> List[Any]:
    value = report.get(key)
    if not isinstance(value, list):
        value = metadata.get(key)
    if not isinstance(value, list):
        probe_grading = metadata.get("probe_grading") if isinstance(metadata.get("probe_grading"), dict) else {}
        value = probe_grading.get(key)
    return value if isinstance(value, list) else []


def _safe_probe_diagnostic(
    event: AdaptiveInteractionEvent,
    *,
    concept_labels: Dict[str, str],
) -> Dict[str, Any]:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    report = metadata.get("diagnostic_report") if isinstance(metadata.get("diagnostic_report"), dict) else {}
    expected_rubric = metadata.get("expected_rubric") if isinstance(metadata.get("expected_rubric"), dict) else {}
    concept_key = str(event.concept_key or expected_rubric.get("concept_key") or "general")
    missing_steps = _safe_prompt_list(_metadata_list(metadata, report, "missing_steps"), limit=2)
    incorrect_steps = _safe_prompt_list(_metadata_list(metadata, report, "incorrect_steps"), limit=2)
    missing_checks = _safe_prompt_list(_metadata_list(metadata, report, "missing_checks"), limit=2)
    misconception_tags = _safe_prompt_list(_metadata_list(metadata, report, "misconception_tags"), limit=3, item_limit=80)
    primary_gap = _safe_prompt_text(metadata.get("primary_gap") or report.get("primary_gap") or "", 220)
    if not primary_gap:
        primary_gap = next(
            (item for item in [*missing_steps, *incorrect_steps, *missing_checks, *misconception_tags] if item),
            "",
        )
    recommended_follow_up = _safe_prompt_text(
        metadata.get("recommended_follow_up")
        or report.get("recommended_follow_up")
        or expected_rubric.get("feedback_focus")
        or "",
        240,
    )
    if not recommended_follow_up and primary_gap:
        recommended_follow_up = "Ask one focused follow-up that targets this gap."
    feedback_summary = _safe_prompt_text(metadata.get("feedback_summary") or report.get("feedback_summary") or "", 260)
    if not any([primary_gap, recommended_follow_up, feedback_summary, missing_steps, incorrect_steps, misconception_tags]):
        return {}
    return {
        "concept_key": concept_key,
        "concept_label": _safe_prompt_text(
            expected_rubric.get("concept_label") or concept_labels.get(concept_key) or _label_from_key(concept_key),
            180,
        ),
        "target_dimension": _safe_prompt_text(
            metadata.get("target_dimension") or expected_rubric.get("target_dimension") or "",
            40,
        ),
        "diagnostic_type": _safe_prompt_text(
            metadata.get("diagnostic_type") or expected_rubric.get("diagnostic_type") or "",
            60,
        ),
        "answer_schema": _safe_prompt_text(metadata.get("answer_schema") or expected_rubric.get("answer_schema") or "", 80),
        "primary_gap": primary_gap,
        "recommended_follow_up": recommended_follow_up,
        "feedback_summary": feedback_summary,
        "missing_steps": missing_steps,
        "incorrect_steps": incorrect_steps,
        "misconception_tags": misconception_tags,
        "created_at": event.created_at.isoformat() if event.created_at else "",
        "source_probe_id": metadata.get("probe_id"),
    }


def _probe_event_resolves_gap(event: AdaptiveInteractionEvent, diagnostic: Dict[str, Any]) -> bool:
    primary_gap = str(diagnostic.get("primary_gap") or "").strip().lower().rstrip(".")
    no_major_gap = primary_gap in {"none", "no major gap detected", "no major gaps detected"}
    open_reasoning_gaps = bool(
        diagnostic.get("missing_steps")
        or diagnostic.get("incorrect_steps")
        or diagnostic.get("misconception_tags")
    )
    if no_major_gap:
        return True
    if primary_gap:
        return False
    return float(event.accuracy_score or 0.0) >= 0.80 and not open_reasoning_gaps


def _concept_targets_for_probe_memory(selection: Dict[str, Any]) -> tuple[list[str], list[str]]:
    requested = selection.get("requested_concept") if isinstance(selection.get("requested_concept"), dict) else {}
    concept_key = str(requested.get("concept_key") or "").strip()
    source = str(requested.get("source") or "").strip()
    if not concept_key or source == ChatConceptSignal.SOURCE_FALLBACK:
        return [], []
    related = requested.get("related_concepts") if isinstance(requested.get("related_concepts"), list) else []
    related_keys: list[str] = []
    for key in related:
        clean = str(key or "").strip()
        if clean and clean != concept_key and clean not in related_keys:
            related_keys.append(clean)
    return [concept_key], related_keys


def _collect_probe_diagnostics_for_concepts(
    *,
    events: List[AdaptiveInteractionEvent],
    concept_keys: list[str],
    concept_labels: Dict[str, str],
    limit: int,
) -> List[Dict[str, Any]]:
    if not concept_keys or limit <= 0:
        return []
    allowed = set(concept_keys)
    diagnostics: List[Dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for event in reversed(events):
        if event.source != AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE:
            continue
        concept_key = str(event.concept_key or "general")
        if concept_key not in allowed:
            continue
        diagnostic = _safe_probe_diagnostic(event, concept_labels=concept_labels)
        target_dimension = str(diagnostic.get("target_dimension") or "")
        pair = (concept_key, target_dimension)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)
        if not diagnostic or _probe_event_resolves_gap(event, diagnostic):
            continue
        diagnostics.append(diagnostic)
        if len(diagnostics) >= limit:
            break
    return diagnostics


def _recent_probe_diagnostics_for_context(
    *,
    events: List[AdaptiveInteractionEvent],
    selection: Dict[str, Any],
    concept_labels: Dict[str, str],
) -> List[Dict[str, Any]]:
    exact_keys, related_keys = _concept_targets_for_probe_memory(selection)
    exact_diagnostics = _collect_probe_diagnostics_for_concepts(
        events=events,
        concept_keys=exact_keys,
        concept_labels=concept_labels,
        limit=3,
    )
    related_diagnostics = _collect_probe_diagnostics_for_concepts(
        events=events,
        concept_keys=related_keys,
        concept_labels=concept_labels,
        limit=2,
    )
    return [*exact_diagnostics, *related_diagnostics]


def build_adaptive_mastery_context(
    username: str,
    learning_goal_id: int,
    current_question: str = "",
) -> Dict[str, Any]:
    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(user=user, id=int(learning_goal_id)).first() if user else None
    if not user or not goal:
        return {"available": False, "states": []}
    events = list(
        AdaptiveInteractionEvent.objects.filter(user=user, learning_goal=goal)
        .order_by("created_at", "id")
    )
    goal_trend = _build_goal_trend(_display_scores_for_events(events))
    states = list(
        LearnerMasteryState.objects.filter(user=user, learning_goal=goal)
        .order_by("quality_score", "-updated_at")
    )
    if not states:
        return {"available": False, "states": []}
    primary, selection = _select_primary_state(
        user=user,
        goal=goal,
        states=states,
        current_question=current_question,
    )
    concept_labels = _concept_label_map(user, goal)
    recent_probe_diagnostics = _recent_probe_diagnostics_for_context(
        events=events,
        selection=selection,
        concept_labels=concept_labels,
    )
    top_states = [primary]
    for state in states:
        if state.id != primary.id and len(top_states) < 4:
            top_states.append(state)
    return {
        "available": True,
        "goal_trend": goal_trend,
        "primary": _state_to_dict(primary),
        "states": [_state_to_dict(state) for state in top_states],
        "selection": selection,
        "recent_probe_diagnostics": recent_probe_diagnostics,
    }


def format_adaptive_context_for_prompt(context: Dict[str, Any] | None) -> str:
    if not isinstance(context, dict) or not context.get("available"):
        return "- No adaptive mastery state is available yet; use the current question and self-assessment normally."
    primary = context.get("primary") or {}
    goal_trend = context.get("goal_trend") if isinstance(context.get("goal_trend"), dict) else {}
    selection = context.get("selection") if isinstance(context.get("selection"), dict) else {}
    requested = selection.get("requested_concept") if isinstance(selection.get("requested_concept"), dict) else {}
    mastery = primary.get("mastery_vector") or {}
    policy = primary.get("response_policy") or {}
    curve_reason = primary.get("curve_reason") if isinstance(primary.get("curve_reason"), dict) else {}
    selected_reason = curve_reason.get("selected_reason") if isinstance(curve_reason.get("selected_reason"), dict) else {}
    lines = [
        f"- Current question concept: {requested.get('concept_key') or 'not detected'}"
        + (f" ({requested.get('concept_label')})" if requested.get("concept_label") else ""),
        f"- Overall goal trend: {goal_trend.get('curve_pattern') or 'insufficient_data'}",
        f"- Overall goal confidence: {float(goal_trend.get('curve_confidence') or 0.0):.2f}",
        f"- Overall goal evidence count: {int(goal_trend.get('curve_evidence_count') or 0)}",
        f"- Overall goal weakest dimension: {DIMENSION_LABELS.get(goal_trend.get('weakest_dimension'), goal_trend.get('weakest_dimension') or 'unknown')}",
        f"- Selected mastery concept: {primary.get('concept_label') or primary.get('concept_key') or 'general'} ({primary.get('concept_key') or 'general'})",
        f"- Selection reason: {selection.get('selection_reason') or 'default'}",
        "- Mastery vector: "
        + ", ".join(f"{DIMENSION_LABELS[d]}={float(mastery.get(d, 0.0)):.2f}" for d in DIMENSIONS),
        f"- Weakest dimension: {DIMENSION_LABELS.get(primary.get('weakest_dimension'), primary.get('weakest_dimension') or 'unknown')}",
        f"- Concept curve pattern: {primary.get('curve_pattern') or 'insufficient_data'}",
        f"- Concept curve confidence: {float(primary.get('curve_confidence') or 0.0):.2f}",
        f"- Concept curve evidence count: {int(primary.get('curve_evidence_count') or 0)}",
        f"- Concept curve reason: {selected_reason.get('reason') or 'not_enough_evidence'}",
        f"- Feedback tier: {primary.get('feedback_tier') or 'CONSOLIDATE'}",
        f"- Recommended intervention: {policy.get('tier_policy') or ''} {policy.get('dimension_policy') or ''}".strip(),
        f"- Curve intervention: {policy.get('curve_intervention') or 'Use normal weakest-dimension support.'}",
    ]
    recent_probe_diagnostics = (
        context.get("recent_probe_diagnostics") if isinstance(context.get("recent_probe_diagnostics"), list) else []
    )
    if recent_probe_diagnostics:
        lines.append("- Recent concept-specific probe diagnostics:")
        for item in recent_probe_diagnostics[:5]:
            if not isinstance(item, dict):
                continue
            concept = item.get("concept_label") or item.get("concept_key") or "general"
            concept_key = item.get("concept_key") or "general"
            target_dimension = item.get("target_dimension") or "unknown"
            dimension_label = DIMENSION_LABELS.get(target_dimension, target_dimension)
            diagnostic_type = item.get("diagnostic_type") or "probe"
            lines.append(
                f"  - Concept: {concept} ({concept_key}); "
                f"Dimension: {dimension_label}; Diagnostic: {diagnostic_type}; "
                f"Primary gap: {item.get('primary_gap') or 'not specified'}; "
                f"Recommended follow-up: {item.get('recommended_follow_up') or 'use focused repair support'}"
            )
            missing_steps = item.get("missing_steps") if isinstance(item.get("missing_steps"), list) else []
            incorrect_steps = item.get("incorrect_steps") if isinstance(item.get("incorrect_steps"), list) else []
            misconception_tags = item.get("misconception_tags") if isinstance(item.get("misconception_tags"), list) else []
            reasoning_notes = []
            if missing_steps:
                reasoning_notes.append("missing steps: " + "; ".join(str(step) for step in missing_steps[:2]))
            if incorrect_steps:
                reasoning_notes.append("incorrect steps: " + "; ".join(str(step) for step in incorrect_steps[:2]))
            if misconception_tags:
                reasoning_notes.append("misconceptions: " + ", ".join(str(tag) for tag in misconception_tags[:3]))
            if reasoning_notes:
                lines.append("    Missing/incorrect reasoning: " + " | ".join(reasoning_notes))
    return "\n".join(lines)


def build_progress_payload(username: str, learning_goal_id: int) -> Dict[str, Any]:
    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(user=user, id=int(learning_goal_id)).first() if user else None
    if not user or not goal:
        return {"ok": False, "error": "learning_goal_not_found"}

    events = list(
        AdaptiveInteractionEvent.objects.filter(user=user, learning_goal=goal)
        .order_by("created_at", "id")
    )
    curves = {dimension: [] for dimension in DIMENSIONS}
    display_rows = _display_scores_for_events(events)
    concept_labels = _concept_label_map(user, goal)
    for idx, (event, display_scores) in enumerate(display_rows, start=1):
        concept_key = event.concept_key or "general"
        for dimension in DIMENSIONS:
            value = float(display_scores.get(dimension, event.accuracy_score or 0.0) or 0.0)
            if value > 1.0 and value <= 100.0:
                value = value / 100.0
            curves[dimension].append({
                "x": idx,
                "y": round(max(0.0, min(1.0, value)), 4),
                "timestamp": event.created_at.isoformat() if event.created_at else "",
                "concept_key": concept_key,
                "concept_label": concept_labels.get(concept_key) or _label_from_key(concept_key),
                "source": event.source,
            })

    goal_trend = _build_goal_trend(display_rows)
    states = list(
        LearnerMasteryState.objects.filter(user=user, learning_goal=goal)
        .order_by("quality_score", "-updated_at")
    )
    state = states[0] if states else None
    pending_probe = (
        AdaptiveProbe.objects.filter(user=user, learning_goal=goal, status=AdaptiveProbe.STATUS_PENDING)
        .order_by("due_at", "-created_at")
        .first()
    )
    recent_events: List[Dict[str, Any]] = []
    for event in reversed(events[-5:]):
        recent_events.append({
            "id": event.id,
            "concept_key": event.concept_key,
            "concept_label": concept_labels.get(event.concept_key or "general") or _label_from_key(event.concept_key or "general"),
            "source": event.source,
            "accuracy_score": round(float(event.accuracy_score or 0.0), 4),
            "created_at": event.created_at.isoformat() if event.created_at else "",
        })
    event_log: List[Dict[str, Any]] = []
    for event in reversed(events):
        event_log.append({
            "id": event.id,
            "concept_key": event.concept_key,
            "concept_label": concept_labels.get(event.concept_key or "general") or _label_from_key(event.concept_key or "general"),
            "source": event.source,
            "accuracy_score": round(float(event.accuracy_score or 0.0), 4),
            "created_at": event.created_at.isoformat() if event.created_at else "",
        })

    def _safe_probe_rubric(probe: AdaptiveProbe) -> Dict[str, Any]:
        rubric = probe.expected_rubric if isinstance(probe.expected_rubric, dict) else {}
        raw_items = rubric.get("probe_items") if isinstance(rubric.get("probe_items"), list) else []
        safe_items: List[Dict[str, Any]] = []
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            safe_items.append({
                "item_id": str(item.get("item_id") or ""),
                "concept_key": str(item.get("concept_key") or ""),
                "concept_label": str(item.get("concept_label") or ""),
                "target_dimension": str(item.get("target_dimension") or ""),
                "diagnostic_type": str(item.get("diagnostic_type") or ""),
                "question_text": str(item.get("question_text") or ""),
                "answer_schema": str(item.get("answer_schema") or "short_explanation"),
                "difficulty": str(item.get("difficulty") or rubric.get("difficulty") or ""),
                "feedback_focus": str(item.get("feedback_focus") or ""),
            })
        raw_plan = rubric.get("probe_plan") if isinstance(rubric.get("probe_plan"), dict) else {}
        safe_coverage_targets: List[Dict[str, Any]] = []
        for target in raw_plan.get("coverage_targets") if isinstance(raw_plan.get("coverage_targets"), list) else []:
            if not isinstance(target, dict):
                continue
            safe_coverage_targets.append({
                "concept_key": str(target.get("concept_key") or ""),
                "concept_label": str(target.get("concept_label") or ""),
                "target_dimension": str(target.get("target_dimension") or ""),
                "diagnostic_type": str(target.get("diagnostic_type") or ""),
                "role": str(target.get("role") or ""),
            })
        return {
            "probe_version": str(rubric.get("probe_version") or ""),
            "probe_items": safe_items,
            "difficulty": str(rubric.get("difficulty") or ""),
            "risk_level": str(rubric.get("risk_level") or raw_plan.get("risk_level") or ""),
            "probe_plan": {
                "selection_reason": str(raw_plan.get("selection_reason") or ""),
                "coverage_targets": safe_coverage_targets,
                "risk_level": str(raw_plan.get("risk_level") or rubric.get("risk_level") or ""),
            },
            "selected_priority_components": (
                rubric.get("selected_priority_components")
                if isinstance(rubric.get("selected_priority_components"), dict)
                else {}
            ),
        }

    latest_probe_feedback: List[Dict[str, Any]] = []
    for event in reversed(events):
        if event.source != AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE:
            continue
        metadata = event.metadata if isinstance(event.metadata, dict) else {}
        diagnostic_report = metadata.get("diagnostic_report") if isinstance(metadata.get("diagnostic_report"), dict) else {}
        feedback_summary = str(metadata.get("feedback_summary") or diagnostic_report.get("feedback_summary") or "").strip()
        primary_gap = str(metadata.get("primary_gap") or diagnostic_report.get("primary_gap") or "").strip()
        recommended_follow_up = str(
            metadata.get("recommended_follow_up") or diagnostic_report.get("recommended_follow_up") or ""
        ).strip()
        if not any([feedback_summary, primary_gap, recommended_follow_up]):
            continue
        concept_key = event.concept_key or "general"
        latest_probe_feedback.append({
            "probe_id": metadata.get("probe_id"),
            "probe_item_id": str(metadata.get("probe_item_id") or ""),
            "concept_key": concept_key,
            "concept_label": concept_labels.get(concept_key) or _label_from_key(concept_key),
            "target_dimension": str(metadata.get("target_dimension") or ""),
            "feedback_summary": feedback_summary,
            "primary_gap": primary_gap,
            "recommended_follow_up": recommended_follow_up,
            "created_at": event.created_at.isoformat() if event.created_at else "",
        })
        if len(latest_probe_feedback) >= 4:
            break

    return {
        "ok": True,
        "curves": curves,
        "goal_trend": goal_trend,
        "current_state": _state_to_dict(state) if state else None,
        "concept_states": [_state_to_dict(item) for item in states],
        "recent_events": recent_events,
        "event_log": event_log,
        "latest_probe_feedback": latest_probe_feedback,
        "pending_probe": {
            "id": pending_probe.id,
            "concept_key": pending_probe.concept_key,
            "target_dimension": pending_probe.target_dimension,
            "question_text": pending_probe.question_text,
            "expected_rubric": _safe_probe_rubric(pending_probe),
            "due_at": pending_probe.due_at.isoformat() if pending_probe.due_at else "",
        } if pending_probe else None,
    }
