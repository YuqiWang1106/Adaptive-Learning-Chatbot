from __future__ import annotations

import json
import logging
import re
from typing import Any, Dict, Iterable, List

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from learning_apps.chat.services.text_service import truncate_for_prompt
from learning_apps.infrastructure.services.llm_gateway import llm_gateway
from learning_apps.infrastructure.services.trace_context import current_trace_context, normalize_trace_id
from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    AdaptiveProbe,
    AdaptiveProbeOffer,
    ChatConceptSignal,
    ConceptRegistryEntry,
    LearnerMasteryState,
    LearnerPerceivedState,
    LearningGoal,
    UserProfile,
)
from learning_apps.chat.services.conversation_repository_service import visible_history_queryset

from .concept_identity_service import (
    canonicalize_concept_key,
    resolution_metadata,
    resolve_concept_identity,
)
from .constants import DIMENSION_LABELS, DIMENSION_POLICY, DIMENSIONS
from .event_service import queue_probe_response_event
from .grader_service import normalize_concept_key
from .mastery_service import CURVE_CONFIDENCE_THRESHOLD, _emit_adaptive_trace, clamp01, default_concept_key_for_goal
from .probe_planner_service import (
    DEFAULT_MICRO_ITEM_COUNT,
    MAX_MICRO_ITEM_COUNT,
    MICRO_PROBE_VERSION,
    build_probe_plan,
    diagnostic_answer_schema,
    diagnostic_type_for_candidate,
)
from .review_scheduler import PROBE_EXPIRY_INTERVAL, REVIEW_SCHEDULER_VERSION

logger = logging.getLogger(__name__)

PRIORITY_THRESHOLD = 0.45
FORGETTING_PRIORITY_THRESHOLD = 0.35
RECENT_CHAT_LIMIT = 6
ANSWER_SCHEMAS = {
    "short_explanation",
    "procedure_steps",
    "strategy_compare",
    "rationale_explain",
    "final_answer_with_reasoning",
    "error_analysis",
}
DIMENSION_DEFAULT_SCHEMA = {
    "facts": "short_explanation",
    "procedures": "procedure_steps",
    "strategies": "strategy_compare",
    "rationales": "rationale_explain",
}


def _template_question(concept_key: str, target_dimension: str) -> str:
    concept = concept_key.replace("_", " ")
    if target_dimension == "facts":
        return f"In 1-2 sentences, define the key idea behind {concept} and name one important term."
    if target_dimension == "procedures":
        return f"Show the first three steps you would use to solve a typical {concept} problem."
    if target_dimension == "strategies":
        return f"Compare two possible methods for a {concept} problem. When would you choose each one?"
    if target_dimension == "rationales":
        return f"Explain why the main method for {concept} works, not just what steps to follow."
    return f"Briefly explain what you understand about {concept}."


def _default_checks(concept_label: str, target_dimension: str) -> List[str]:
    if target_dimension == "facts":
        return [
            f"States the core meaning of {concept_label}.",
            "Uses one accurate term, feature, or example from the concept.",
        ]
    if target_dimension == "procedures":
        return [
            f"Lists ordered steps or actions for using {concept_label}.",
            "Includes enough detail that another learner could follow the process.",
        ]
    if target_dimension == "strategies":
        return [
            f"Chooses or compares a suitable method for {concept_label}.",
            "Explains when or why that method is appropriate.",
        ]
    if target_dimension == "rationales":
        return [
            f"Explains why {concept_label} works or matters.",
            "Connects the explanation to a principle, cause, mechanism, or evidence.",
        ]
    return [f"Gives a relevant explanation of {concept_label}."]


def _normalize_answer_schema(value: Any, target_dimension: str, diagnostic_type: str = "") -> str:
    schema = str(value or "").strip()
    if schema in ANSWER_SCHEMAS:
        return schema
    if diagnostic_type:
        schema = diagnostic_answer_schema(diagnostic_type, target_dimension)
        if schema in ANSWER_SCHEMAS:
            return schema
    return DIMENSION_DEFAULT_SCHEMA.get(target_dimension, "short_explanation")


def _default_expected_steps(concept_label: str, target_dimension: str, diagnostic_type: str) -> List[str]:
    if diagnostic_type == "error_diagnosis":
        return [
            f"Identify the likely error or weak claim about {concept_label}.",
            "Explain why that part is wrong, incomplete, or unsupported.",
            "Give a corrected version or next repair step.",
        ]
    if diagnostic_type == "transfer":
        return [
            f"Recognize how {concept_label} applies in a new example.",
            "Use the relevant fact, step, strategy, or rationale accurately.",
            "Explain the connection between the new case and the original concept.",
        ]
    if target_dimension == "procedures":
        return [
            f"Choose a valid first action for {concept_label}.",
            "Keep the steps in a logical order.",
            "Include enough detail for another learner to follow.",
        ]
    if target_dimension == "strategies":
        return [
            f"Name or choose a suitable strategy for {concept_label}.",
            "Compare it with an alternative or rejected approach.",
            "Explain when the chosen strategy fits.",
        ]
    if target_dimension == "rationales":
        return [
            f"State the reason or mechanism behind {concept_label}.",
            "Connect the reason to a principle, evidence, or cause.",
            "Avoid only listing steps without explaining why they work.",
        ]
    return [
        f"State the core idea of {concept_label}.",
        "Use at least one accurate term, feature, or example.",
    ]


def _default_misconception_map(concept_label: str, target_dimension: str, diagnostic_type: str) -> List[Dict[str, str]]:
    if diagnostic_type == "error_diagnosis":
        return [{
            "tag": "unclear_error_source",
            "evidence_pattern": "Names a correction without explaining the original error.",
            "repair_hint": "Ask the learner to point to the exact incorrect step or claim before fixing it.",
        }]
    if target_dimension == "procedures":
        return [{
            "tag": "unordered_steps",
            "evidence_pattern": "Steps are listed but sequence or dependency is unclear.",
            "repair_hint": "Ask for the first action, next action, and stopping condition in order.",
        }]
    if target_dimension == "strategies":
        return [{
            "tag": "method_without_condition",
            "evidence_pattern": "Names a method but does not say when it should be used.",
            "repair_hint": "Ask the learner to contrast two cases where different strategies fit.",
        }]
    if target_dimension == "rationales":
        return [{
            "tag": "steps_without_why",
            "evidence_pattern": "Explains what to do but not why it works.",
            "repair_hint": "Ask for the cause, principle, or evidence that makes the step valid.",
        }]
    return [{
        "tag": "vague_definition",
        "evidence_pattern": "Uses broad wording without a precise feature, term, or example.",
        "repair_hint": "Ask for one concrete feature or example that distinguishes the concept.",
    }]


