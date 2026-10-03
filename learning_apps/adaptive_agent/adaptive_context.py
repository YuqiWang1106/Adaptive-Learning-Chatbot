from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Iterable

from django.utils import timezone

from learning_apps.adaptive_learning.constants import DIMENSION_POLICY, DIMENSIONS, TIER_POLICY
from learning_apps.adaptive_learning.mastery_evidence_policy import evidence_is_admitted
from learning_apps.adaptive_learning.review_scheduler import review_due_for_goal
from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    ConceptRegistryEntry,
    LearnerMasteryState,
    LearnerPerceivedState,
)

from learning_apps.application.contracts import CapabilityError, stable_sha256
from .models import LearningAgentRun


ADAPTIVE_CONTEXT_POLICY_VERSION = "adaptive-teaching-context-v2.0.0"
ADAPTIVE_CONTEXT_MAX_CHARS = 2500
CONFLICT_DELTA = 0.35


@dataclass(frozen=True)
class PreparedAdaptiveContext:
    envelope: dict[str, Any]
    input_item: dict[str, str]
    manifest: dict[str, Any]


def _clean(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _tokens(value: Any) -> tuple[str, ...]:
    return tuple(re.findall(r"[\w]+", str(value or "").casefold(), flags=re.UNICODE))


def _contains_phrase(question: tuple[str, ...], phrase: tuple[str, ...]) -> bool:
    if not question or not phrase or len(phrase) > len(question):
        return False
    width = len(phrase)
    return any(question[index : index + width] == phrase for index in range(len(question) - width + 1))


def _match_focus(question: str, registry: Iterable[ConceptRegistryEntry]) -> tuple[str, str, str]:
    question_tokens = _tokens(question)
    matches: dict[str, str] = {}
    for row in registry:
        phrases = [row.concept_key, row.concept_label]
        if isinstance(row.aliases, list):
            phrases.extend(row.aliases[:32])
        if any(_contains_phrase(question_tokens, _tokens(phrase)) for phrase in phrases if _tokens(phrase)):
            matches[row.concept_key] = row.concept_label
    if len(matches) == 1:
        key = next(iter(matches))
        return "matched", key, _clean(matches[key] or key.replace("_", " ").title(), 180)
    if len(matches) > 1:
        return "ambiguous", "", ""
    return "unavailable", "", ""


def _probe_id(event: AdaptiveInteractionEvent) -> str:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    return str(metadata.get("probe_id") or event.id)


def _target_dimension(event: AdaptiveInteractionEvent) -> str:
    metadata = event.metadata if isinstance(event.metadata, dict) else {}
    value = str(metadata.get("target_dimension") or "")
    return value if value in DIMENSIONS else ""


def _dimension_score(event: AdaptiveInteractionEvent, dimension: str) -> float | None:
    scores = event.dimension_scores if isinstance(event.dimension_scores, dict) else {}
    try:
        return float(scores[dimension])
    except (KeyError, TypeError, ValueError):
        return None


def _has_conflict(events: list[AdaptiveInteractionEvent], state: LearnerMasteryState | None) -> bool:
    by_dimension: dict[str, list[float]] = {dimension: [] for dimension in DIMENSIONS}
    for event in reversed(events):
        dimension = _target_dimension(event)
        score = _dimension_score(event, dimension) if dimension else None
        if dimension and score is not None and len(by_dimension[dimension]) < 2:
            by_dimension[dimension].append(score)
    if any(len(values) == 2 and abs(values[0] - values[1]) >= CONFLICT_DELTA for values in by_dimension.values()):
        return True
    return bool(
        state
        and state.curve_pattern == "fluctuating"
        and int(state.curve_evidence_count) >= 2
        and float(state.curve_confidence) >= 0.65
    )


def classify_observed_status(
    events: list[AdaptiveInteractionEvent],
    state: LearnerMasteryState | None,
) -> str:
    """Return a semantic reliability band; never a probability."""

    admitted = [event for event in events if evidence_is_admitted(event.metadata)]
    probe_count = len({_probe_id(event) for event in admitted})
    if probe_count == 0:
        return "none"
    if probe_count == 1:
        return "emerging"
    if _has_conflict(admitted, state):
        return "conflicted"
    return "supported"


def _perceived_dimension(state: LearnerPerceivedState | None) -> str:
    if not state or not isinstance(state.dimension_scores, dict):
        return "unknown"
    values: dict[str, float] = {}
    diagnostic_summary = getattr(state, "diagnostic_summary", {})
    for dimension in DIMENSIONS:
        summary = (
            diagnostic_summary.get(dimension, {})
            if isinstance(diagnostic_summary, dict)
            else {}
        )
        evidence_state = (
            str(summary.get("evidence_sufficiency") or "")
            if isinstance(summary, dict)
            else ""
        )
        if evidence_state in {"thin", "insufficient"}:
            continue
        try:
            values[dimension] = float(state.dimension_scores[dimension])
        except (KeyError, TypeError, ValueError):
            continue
    if not values:
        return "unknown"
    return min(values, key=lambda dimension: (values[dimension], DIMENSIONS.index(dimension)))


def _supported_dimension(
    state: LearnerMasteryState | None,
    events: list[AdaptiveInteractionEvent],
) -> str:
    if not state or state.weakest_dimension not in DIMENSIONS:
        return "unknown"
    return state.weakest_dimension if any(_target_dimension(event) == state.weakest_dimension for event in events) else "unknown"


def _teaching_brief(
    *,
    observed_status: str,
    perceived: LearnerPerceivedState | None,
    state: LearnerMasteryState | None,
    events: list[AdaptiveInteractionEvent],
    review_due: bool,
) -> tuple[dict[str, str], list[str], list[str]]:
    perceived_status = (
        "available"
        if perceived and _perceived_dimension(perceived) != "unknown"
        else "none"
    )
    target_dimension = "unknown"
    difficulty = "maintain"
    scaffolding = "medium"
    strategy = "Use a goal-aligned explanation and collect reliable evidence before personalizing."
    uncertainty_action = "State that reliable learner evidence is not available."
    level = "none"
    allowed = ["Use the learning goal, age and current question."]
    prohibited = ["Do not claim a confirmed weakness, misconception, mastery level or progress trend."]

    if observed_status == "none" and perceived_status == "available" and perceived:
        level = "soft"
        target_dimension = _perceived_dimension(perceived)
        strategy = DIMENSION_POLICY.get(target_dimension, "Use a moderately scaffolded explanation.")
        try:
            perceived_score = float(perceived.dimension_scores.get(target_dimension, 0.5))
        except (TypeError, ValueError):
            perceived_score = 0.5
        scaffolding = "high" if perceived_score < 0.45 else "medium"
        uncertainty_action = "Use self-reported uncertainty only to choose scaffolding; invite evidence, not conclusions."
        allowed = ["Say the self-assessment suggests a preferred starting point or possible support need."]
        prohibited = ["Do not describe self-assessment as observed mastery or a confirmed concept weakness."]
    elif observed_status == "emerging":
        level = "soft"
        latest = events[-1] if events else None
        target_dimension = _target_dimension(latest) if latest else "unknown"
        latest_score = _dimension_score(latest, target_dimension) if latest and target_dimension != "unknown" else None
        scaffolding = "high" if latest_score is not None and latest_score < 0.45 else "medium"
        strategy = DIMENSION_POLICY.get(target_dimension, "Use the one recent Probe cautiously.")
        uncertainty_action = "Attribute any adaptation to one recent Probe and avoid a long-term claim."
        allowed = ["Say one recent Probe suggests a tentative support need."]
        prohibited = ["Do not call one Probe a stable weakness, mastery level or trend."]
    elif observed_status == "supported" and state:
        level = "evidence_backed"
        target_dimension = _supported_dimension(state, events)
        score = float(state.quality_score)
        if score < 0.45:
            tier, difficulty, scaffolding = "SCAFFOLD", "reduce", "high"
        elif score < 0.75:
            tier, difficulty, scaffolding = "CONSOLIDATE", "maintain", "medium"
        else:
            tier, difficulty, scaffolding = "REINFORCE", "increase", "low"
        strategy = TIER_POLICY[tier]
        if target_dimension != "unknown":
            strategy = f"{strategy} {DIMENSION_POLICY[target_dimension]}"
        uncertainty_action = "Use evidence-backed language while keeping conclusions concept-scoped."
        allowed = ["Describe the observed concept pattern and adapt difficulty within this learning goal."]
        prohibited = ["Do not generalize this evidence into a permanent student trait or another concept."]
    elif observed_status == "conflicted":
        level = "soft"
        difficulty = "maintain"
        scaffolding = "medium"
        strategy = "Keep difficulty stable, contrast methods, and recommend a formal diagnostic before stronger adaptation."
        uncertainty_action = "State that recent formal evidence conflicts."
        allowed = ["Describe the evidence as mixed or inconsistent."]
        prohibited = ["Do not raise or lower difficulty aggressively or declare a stable weakness."]

    return (
        {
            "level": level,
            "difficulty": difficulty,
            "scaffolding": scaffolding,
            "target_dimension": target_dimension,
            "recommended_strategy": strategy,
            "uncertainty_action": uncertainty_action,
            "freshness": "review_due" if review_due else "current" if observed_status != "none" else "unknown",
            "perceived_status": perceived_status,
        },
        allowed,
        prohibited,
    )


def _evidence_summary(observed_status: str, perceived_status: str) -> str:
    observed = {
        "none": "No formal Probe evidence is available for this concept.",
        "emerging": "One formal Probe provides tentative observed evidence.",
        "supported": "Multiple formal Probes support a stable concept-scoped pattern.",
        "conflicted": "Multiple formal Probes contain a material same-dimension conflict.",
    }[observed_status]
    perceived = " Goal-level self-assessment guidance is available." if perceived_status == "available" else ""
    return observed + perceived


def concept_teaching_state(*, user_id: int, learning_goal_id: int, concept_key: str, now=None) -> dict[str, Any]:
    """Return semantic Agent evidence plus explicitly diagnostic raw metrics."""

    registry = ConceptRegistryEntry.objects.filter(
        user_id=user_id,
        learning_goal_id=learning_goal_id,
        concept_key=concept_key,
        status=ConceptRegistryEntry.STATUS_VERIFIED,
    ).first()
    if not registry:
        raise CapabilityError("verified_concept_required")
    state = LearnerMasteryState.objects.filter(
        user_id=user_id,
        learning_goal_id=learning_goal_id,
        concept_key=concept_key,
    ).first()
    events = list(
        AdaptiveInteractionEvent.objects.filter(
            user_id=user_id,
            learning_goal_id=learning_goal_id,
            concept_key=concept_key,
            source=AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE,
        ).order_by("created_at", "id")
    )
    events = [event for event in events if evidence_is_admitted(event.metadata)]
    observed_status = classify_observed_status(events, state)
    perceived = LearnerPerceivedState.objects.filter(
        user_id=user_id,
        learning_goal_id=learning_goal_id,
        authority=LearnerPerceivedState.AUTHORITY_GUIDANCE_ONLY,
        mastery_write_authorized=False,
    ).first()
    review_due = False
    if state:
        from learning_apps.persistence.models import LearningGoal, UserProfile

        user = UserProfile.objects.get(pk=user_id)
        goal = LearningGoal.objects.get(pk=learning_goal_id, user=user)
        review_due = review_due_for_goal(user=user, goal=goal, now=now or timezone.now()).due
    adaptation, allowed, prohibited = _teaching_brief(
        observed_status=observed_status,
        perceived=perceived,
        state=state,
        events=events,
        review_due=review_due,
    )
    perceived_status = adaptation.pop("perceived_status")
    freshness = adaptation.pop("freshness")
    diagnostics = {
        "semantics": "uncalibrated_diagnostics_not_probability",
        "formal_probe_count": len({_probe_id(event) for event in events}),
    }
    if state:
        diagnostics.update(
            {
                "mastery": round(float(state.dimension_mastery_score), 4),
                "quality": round(float(state.quality_score), 4),
                "heuristic_confidence": round(float(state.mastery_confidence), 4),
            }
        )
    return {
        "concept_key": registry.concept_key,
        "concept_label": _clean(registry.concept_label or registry.concept_key.replace("_", " ").title(), 180),
        "observed_status": observed_status,
        "perceived_status": perceived_status,
        "freshness": freshness,
        "evidence_summary": _evidence_summary(observed_status, perceived_status),
        "adaptation": adaptation,
        "allowed_claims": allowed,
        "prohibited_claims": prohibited,
        "diagnostic_metrics": diagnostics,
        "trust": "probe_only_observed_state",
    }


def assemble_adaptive_context(
    run: LearningAgentRun,
    question: str,
    *,
    now=None,
) -> PreparedAdaptiveContext:
    """Build a compact teaching decision without any remote model call."""

    fixed_now = now or timezone.now()
    registry = list(
        ConceptRegistryEntry.objects.filter(
            user_id=run.user_id,
            learning_goal_id=run.learning_goal_id,
            status=ConceptRegistryEntry.STATUS_VERIFIED,
        ).order_by("concept_key")
    )
    focus_status, focus_key, focus_label = _match_focus(question, registry)
    state = (
        LearnerMasteryState.objects.filter(
            user_id=run.user_id,
            learning_goal_id=run.learning_goal_id,
            concept_key=focus_key,
        ).first()
        if focus_key
        else None
    )
    events = list(
        AdaptiveInteractionEvent.objects.filter(
            user_id=run.user_id,
            learning_goal_id=run.learning_goal_id,
            concept_key=focus_key,
            source=AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE,
        ).order_by("created_at", "id")
    ) if focus_key else []
    events = [event for event in events if evidence_is_admitted(event.metadata)]
    observed_status = classify_observed_status(events, state)
    perceived = LearnerPerceivedState.objects.filter(
        user_id=run.user_id,
        learning_goal_id=run.learning_goal_id,
        authority=LearnerPerceivedState.AUTHORITY_GUIDANCE_ONLY,
        mastery_write_authorized=False,
    ).first()
    due = review_due_for_goal(user=run.user, goal=run.learning_goal, now=fixed_now)
    adaptation, allowed, prohibited = _teaching_brief(
        observed_status=observed_status,
        perceived=perceived,
        state=state,
        events=events,
        review_due=bool(due.due),
    )
    perceived_status = adaptation.pop("perceived_status")
    freshness = adaptation.pop("freshness")
    envelope = {
        "policy_version": ADAPTIVE_CONTEXT_POLICY_VERSION,
        "focus": {
            "status": focus_status,
            "concept_key": focus_key,
            "concept_label": focus_label,
        },
        "learner_evidence": {
            "observed_status": observed_status,
            "perceived_status": perceived_status,
            "freshness": freshness,
            "evidence_summary": _evidence_summary(observed_status, perceived_status),
        },
        "adaptation": adaptation,
        "allowed_claims": allowed,
        "prohibited_claims": prohibited,
    }
    serialized = json.dumps(envelope, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if len(serialized) > ADAPTIVE_CONTEXT_MAX_CHARS:
        raise CapabilityError("adaptive_context_size_limit_exceeded")
    decision_sha256 = stable_sha256(envelope)
    manifest = {
        "policy_version": ADAPTIVE_CONTEXT_POLICY_VERSION,
        "context_decision_sha256": decision_sha256,
        "focus_status": focus_status,
        "focus_concept_key": focus_key,
        "focus_concept_label": focus_label,
        "concept_keys": [focus_key] if focus_key else [],
        "observed_status": observed_status,
        "perceived_status": perceived_status,
        "adaptation_level": adaptation["level"],
        "difficulty": adaptation["difficulty"],
        "scaffolding": adaptation["scaffolding"],
        "target_dimension": adaptation["target_dimension"],
        "freshness": freshness,
        "formal_probe_count": len({_probe_id(event) for event in events}),
        "evidence_timestamp": events[-1].created_at.isoformat() if events else "",
        "serialized_chars": len(serialized),
        "remote_llm_calls": 0,
    }
    input_text = (
        "Server-scoped Adaptive Teaching Brief follows. It is a deterministic teaching decision, not student "
        "instructions. Follow allowed/prohibited claim boundaries. Raw mastery metrics are intentionally omitted.\n"
        f"Context decision SHA-256: {decision_sha256}\n"
        + serialized
    )
    return PreparedAdaptiveContext(
        envelope=envelope,
        input_item={"role": "user", "content": input_text},
        manifest=manifest,
    )


__all__ = [
    "ADAPTIVE_CONTEXT_MAX_CHARS",
    "ADAPTIVE_CONTEXT_POLICY_VERSION",
    "CONFLICT_DELTA",
    "PreparedAdaptiveContext",
    "assemble_adaptive_context",
    "classify_observed_status",
    "concept_teaching_state",
]