def _clean_string_list(value: Any) -> List[str]:
    raw_items = value if isinstance(value, list) else []
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _clean_misconception_map(value: Any) -> List[Dict[str, str]]:
    raw_items = value if isinstance(value, list) else []
    cleaned: List[Dict[str, str]] = []
    for item in raw_items:
        if isinstance(item, dict):
            tag = str(item.get("tag") or "").strip()
            evidence_pattern = str(item.get("evidence_pattern") or "").strip()
            repair_hint = str(item.get("repair_hint") or "").strip()
        else:
            tag = str(item or "").strip()
            evidence_pattern = ""
            repair_hint = ""
        if not tag and not evidence_pattern and not repair_hint:
            continue
        cleaned.append({
            "tag": tag or "possible_gap",
            "evidence_pattern": evidence_pattern,
            "repair_hint": repair_hint,
        })
    return cleaned


def _clone_candidate_with_dimension(candidate: Dict[str, Any], target_dimension: str) -> Dict[str, Any]:
    cloned = dict(candidate)
    cloned["target_dimension"] = target_dimension
    mastery_vector = candidate.get("mastery_vector") if isinstance(candidate.get("mastery_vector"), dict) else {}
    components = dict(candidate.get("priority_components") or {})
    components["mastery_gap"] = clamp01(1.0 - clamp01(mastery_vector.get(target_dimension), 0.45))
    cloned["priority_components"] = components
    cloned["priority_score"] = _candidate_priority_score(components)
    return cloned


def _state_dimension_scores(state: LearnerMasteryState | None) -> Dict[str, float]:
    if not state:
        return {dimension: 0.45 for dimension in DIMENSIONS}
    return {
        "facts": clamp01(state.facts_mastery),
        "procedures": clamp01(state.procedures_mastery),
        "strategies": clamp01(state.strategies_mastery),
        "rationales": clamp01(state.rationales_mastery),
    }


def _weakest_dimension_from_state(state: LearnerMasteryState | None) -> str:
    scores = _state_dimension_scores(state)
    if state and state.weakest_dimension in DIMENSIONS:
        return state.weakest_dimension
    return min(DIMENSIONS, key=lambda dimension: (scores[dimension], list(DIMENSIONS).index(dimension)))


def _dimension_hint_from_text(text: str) -> str:
    lowered = (text or "").lower()
    if re.search(r"\bwhy\b|\bbecause\b|\bjustify\b|\breason\b|\bworks\b", lowered):
        return "rationales"
    if re.search(r"\bstep\b|\bfirst\b|\bnext\b|\bhow\b|\bsolve\b|\bcalculate\b", lowered):
        return "procedures"
    if re.search(r"\bwhen\b|\bchoose\b|\bcompare\b|\bmethod\b|\bstrategy\b|\bapproach\b", lowered):
        return "strategies"
    return "facts"


def _recent_signals(user: UserProfile, goal: LearningGoal) -> List[ChatConceptSignal]:
    signals = list(
        ChatConceptSignal.objects.filter(user=user, learning_goal=goal)
        .select_related("chat_history")
        .order_by("-created_at", "-id")[:RECENT_CHAT_LIMIT]
    )
    verified_keys = set(
        ConceptRegistryEntry.objects.filter(
            user=user,
            learning_goal=goal,
            status=ConceptRegistryEntry.STATUS_VERIFIED,
            concept_key__in=[signal.concept_key for signal in signals],
        ).values_list("concept_key", flat=True)
    )
    return [signal for signal in signals if signal.concept_key in verified_keys]


def _recent_signal_relevance(signals: Iterable[ChatConceptSignal]) -> Dict[str, float]:
    weights = [1.0, 0.85, 0.70, 0.55, 0.40, 0.25]
    relevance: Dict[str, float] = {}
    total = 0.0
    for idx, signal in enumerate(signals):
        if idx >= len(weights):
            break
        weight = weights[idx]
        total += weight
        relevance[signal.concept_key] = relevance.get(signal.concept_key, 0.0) + weight
    if not total:
        return {}
    return {key: round(value / total, 4) for key, value in relevance.items()}


def _last_probe_penalties(
    *,
    recent_probes: List[AdaptiveProbe],
    concept_key: str,
    target_dimension: str,
) -> tuple[float, float]:
    if not recent_probes:
        return 0.0, 0.0
    same_concept = [probe for probe in recent_probes[:2] if probe.concept_key == concept_key]
    if recent_probes[0].concept_key == concept_key:
        recent_probe_penalty = 1.0 if recent_probes[0].target_dimension == target_dimension else 0.35
    else:
        recent_probe_penalty = 0.6 if same_concept else 0.0
    same_dimension_penalty = 0.0
    if recent_probes[0].concept_key == concept_key and recent_probes[0].target_dimension == target_dimension:
        same_dimension_penalty = 1.0
    elif any(probe.target_dimension == target_dimension for probe in recent_probes[:2]):
        same_dimension_penalty = 0.5
    return recent_probe_penalty, same_dimension_penalty


def _avoid_third_repeat_dimension(
    *,
    recent_probes: List[AdaptiveProbe],
    concept_key: str,
    proposed_dimension: str,
    state: LearnerMasteryState | None,
) -> str:
    last_two = recent_probes[:2]
    if len(last_two) < 2:
        return proposed_dimension
    if not all(probe.concept_key == concept_key and probe.target_dimension == proposed_dimension for probe in last_two):
        return proposed_dimension
    scores = _state_dimension_scores(state)
    alternatives = [dimension for dimension in DIMENSIONS if dimension != proposed_dimension]
    return min(alternatives, key=lambda dimension: (scores[dimension], list(DIMENSIONS).index(dimension)))


def _last_activity_for_concept(user: UserProfile, goal: LearningGoal, concept_key: str):
    last_event = (
        AdaptiveInteractionEvent.objects.filter(user=user, learning_goal=goal, concept_key=concept_key)
        .order_by("-created_at", "-id")
        .first()
    )
    last_probe = (
        AdaptiveProbe.objects.filter(user=user, learning_goal=goal, concept_key=concept_key)
        .order_by("-created_at", "-id")
        .first()
    )
    timestamps = [item for item in [getattr(last_event, "created_at", None), getattr(last_probe, "created_at", None)] if item]
    return max(timestamps) if timestamps else None


def _staleness(user: UserProfile, goal: LearningGoal, concept_key: str) -> float:
    last_activity = _last_activity_for_concept(user, goal, concept_key)
    if not last_activity:
        return 1.0
    age_days = max(0.0, (timezone.now() - last_activity).total_seconds() / 86400.0)
    return clamp01(age_days / 7.0)


def _forgetting_risk(state: LearnerMasteryState | None) -> float:
    if not state or not state.updated_at:
        return 0.0
    age_days = max(0.0, (timezone.now() - state.updated_at).total_seconds() / 86400.0)
    return clamp01(age_days / 3.0)


def _uncertainty(state: LearnerMasteryState | None) -> float:
    if not state:
        return 1.0
    if state.event_count <= 0:
        base = 1.0
    elif state.event_count == 1:
        base = 0.75
    elif state.event_count == 2:
        base = 0.60
    elif state.event_count <= 4:
        base = 0.40
    else:
        base = 0.20
    if state.curve_pattern == "fluctuating":
        base += 0.20
    return clamp01(base)


def _curve_need(state: LearnerMasteryState | None) -> float:
    if not state or clamp01(state.curve_confidence) < CURVE_CONFIDENCE_THRESHOLD:
        return 0.0
    if state.curve_pattern == "declining":
        return 0.20
    if state.curve_pattern == "plateau":
        return 0.15
    if state.curve_pattern == "fluctuating":
        return 0.12
    if state.curve_pattern == "fast_growth":
        return 0.05
    return 0.0


def _prerequisite_importance(
    *,
    concept_key: str,
    baseline_key: str,
    signal: ChatConceptSignal | None,
    state: LearnerMasteryState | None,
) -> float:
    if signal and signal.source == ChatConceptSignal.SOURCE_CONCEPT_MAP:
        return 0.70
    if concept_key == baseline_key:
        return 0.60
    if state:
        return 0.50
    return 0.40


def _candidate_priority_score(components: Dict[str, float]) -> float:
    return round(
        0.30 * components["mastery_gap"]
        + 0.20 * components["uncertainty"]
        + 0.20 * components["recent_relevance"]
        + 0.15 * components["staleness"]
        + 0.10 * components["prerequisite_importance"]
        + 0.05 * components["forgetting_risk"]
        + components.get("curve_need", 0.0)
        - 0.25 * components["recent_probe_penalty"]
        - 0.15 * components["same_dimension_penalty"],
        4,
    )


def _build_probe_candidates(
    *,
    user: UserProfile,
    goal: LearningGoal,
    recent_probes: List[AdaptiveProbe],
    allowed_concept_keys: set[str] | None = None,
) -> List[Dict[str, Any]]:
    signals = _recent_signals(user, goal)
    relevance_by_concept = _recent_signal_relevance(signals)
    latest_signal_by_concept: Dict[str, ChatConceptSignal] = {}
    for signal in signals:
        latest_signal_by_concept.setdefault(signal.concept_key, signal)

    states_by_concept = {
        state.concept_key: state
        for state in LearnerMasteryState.objects.filter(user=user, learning_goal=goal)
    }
    baseline_key = default_concept_key_for_goal(goal)
    concept_keys = set(states_by_concept) | set(latest_signal_by_concept) | {baseline_key}
    if allowed_concept_keys is not None:
        concept_keys &= {str(key) for key in allowed_concept_keys}
    candidates: List[Dict[str, Any]] = []
    for concept_key in sorted(concept_keys):
        state = states_by_concept.get(concept_key)
        signal = latest_signal_by_concept.get(concept_key)
        if state:
            target_dimension = _weakest_dimension_from_state(state)
        elif signal:
            target_dimension = _dimension_hint_from_text(signal.evidence_snippet)
        else:
            target_dimension = "facts"
        target_dimension = _avoid_third_repeat_dimension(
            recent_probes=recent_probes,
            concept_key=concept_key,
            proposed_dimension=target_dimension,
            state=state,
        )
        mastery_scores = _state_dimension_scores(state)
        mastery_gap = clamp01(1.0 - mastery_scores[target_dimension]) if state else 0.55
        recent_probe_penalty, same_dimension_penalty = _last_probe_penalties(
            recent_probes=recent_probes,
            concept_key=concept_key,
            target_dimension=target_dimension,
        )
        components = {
            "mastery_gap": mastery_gap,
            "uncertainty": _uncertainty(state),
            "recent_relevance": relevance_by_concept.get(concept_key, 0.0),
            "staleness": _staleness(user, goal, concept_key),
            "prerequisite_importance": _prerequisite_importance(
                concept_key=concept_key,
                baseline_key=baseline_key,
                signal=signal,
                state=state,
            ),
            "forgetting_risk": _forgetting_risk(state),
            "curve_need": _curve_need(state),
            "recent_probe_penalty": recent_probe_penalty,
            "same_dimension_penalty": same_dimension_penalty,
        }
        priority_score = _candidate_priority_score(components)
        label = signal.concept_label if signal and signal.concept_label else concept_key.replace("_", " ").title()
        candidates.append({
            "concept_key": concept_key,
            "concept_label": label,
            "target_dimension": target_dimension,
            "state": state,
            "signal": signal,
            "priority_components": components,
            "priority_score": priority_score,
            "mastery_vector": mastery_scores,
            "quality_score": clamp01(state.quality_score) if state else None,
        })
    candidates.sort(key=lambda item: (-item["priority_score"], -item["priority_components"]["recent_relevance"], item["concept_key"]))
    return candidates


def _recent_chat_context(user: UserProfile, goal: LearningGoal, concept_key: str) -> List[Dict[str, str]]:
    matching_history_ids = list(
        ChatConceptSignal.objects.filter(user=user, learning_goal=goal, concept_key=concept_key)
        .exclude(chat_history=None)
        .order_by("-created_at", "-id")
        .values_list("chat_history_id", flat=True)[:3]
    )
    histories = list(
        visible_history_queryset().filter(id__in=matching_history_ids, user=user, learning_goal=goal)
        .order_by("-timestamp", "-id")
    )
    if len(histories) < 3:
        existing_ids = {history.id for history in histories}
        histories.extend(
            visible_history_queryset().filter(user=user, learning_goal=goal)
            .exclude(id__in=existing_ids)
            .order_by("-timestamp", "-id")[: 3 - len(histories)]
        )
    return [
        {
            "question": truncate_for_prompt(history.question or "", 260),
            "answer": truncate_for_prompt(history.answer or "", 320),
        }
        for history in histories[:3]
    ]


def _last_probe_feedback(user: UserProfile, goal: LearningGoal, concept_key: str) -> Dict[str, Any]:
    event = (
        AdaptiveInteractionEvent.objects.filter(
            user=user,
            learning_goal=goal,
            concept_key=concept_key,
            source=AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE,
        )
        .order_by("-created_at", "-id")
        .first()
    )
    if not event:
        return {}
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    return {
        "evidence": truncate_for_prompt(event.evidence or "", 300),
        "missing_checks": metadata.get("missing_checks") or metadata.get("probe_grading", {}).get("missing_checks") or [],
        "accuracy_score": event.accuracy_score,
    }


def _difficulty_for_candidate(candidate: Dict[str, Any]) -> str:
    state = candidate.get("state")
    if state and clamp01(getattr(state, "curve_confidence", 0.0)) >= CURVE_CONFIDENCE_THRESHOLD:
        if state.curve_pattern == "declining":
            return "repair"
        if state.curve_pattern in {"plateau", "fluctuating"}:
            return "transfer"
        if state.curve_pattern == "fast_growth" and clamp01(getattr(state, "quality_score", 0.0)) >= 0.70:
            return "challenge"
    quality = candidate.get("quality_score")
    if quality is None:
        return "repair"
    if quality < 0.45:
        return "repair"
    if quality < 0.75:
        return "transfer"
    return "challenge"


def _candidate_pair(candidate: Dict[str, Any]) -> tuple[str, str]:
    return (
        str(candidate.get("concept_key") or ""),
        str(candidate.get("target_dimension") or "facts"),
    )


def _alternate_dimension_candidates(candidate: Dict[str, Any]) -> List[Dict[str, Any]]:
    current_dimension = str(candidate.get("target_dimension") or "facts")
    mastery_vector = candidate.get("mastery_vector") if isinstance(candidate.get("mastery_vector"), dict) else {}
    alternatives = [dimension for dimension in DIMENSIONS if dimension != current_dimension]
    alternatives.sort(key=lambda dimension: (clamp01(mastery_vector.get(dimension), 0.45), list(DIMENSIONS).index(dimension)))
    return [_clone_candidate_with_dimension(candidate, dimension) for dimension in alternatives]


def _select_micro_probe_candidates(
    candidates: List[Dict[str, Any]],
    *,
    due_by_forgetting: bool,
) -> List[Dict[str, Any]]:
    if not candidates:
        return []

    target_count = MAX_MICRO_ITEM_COUNT if due_by_forgetting else DEFAULT_MICRO_ITEM_COUNT
    selected: List[Dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    def add(candidate: Dict[str, Any] | None) -> None:
        if not candidate or len(selected) >= target_count:
            return
        pair = _candidate_pair(candidate)
        if not pair[0] or pair in seen_pairs:
            return
        selected.append(candidate)
        seen_pairs.add(pair)

    add(candidates[0])

    stale_candidates = sorted(
        candidates[1:],
        key=lambda item: (
            -float((item.get("priority_components") or {}).get("forgetting_risk") or 0.0),
            -float((item.get("priority_components") or {}).get("staleness") or 0.0),
            -float(item.get("priority_score") or 0.0),
        ),
    )
    for candidate in stale_candidates:
        add(candidate)
        if len(selected) >= min(target_count, DEFAULT_MICRO_ITEM_COUNT):
            break

    for candidate in candidates:
        add(candidate)

    if len(selected) < target_count:
        for base in list(selected) or candidates[:1]:
            for alternate in _alternate_dimension_candidates(base):
                add(alternate)
                if len(selected) >= target_count:
                    break
            if len(selected) >= target_count:
                break

    return selected


def _generate_probe_question(
    goal: LearningGoal,
    candidate: Dict[str, Any],
    *,
    user: UserProfile | None = None,
    allow_remote_generation: bool = True,
) -> tuple[str, Dict[str, Any]]:
    concept_key = str(candidate.get("concept_key") or default_concept_key_for_goal(goal))
    target_dimension = str(candidate.get("target_dimension") or "facts")
    fallback = _template_question(concept_key, target_dimension)
    difficulty = _difficulty_for_candidate(candidate)
    diagnostic_type = diagnostic_type_for_candidate(candidate, role=str(candidate.get("selection_role") or ""))
    answer_schema = _normalize_answer_schema(candidate.get("answer_schema"), target_dimension, diagnostic_type)
    concept_label = candidate.get("concept_label") or concept_key.replace("_", " ").title()
    recent_context = _recent_chat_context(user, goal, concept_key) if user else []
    last_feedback = _last_probe_feedback(user, goal, concept_key) if user else {}
    rubric: Dict[str, Any] = {
        "concept_key": concept_key,
        "concept_label": concept_label,
        "target_dimension": target_dimension,
        "diagnostic_type": diagnostic_type,
        "answer_schema": answer_schema,
        "expected_steps": _default_expected_steps(concept_label, target_dimension, diagnostic_type),
        "checks": _default_checks(concept_label, target_dimension),
        "misconception_map": _default_misconception_map(concept_label, target_dimension, diagnostic_type),
        "feedback_focus": f"Pinpoint the learner's next repair step for {concept_label}.",
        "dimension_policy": DIMENSION_POLICY.get(target_dimension, ""),
        "difficulty": difficulty,
        "mastery_vector": candidate.get("mastery_vector") or {},
        "priority_components": candidate.get("priority_components") or {},
        "priority_score": candidate.get("priority_score"),
        "curve_pattern": getattr(candidate.get("state"), "curve_pattern", "insufficient_data") if candidate.get("state") else "insufficient_data",
        "curve_confidence": clamp01(getattr(candidate.get("state"), "curve_confidence", 0.0)) if candidate.get("state") else 0.0,
        "curve_reason": getattr(candidate.get("state"), "curve_reason", {}) if candidate.get("state") else {},
        "recent_chat_context": recent_context,
        "last_probe_feedback": last_feedback,
        "generated_by": "template",
    }
    if not allow_remote_generation:
        rubric["generation_reason"] = (
            "Deterministic local template for bounded agent probe request."
        )
        return fallback, rubric
    prompt = f"""
Create one short diagnostic probe item for an adaptive tutor. The learner's subject may be math, programming, science, humanities, writing, language learning, or another academic field. Do not assume mathematics unless the learning goal requires it.
Return STRICT JSON only:
{{
  "question_text": "...",
  "rubric": {{
    "concept_key": "...",
    "target_dimension": "...",
    "diagnostic_type": "recall|procedure_trace|strategy_choice|why_explain|transfer|error_diagnosis",
    "answer_schema": "short_explanation|procedure_steps|strategy_compare|rationale_explain|final_answer_with_reasoning|error_analysis",
    "ideal_answer": "...",
    "expected_steps": ["observable reasoning step or answer component"],
    "checks": ["..."],
    "common_misconceptions": ["..."],
    "misconception_map": [
      {{"tag": "short_tag", "evidence_pattern": "what in the student's answer would reveal it", "repair_hint": "one next coaching move"}}
    ],
    "feedback_focus": "one actionable coaching target",
    "scoring_guidance": {{
      "facts": "...",
      "procedures": "...",
      "strategies": "...",
      "rationales": "..."
    }}
  }},
  "difficulty": "repair|transfer|challenge",
  "generation_reason": "..."
}}

Learning goal: {truncate_for_prompt(goal.preference_text or goal.title or "", 500)}
Selected concept: {rubric["concept_label"]} ({concept_key})
Target dimension: {DIMENSION_LABELS.get(target_dimension, target_dimension)}
Diagnostic type: {diagnostic_type}
Default answer schema: {answer_schema}
Difficulty: {difficulty}
Current mastery vector: {json.dumps(candidate.get("mastery_vector") or {}, ensure_ascii=True)}
Curve pattern: {rubric["curve_pattern"]} (confidence {rubric["curve_confidence"]:.2f})
Curve reason: {json.dumps(rubric["curve_reason"], ensure_ascii=True)[:1200]}
Recent related chat turns: {json.dumps(recent_context, ensure_ascii=True)}
Last probe feedback: {json.dumps(last_feedback, ensure_ascii=True)}
Question requirements:
- Ask exactly one question.
- It should be answerable in 30-60 seconds.
- It must test the target dimension directly.
- It must fit the diagnostic type and answer schema.
- Do not ask the student to merely repeat the last tutor answer.
- Prefer a small transfer/application task when the learner has already seen the concept.
- Use a task style that fits the learning goal's discipline.
- For error_analysis, include a concise flawed claim, sample step, sentence, explanation, or approach for the student to diagnose.
- If the curve is declining, test a prerequisite or foundation skill.
- If the curve is plateau, use a different representation or a transfer example.
- If the curve is fluctuating, make the prompt short and low-ambiguity.
- If the curve is fast_growth, use a slightly harder but related challenge.
"""
    try:
        response = llm_gateway.chat_completion_or_raise(
            route="adaptive.probe",
            model=settings.LEARNING_ADAPTIVE_PROBE_MODEL,
            messages=[{"role": "user", "content": prompt}],
            reasoning_effort="none",
            max_completion_tokens=550,
            response_format={"type": "json_object"},
        )
        payload = json.loads(response.choices[0].message.content or "{}")
        question = str(payload.get("question_text") or "").strip()
        if question:
            llm_rubric = payload.get("rubric") if isinstance(payload.get("rubric"), dict) else {}
            rubric.update(llm_rubric)
            rubric["difficulty"] = str(payload.get("difficulty") or difficulty)
            rubric["generation_reason"] = str(payload.get("generation_reason") or "")
            rubric["generated_by"] = "llm"
            rubric["diagnostic_type"] = diagnostic_type_for_candidate(rubric, role=str(candidate.get("selection_role") or ""))
            rubric["answer_schema"] = _normalize_answer_schema(
                rubric.get("answer_schema"),
                target_dimension,
                str(rubric.get("diagnostic_type") or diagnostic_type),
            )
            if not isinstance(rubric.get("checks"), list) or not rubric["checks"]:
                rubric["checks"] = _default_checks(rubric["concept_label"], target_dimension)
            rubric["expected_steps"] = _clean_string_list(rubric.get("expected_steps")) or _default_expected_steps(
                rubric["concept_label"], target_dimension, str(rubric.get("diagnostic_type") or diagnostic_type)
            )
            rubric["misconception_map"] = _clean_misconception_map(rubric.get("misconception_map")) or _default_misconception_map(
                rubric["concept_label"], target_dimension, str(rubric.get("diagnostic_type") or diagnostic_type)
            )
            rubric["feedback_focus"] = str(rubric.get("feedback_focus") or "").strip() or f"Clarify the learner's next step for {rubric['concept_label']}."
            return question, rubric
    except Exception as exc:
        logger.info("Adaptive probe LLM generation fell back to template: %s", exc)
    rubric["generation_reason"] = "Local fallback template after probe LLM generation was unavailable."
    return fallback, rubric


def _normalize_probe_item(
    candidate: Dict[str, Any],
    *,
    question: str,
    rubric: Dict[str, Any],
    index: int,
) -> Dict[str, Any]:
    concept_key = normalize_concept_key(rubric.get("concept_key"), str(candidate.get("concept_key") or "general"))
    target_dimension = str(rubric.get("target_dimension") or candidate.get("target_dimension") or "facts")
    if target_dimension not in DIMENSIONS:
        target_dimension = "facts"
    concept_label = str(
        rubric.get("concept_label")
        or candidate.get("concept_label")
        or concept_key.replace("_", " ").title()
    ).strip()
    checks = rubric.get("checks") if isinstance(rubric.get("checks"), list) else []
    checks = [str(check).strip() for check in checks if str(check).strip()]
    if not checks:
        checks = _default_checks(concept_label, target_dimension)
    diagnostic_type = str(rubric.get("diagnostic_type") or candidate.get("diagnostic_type") or "").strip()
    if diagnostic_type not in {
        "recall",
        "procedure_trace",
        "strategy_choice",
        "why_explain",
        "transfer",
        "error_diagnosis",
    }:
        diagnostic_type = diagnostic_type_for_candidate(candidate, role=str(candidate.get("selection_role") or ""))
    expected_steps = _clean_string_list(rubric.get("expected_steps")) or _default_expected_steps(
        concept_label, target_dimension, diagnostic_type
    )
    misconception_map = _clean_misconception_map(rubric.get("misconception_map")) or _default_misconception_map(
        concept_label, target_dimension, diagnostic_type
    )
    return {
        "item_id": str(rubric.get("item_id") or f"p{index}"),
        "concept_key": concept_key,
        "concept_label": concept_label,
        "target_dimension": target_dimension,
        "diagnostic_type": diagnostic_type,
        "question_text": str(question or rubric.get("question_text") or _template_question(concept_key, target_dimension)).strip(),
        "answer_schema": _normalize_answer_schema(rubric.get("answer_schema"), target_dimension, diagnostic_type),
        "expected_steps": expected_steps,
        "checks": checks,
        "ideal_answer": str(rubric.get("ideal_answer") or "").strip(),
        "common_misconceptions": [
            str(item).strip()
            for item in (rubric.get("common_misconceptions") if isinstance(rubric.get("common_misconceptions"), list) else [])
            if str(item).strip()
        ],
        "misconception_map": misconception_map,
        "feedback_focus": str(rubric.get("feedback_focus") or "").strip() or f"Clarify the learner's next step for {concept_label}.",
        "difficulty": str(rubric.get("difficulty") or _difficulty_for_candidate(candidate)),
        "generation_reason": str(rubric.get("generation_reason") or "").strip(),
        "generated_by": str(rubric.get("generated_by") or "template"),
    }


def _resolve_probe_item_identity(user: UserProfile | None, goal: LearningGoal, item: Dict[str, Any]) -> Dict[str, Any]:
    original_key = str(item.get("concept_key") or "")
    canonical_original = canonicalize_concept_key(original_key, domain=goal.domain or "", branch=goal.branch or "") if original_key else ""
    if not user:
        item["concept_key"] = canonical_original
        return item
    resolution = None
    if original_key:
        resolution = resolve_concept_identity(
            user,
            goal,
            canonical_original,
            source_context="probe_item",
            allow_llm=False,
        )
    if resolution is None:
        resolution = resolve_concept_identity(
            user,
            goal,
            " ".join(
                part
                for part in [
                    original_key,
                    str(item.get("concept_label") or ""),
                    str(item.get("question_text") or ""),
                ]
                if str(part or "").strip()
            ),
            source_context="probe_item",
            allow_llm=False,
        )
    item["concept_key"] = resolution.concept_key
    item["concept_label"] = item.get("concept_label") or resolution.concept_label
    item["identity"] = {
        **resolution_metadata(resolution, original_concept_key=original_key),
        "resolved_concept_key": item["concept_key"],
    }
    return item


def _build_micro_probe(
    goal: LearningGoal,
    selected_candidates: List[Dict[str, Any]],
    *,
    user: UserProfile | None = None,
    probe_plan: Dict[str, Any] | None = None,
    risk_level: str = "normal",
    allow_remote_generation: bool = True,
    local_verified_only: bool = False,
) -> tuple[str, Dict[str, Any]]:
    probe_items: List[Dict[str, Any]] = []
    difficulties: List[str] = []
    for index, candidate in enumerate(selected_candidates, start=1):
        question, item_rubric = _generate_probe_question(
            goal,
            candidate,
            user=user,
            allow_remote_generation=allow_remote_generation,
        )
        item = _normalize_probe_item(candidate, question=question, rubric=item_rubric, index=index)
        if local_verified_only:
            if not user:
                raise ValueError("Local verified probe identity requires a scoped user.")
            verified_entry = ConceptRegistryEntry.objects.filter(
                user=user,
                learning_goal=goal,
                # Validate the exact server-selected candidate key before any
                # legacy display-key normalization performed above.
                concept_key=str(candidate.get("concept_key") or ""),
                status=ConceptRegistryEntry.STATUS_VERIFIED,
            ).first()
            if not verified_entry:
                raise ValueError("Probe candidate is not an exact verified local concept.")
            item["concept_key"] = verified_entry.concept_key
            item["concept_label"] = verified_entry.concept_label or item.get("concept_label")
            # Question construction does not admit student evidence.  This
            # local metadata records the verified source while explicitly
            # withholding mastery-write eligibility.
            item["identity"] = {
                "identity_version": "p3.2-local-verified-probe-v1",
                "identity_resolver_version": "local_verified_registry",
                "identity_decision_id": "",
                "identity_source": "verified_registry_exact",
                "identity_confidence": 1.0,
                "identity_relation": "same",
                "identity_decision_status": "accepted",
                "identity_admission_status": "blocked",
                "identity_admission_reason": "probe_question_not_student_evidence",
                "identity_decision_scores": {},
                "identity_candidate_trace": [],
                "original_concept_key": str(item.get("concept_key") or ""),
                "resolved_concept_key": verified_entry.concept_key,
                "related_concepts": [],
                "prerequisite_concepts": [],
                "identity_lifecycle_status": verified_entry.status,
                "identity_mastery_eligible": False,
                "identity_taxonomy_version": verified_entry.taxonomy_version,
                "identity_taxonomy_fingerprint": "",
            }
        else:
            item = _resolve_probe_item_identity(user, goal, item)
        probe_items.append(item)
        difficulties.append(item.get("difficulty") or "repair")

    if not probe_items:
        raise ValueError("Cannot build a micro probe without probe items.")

    question_text = (
        probe_items[0]["question_text"]
        if len(probe_items) == 1
        else "\n".join(
            f"{idx}. {item['question_text']}"
            for idx, item in enumerate(probe_items, start=1)
        )
    )
    primary = selected_candidates[0]
    rubric = {
        "probe_version": MICRO_PROBE_VERSION,
        "probe_items": probe_items,
        "difficulty": difficulties[0] if difficulties else "repair",
        "selected_candidates": [
            {
                "concept_key": item.get("concept_key"),
                "concept_label": item.get("concept_label"),
                "target_dimension": item.get("target_dimension"),
                "diagnostic_type": item.get("diagnostic_type"),
                "priority_score": candidate.get("priority_score"),
                "priority_components": candidate.get("priority_components") or {},
            }
            for item, candidate in zip(probe_items, selected_candidates)
        ],
        "selected_priority_components": primary.get("priority_components") or {},
        "selected_priority_score": primary.get("priority_score"),
        "probe_plan": probe_plan or {},
        "risk_level": risk_level,
        "generated_by": "micro_probe",
    }
    return question_text, rubric


def _coerce_probe_answers(answer: Any) -> List[Dict[str, Any]]:
    raw_answers = answer.get("answers") if isinstance(answer, dict) else answer
    if not isinstance(raw_answers, list):
        return []

    normalized: List[Dict[str, Any]] = []
    for raw in raw_answers:
        if not isinstance(raw, dict):
            continue
        item_id = str(raw.get("item_id") or "").strip()
        answer_text = str(raw.get("answer") or "").strip()
        final_answer = str(raw.get("final_answer") or "").strip()
        method_choice = str(raw.get("method_choice") or "").strip()
        reason = str(raw.get("reason") or "").strip()
        identified_error = str(raw.get("identified_error") or "").strip()
        correction = str(raw.get("correction") or "").strip()
        explanation = str(raw.get("explanation") or "").strip()
        steps_raw = raw.get("reasoning_steps") or raw.get("steps") or []
        reasoning_steps = [
            str(step).strip()
            for step in steps_raw
            if str(step).strip()
        ] if isinstance(steps_raw, list) else []
        has_content = any([
            answer_text,
            final_answer,
            method_choice,
            reason,
            identified_error,
            correction,
            explanation,
            reasoning_steps,
        ])
        if not item_id or not has_content:
            continue
        normalized.append({
            "item_id": item_id,
            "answer": answer_text,
            "final_answer": final_answer,
            "method_choice": method_choice,
            "reason": reason,
            "identified_error": identified_error,
            "correction": correction,
            "explanation": explanation,
            "reasoning_steps": reasoning_steps,
        })
    return normalized


def _answer_item_to_text(answer: Dict[str, Any]) -> str:
    parts: List[str] = []
    if answer.get("answer"):
        parts.append(str(answer["answer"]))
    if answer.get("final_answer"):
        parts.append(f"Final answer: {answer['final_answer']}")
    if answer.get("method_choice"):
        parts.append(f"Method or choice: {answer['method_choice']}")
    if answer.get("reason"):
        parts.append(f"Reason: {answer['reason']}")
    if answer.get("identified_error"):
        parts.append(f"Identified error: {answer['identified_error']}")
    if answer.get("correction"):
        parts.append(f"Correction: {answer['correction']}")
    if answer.get("explanation"):
        parts.append(f"Explanation: {answer['explanation']}")
    for idx, step in enumerate(answer.get("reasoning_steps") or [], start=1):
        parts.append(f"Step {idx}: {step}")
    return "\n".join(parts).strip()


def _probe_item_lookup(expected_rubric: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    items = expected_rubric.get("probe_items") if isinstance(expected_rubric.get("probe_items"), list) else []
    return {
        str(item.get("item_id")): item
        for item in items
        if isinstance(item, dict) and item.get("item_id")
    }


def _rubric_for_probe_item(parent_rubric: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "probe_version": parent_rubric.get("probe_version") or MICRO_PROBE_VERSION,
        "concept_key": item.get("concept_key"),
        "concept_label": item.get("concept_label"),
        "target_dimension": item.get("target_dimension"),
        "diagnostic_type": item.get("diagnostic_type"),
        "answer_schema": item.get("answer_schema"),
        "ideal_answer": item.get("ideal_answer", ""),
        "expected_steps": item.get("expected_steps") or [],
        "checks": item.get("checks") or [],
        "common_misconceptions": item.get("common_misconceptions") or [],
        "misconception_map": item.get("misconception_map") or [],
        "feedback_focus": item.get("feedback_focus") or "",
        "difficulty": item.get("difficulty") or parent_rubric.get("difficulty"),
    }


def generate_probe_from_offer(offer_id: str, *, allow_remote_generation: bool = True) -> AdaptiveProbe | None:
    """Generate exactly one formal Probe after an explicit Offer acceptance."""

    try:
        with transaction.atomic():
            offer = AdaptiveProbeOffer.objects.select_for_update().select_related("user", "learning_goal").filter(
                offer_id=str(offer_id or ""),
            ).first()
            if not offer:
                return None
            if offer.resulting_probe_id:
                return offer.resulting_probe
            if offer.status not in {AdaptiveProbeOffer.STATUS_ACCEPTED, AdaptiveProbeOffer.STATUS_GENERATING}:
                return None
            offer.status = AdaptiveProbeOffer.STATUS_GENERATING
            offer.last_error_code = ""
            offer.save(update_fields=["status", "last_error_code", "updated_at"])
            if AdaptiveProbe.objects.filter(
                user=offer.user,
                learning_goal=offer.learning_goal,
                status=AdaptiveProbe.STATUS_PENDING,
            ).exists():
                raise RuntimeError("formal_probe_already_pending")
            recent = list(
                AdaptiveProbe.objects.filter(user=offer.user, learning_goal=offer.learning_goal)
                .order_by("-created_at", "-id")[:3]
            )
            if offer.due_trigger == "initial_calibration":
                registry = ConceptRegistryEntry.objects.filter(
                    user=offer.user,
                    learning_goal=offer.learning_goal,
                    concept_key=offer.target_concept_key,
                    status=ConceptRegistryEntry.STATUS_VERIFIED,
                ).first()
                perceived = LearnerPerceivedState.objects.filter(
                    user=offer.user,
                    learning_goal=offer.learning_goal,
                    authority=LearnerPerceivedState.AUTHORITY_GUIDANCE_ONLY,
                    mastery_write_authorized=False,
                ).first()
                if not registry or not perceived:
                    raise RuntimeError("probe_offer_target_stale")
                perceived_scores = {
                    dimension: clamp01(
                        (perceived.dimension_scores or {}).get(dimension),
                        0.5,
                    )
                    for dimension in DIMENSIONS
                }
                candidate = {
                    "concept_key": registry.concept_key,
                    "concept_label": registry.concept_label or registry.concept_key,
                    "target_dimension": offer.target_dimension,
                    "state": None,
                    "signal": None,
                    "priority_components": offer.candidate_components_snapshot or {},
                    "priority_score": offer.candidate_score,
                    "mastery_vector": perceived_scores,
                    "quality_score": None,
                    "selection_role": "initial_calibration",
                }
            else:
                verified = set(
                    ConceptRegistryEntry.objects.filter(
                        user=offer.user,
                        learning_goal=offer.learning_goal,
                        status=ConceptRegistryEntry.STATUS_VERIFIED,
                    ).values_list("concept_key", flat=True)
                )
                admitted = set(
                    LearnerMasteryState.objects.filter(
                        user=offer.user,
                        learning_goal=offer.learning_goal,
                        concept_key__in=verified,
                        eligible_evidence_count__gt=0,
                    ).values_list("concept_key", flat=True)
                )
                candidates = _build_probe_candidates(
                    user=offer.user,
                    goal=offer.learning_goal,
                    recent_probes=recent,
                    allowed_concept_keys=admitted,
                )
                candidate = next(
                    (
                        item
                        for item in candidates
                        if item["concept_key"] == offer.target_concept_key
                        and item["target_dimension"] == offer.target_dimension
                    ),
                    None,
                )
            if candidate is None:
                raise RuntimeError("probe_offer_target_stale")
            if offer.due_trigger == "initial_calibration":
                plan_result = {
                    "selected_candidates": [candidate],
                    "probe_plan": {
                        "policy": "initial_calibration_single_item",
                        "requested_item_count": 1,
                        "selected_item_count": 1,
                    },
                    "risk_level": "normal",
                }
                selected = [candidate]
            else:
                plan_result = build_probe_plan(
                    [candidate],
                    due_by_forgetting=offer.due_trigger == "forgetting",
                    recent_probes=recent,
                )
                selected = plan_result.get("selected_candidates") or [candidate]
            question, rubric = _build_micro_probe(
                offer.learning_goal,
                selected,
                user=offer.user,
                probe_plan=plan_result.get("probe_plan") if isinstance(plan_result.get("probe_plan"), dict) else {},
                risk_level=str(plan_result.get("risk_level") or "normal"),
                allow_remote_generation=allow_remote_generation,
                local_verified_only=True,
            )
            rubric.update({
                "probe_offer_id": offer.offer_id,
                "trigger": offer.due_trigger,
                "due_reason": offer.due_reason,
                "scheduler_version": REVIEW_SCHEDULER_VERSION,
                "trigger_priority_score": offer.candidate_score,
                "trigger_priority_components": offer.candidate_components_snapshot,
            })
            primary_item = (rubric.get("probe_items") or [{}])[0]
            fixed_now = timezone.now()
            probe = AdaptiveProbe.objects.create(
                user=offer.user,
                learning_goal=offer.learning_goal,
                concept_key=primary_item.get("concept_key") or offer.target_concept_key,
                target_dimension=primary_item.get("target_dimension") or offer.target_dimension,
                question_text=question,
                expected_rubric=rubric,
                status=AdaptiveProbe.STATUS_PENDING,
                due_at=fixed_now,
                expires_at=fixed_now + PROBE_EXPIRY_INTERVAL,
                trace_id=normalize_trace_id(current_trace_context().trace_id),
            )
            offer.resulting_probe = probe
            offer.status = AdaptiveProbeOffer.STATUS_READY
            offer.open_scope_key = _open_offer_scope_key(offer.user_id, offer.learning_goal_id)
            offer.save(update_fields=["resulting_probe", "status", "open_scope_key", "updated_at"])
            return probe
    except Exception as exc:
        AdaptiveProbeOffer.objects.filter(offer_id=str(offer_id or ""), resulting_probe__isnull=True).update(
            status=AdaptiveProbeOffer.STATUS_ACCEPTED,
            last_error_code=exc.__class__.__name__[:64],
        )
        raise


def _open_offer_scope_key(user_id: int, goal_id: int) -> str:
    return f"{int(user_id)}:{int(goal_id)}"


def _consume_linked_probe_offer(probe: AdaptiveProbe) -> None:
    """Close the user-approved offer once its formal probe is resolved."""

    AdaptiveProbeOffer.objects.filter(
        resulting_probe=probe,
        status__in=AdaptiveProbeOffer.ACTIVE_STATUSES,
    ).update(
        status=AdaptiveProbeOffer.STATUS_CONSUMED,
        open_scope_key=None,
    )


@transaction.atomic
def submit_probe_answer(username: str, learning_goal_id: int, probe_id: int, answer: Any) -> Dict[str, Any]:
    user = UserProfile.objects.filter(username=username).first()
    probe = (
        AdaptiveProbe.objects.select_for_update().filter(
            id=int(probe_id),
            user=user,
            learning_goal_id=int(learning_goal_id),
            status=AdaptiveProbe.STATUS_PENDING,
        ).first()
        if user else None
    )
    if not probe:
        _emit_adaptive_trace(
            "probe_submit_failed "
            f"user={username} goal_id={learning_goal_id} probe_id={probe_id} reason=probe_not_found"
        )
        return {"ok": False, "error": "probe_not_found"}

    expected_rubric = probe.expected_rubric if isinstance(probe.expected_rubric, dict) else {}
    structured_answers = _coerce_probe_answers(answer)
    item_lookup = _probe_item_lookup(expected_rubric)
    unknown_item_ids = [
        answer_item["item_id"]
        for answer_item in structured_answers
        if not item_lookup.get(answer_item["item_id"])
    ]
    if unknown_item_ids:
        _emit_adaptive_trace(
            "probe_submit_failed "
            f"user={username} goal_id={learning_goal_id} probe_id={probe_id} "
            f"reason=unknown_probe_item item_ids={','.join(unknown_item_ids)}"
        )
        return {"ok": False, "error": "unknown_probe_item"}
    valid_structured_answers = [
        answer_item
        for answer_item in structured_answers
        if item_lookup.get(answer_item["item_id"]) and _answer_item_to_text(answer_item)
    ]
    if structured_answers and not valid_structured_answers:
        _emit_adaptive_trace(
            "probe_submit_failed "
            f"user={username} goal_id={learning_goal_id} probe_id={probe_id} reason=no_matching_probe_items"
        )
        return {"ok": False, "error": "no_matching_probe_items"}

    if structured_answers:
        probe.student_answer = json.dumps({"answers": valid_structured_answers}, ensure_ascii=True)
    else:
        legacy_answer = answer.get("answer") if isinstance(answer, dict) else answer
        probe.student_answer = str(legacy_answer or "").strip()

    if not probe.student_answer:
        _emit_adaptive_trace(
            "probe_submit_failed "
            f"user={username} goal_id={learning_goal_id} probe_id={probe_id} reason=missing_answer"
        )
        return {"ok": False, "error": "missing_answer"}

    probe.status = AdaptiveProbe.STATUS_ANSWERED
    probe.completed_at = timezone.now()
    probe.save(update_fields=["student_answer", "status", "completed_at", "updated_at"])
    _consume_linked_probe_offer(probe)
    _emit_adaptive_trace(
        "probe_answer_saved "
        f"user={username} goal_id={learning_goal_id} probe_id={probe.id} "
        f"concept={probe.concept_key} target_dimension={probe.target_dimension} "
        f"answer_length={len(probe.student_answer)}"
    )

    if structured_answers:
        queued_items = 0
        for answer_item in valid_structured_answers:
            probe_item = item_lookup.get(answer_item["item_id"])
            if not probe_item:
                continue
            student_answer = _answer_item_to_text(answer_item)
            if not student_answer:
                continue
            item_rubric = _rubric_for_probe_item(expected_rubric, probe_item)
            queue_probe_response_event(
                username=username,
                learning_goal_id=learning_goal_id,
                probe_id=probe.id,
                concept_key=str(probe_item.get("concept_key") or probe.concept_key),
                target_dimension=str(probe_item.get("target_dimension") or probe.target_dimension),
                question_text=str(probe_item.get("question_text") or probe.question_text),
                student_answer=student_answer,
                expected_rubric=item_rubric,
                probe_version=str(expected_rubric.get("probe_version") or MICRO_PROBE_VERSION),
                probe_item_id=answer_item["item_id"],
                answer_schema=str(probe_item.get("answer_schema") or ""),
            )
            queued_items += 1
        if queued_items <= 0:
            return {"ok": False, "error": "no_matching_probe_items"}
        return {"ok": True, "status": probe.status, "queued_items": queued_items}

    queue_probe_response_event(
        username=username,
        learning_goal_id=learning_goal_id,
        probe_id=probe.id,
        concept_key=probe.concept_key,
        target_dimension=probe.target_dimension,
        question_text=probe.question_text,
        student_answer=probe.student_answer,
        expected_rubric=expected_rubric,
    )
    return {"ok": True, "status": probe.status, "queued_items": 1}


@transaction.atomic
def skip_probe(username: str, learning_goal_id: int, probe_id: int) -> Dict[str, Any]:
    user = UserProfile.objects.filter(username=username).first()
    probe = (
        AdaptiveProbe.objects.select_for_update().filter(
            id=int(probe_id),
            user=user,
            learning_goal_id=int(learning_goal_id),
            status=AdaptiveProbe.STATUS_PENDING,
        ).first()
        if user else None
    )
    if not probe:
        _emit_adaptive_trace(
            "probe_skip_failed "
            f"user={username} goal_id={learning_goal_id} probe_id={probe_id} reason=probe_not_found"
        )
        return {"ok": False, "error": "probe_not_found"}
    probe.status = AdaptiveProbe.STATUS_SKIPPED
    probe.completed_at = timezone.now()
    probe.save(update_fields=["status", "completed_at", "updated_at"])
    _consume_linked_probe_offer(probe)
    _emit_adaptive_trace(
        "probe_skipped "
        f"user={username} goal_id={learning_goal_id} probe_id={probe.id} "
        f"concept={probe.concept_key} target_dimension={probe.target_dimension}"
    )
    return {"ok": True, "status": probe.status}
