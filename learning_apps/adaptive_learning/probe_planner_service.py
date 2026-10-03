from __future__ import annotations

from typing import Any, Dict, List

from learning_apps.persistence.models import AdaptiveProbe

from .constants import DIMENSIONS
from .mastery_service import clamp01

MICRO_PROBE_VERSION = "micro_v2"
DEFAULT_MICRO_ITEM_COUNT = 3
MAX_MICRO_ITEM_COUNT = 4

DIAGNOSTIC_TYPES = {
    "recall",
    "procedure_trace",
    "strategy_choice",
    "why_explain",
    "transfer",
    "error_diagnosis",
}

DIMENSION_DIAGNOSTIC_DEFAULT = {
    "facts": "recall",
    "procedures": "procedure_trace",
    "strategies": "strategy_choice",
    "rationales": "why_explain",
}

DIAGNOSTIC_ANSWER_SCHEMA = {
    "recall": "short_explanation",
    "procedure_trace": "procedure_steps",
    "strategy_choice": "strategy_compare",
    "why_explain": "rationale_explain",
    "transfer": "final_answer_with_reasoning",
    "error_diagnosis": "error_analysis",
}


def diagnostic_answer_schema(diagnostic_type: str, target_dimension: str = "") -> str:
    diagnostic = diagnostic_type if diagnostic_type in DIAGNOSTIC_TYPES else ""
    if diagnostic:
        return DIAGNOSTIC_ANSWER_SCHEMA[diagnostic]
    return DIAGNOSTIC_ANSWER_SCHEMA[DIMENSION_DIAGNOSTIC_DEFAULT.get(target_dimension, "recall")]


def diagnostic_type_for_candidate(candidate: Dict[str, Any], *, role: str = "") -> str:
    existing = str(candidate.get("diagnostic_type") or "").strip()
    if existing in DIAGNOSTIC_TYPES:
        return existing
    if role == "transfer_challenge":
        return "transfer"
    target_dimension = str(candidate.get("target_dimension") or "facts")
    return DIMENSION_DIAGNOSTIC_DEFAULT.get(target_dimension, "recall")


def _components(candidate: Dict[str, Any]) -> Dict[str, float]:
    raw = candidate.get("priority_components") if isinstance(candidate.get("priority_components"), dict) else {}
    return {
        "mastery_gap": clamp01(raw.get("mastery_gap"), 0.0),
        "uncertainty": clamp01(raw.get("uncertainty"), 0.0),
        "recent_relevance": clamp01(raw.get("recent_relevance"), 0.0),
        "staleness": clamp01(raw.get("staleness"), 0.0),
        "prerequisite_importance": clamp01(raw.get("prerequisite_importance"), 0.0),
        "forgetting_risk": clamp01(raw.get("forgetting_risk"), 0.0),
        "curve_need": clamp01(raw.get("curve_need"), 0.0),
        "recent_probe_penalty": clamp01(raw.get("recent_probe_penalty"), 0.0),
        "same_dimension_penalty": clamp01(raw.get("same_dimension_penalty"), 0.0),
    }


def _candidate_score(candidate: Dict[str, Any], *, role: str = "") -> float:
    components = _components(candidate)
    base = float(candidate.get("priority_score") or 0.0)
    coverage_penalty = 0.20 * components["recent_probe_penalty"] + 0.12 * components["same_dimension_penalty"]
    if role == "current_recent":
        return base + (0.50 * components["recent_relevance"]) - coverage_penalty
    if role == "stale_forgetting":
        return base + (0.45 * components["forgetting_risk"]) + (0.35 * components["staleness"]) - coverage_penalty
    if role == "prerequisite_related":
        return base + (0.45 * components["prerequisite_importance"]) - coverage_penalty
    if role == "transfer_challenge":
        return base + (0.35 * components["curve_need"]) + (0.20 * components["mastery_gap"]) - coverage_penalty
    return base - coverage_penalty


def _risk_level(candidates: List[Dict[str, Any]], *, due_by_forgetting: bool) -> str:
    if due_by_forgetting:
        return "high"
    if not candidates:
        return "normal"
    max_forgetting = max(_components(candidate)["forgetting_risk"] for candidate in candidates)
    max_staleness = max(_components(candidate)["staleness"] for candidate in candidates)
    max_curve_need = max(_components(candidate)["curve_need"] for candidate in candidates)
    if max_forgetting >= 0.75 or max_curve_need >= 0.18:
        return "high"
    if max_forgetting >= 0.45 or max_staleness >= 0.70 or max_curve_need >= 0.10:
        return "elevated"
    return "normal"


def _clone_with_dimension(candidate: Dict[str, Any], target_dimension: str, *, role: str = "") -> Dict[str, Any]:
    cloned = dict(candidate)
    cloned["target_dimension"] = target_dimension
    diagnostic = diagnostic_type_for_candidate(cloned, role=role)
    cloned["diagnostic_type"] = diagnostic
    cloned["answer_schema"] = diagnostic_answer_schema(diagnostic, target_dimension)
    return cloned


def _alternate_dimension_candidates(candidate: Dict[str, Any], *, role: str = "") -> List[Dict[str, Any]]:
    current_dimension = str(candidate.get("target_dimension") or "facts")
    mastery_vector = candidate.get("mastery_vector") if isinstance(candidate.get("mastery_vector"), dict) else {}
    alternatives = [dimension for dimension in DIMENSIONS if dimension != current_dimension]
    alternatives.sort(key=lambda dimension: (clamp01(mastery_vector.get(dimension), 0.45), list(DIMENSIONS).index(dimension)))
    return [_clone_with_dimension(candidate, dimension, role=role) for dimension in alternatives]


def _coverage_key(candidate: Dict[str, Any], *, role: str = "") -> tuple[str, str, str]:
    concept_key = str(candidate.get("concept_key") or "")
    target_dimension = str(candidate.get("target_dimension") or "facts")
    diagnostic_type = diagnostic_type_for_candidate(candidate, role=role)
    return concept_key, target_dimension, diagnostic_type


def _recently_covered_targets(recent_probes: List[AdaptiveProbe]) -> List[Dict[str, Any]]:
    covered: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for probe in recent_probes:
        rubric = probe.expected_rubric if isinstance(probe.expected_rubric, dict) else {}
        items = rubric.get("probe_items") if isinstance(rubric.get("probe_items"), list) else []
        if items:
            for item in items:
                if not isinstance(item, dict):
                    continue
                key = (
                    str(item.get("concept_key") or probe.concept_key or ""),
                    str(item.get("target_dimension") or probe.target_dimension or ""),
                    str(item.get("diagnostic_type") or ""),
                )
                if key in seen:
                    continue
                seen.add(key)
                covered.append({
                    "concept_key": key[0],
                    "target_dimension": key[1],
                    "diagnostic_type": key[2],
                    "probe_id": probe.id,
                })
        else:
            key = (str(probe.concept_key or ""), str(probe.target_dimension or ""), "")
            if key not in seen:
                seen.add(key)
                covered.append({
                    "concept_key": key[0],
                    "target_dimension": key[1],
                    "diagnostic_type": key[2],
                    "probe_id": probe.id,
                })
    return covered


def _candidate_target(candidate: Dict[str, Any], *, role: str = "") -> Dict[str, Any]:
    diagnostic_type = diagnostic_type_for_candidate(candidate, role=role)
    return {
        "concept_key": str(candidate.get("concept_key") or ""),
        "concept_label": str(candidate.get("concept_label") or ""),
        "target_dimension": str(candidate.get("target_dimension") or "facts"),
        "diagnostic_type": diagnostic_type,
        "role": role or str(candidate.get("selection_role") or ""),
        "priority_score": float(candidate.get("priority_score") or 0.0),
    }


def build_probe_plan(
    candidates: List[Dict[str, Any]],
    *,
    due_by_forgetting: bool = False,
    recent_probes: List[AdaptiveProbe] | None = None,
) -> Dict[str, Any]:
    """Select a cross-concept micro-probe set and explain why it was selected."""
    if not candidates:
        return {
            "probe_version": MICRO_PROBE_VERSION,
            "risk_level": "normal",
            "selected_candidates": [],
            "probe_plan": {
                "selection_reason": "no_candidates",
                "coverage_targets": [],
                "forgotten_or_stale_targets": [],
                "recently_covered_targets": [],
                "risk_level": "normal",
                "planner_debug": {},
            },
        }

    recent_probes = recent_probes or []
    risk = _risk_level(candidates, due_by_forgetting=due_by_forgetting)
    target_count = MAX_MICRO_ITEM_COUNT if risk == "high" else DEFAULT_MICRO_ITEM_COUNT
    roles = ["current_recent", "stale_forgetting", "prerequisite_related"]
    if target_count >= 4:
        roles.append("transfer_challenge")

    selected: List[Dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()

    def add(candidate: Dict[str, Any] | None, *, role: str) -> None:
        if not candidate or len(selected) >= target_count:
            return
        prepared = dict(candidate)
        prepared["selection_role"] = role
        diagnostic_type = diagnostic_type_for_candidate(prepared, role=role)
        prepared["diagnostic_type"] = diagnostic_type
        prepared["answer_schema"] = diagnostic_answer_schema(diagnostic_type, str(prepared.get("target_dimension") or "facts"))
        key = _coverage_key(prepared, role=role)
        if not key[0] or key in seen:
            return
        selected.append(prepared)
        seen.add(key)

    for role in roles:
        selected_concepts = {str(item.get("concept_key") or "") for item in selected}
        ordered = sorted(
            candidates,
            key=lambda item: (
                str(item.get("concept_key") or "") in selected_concepts,
                -_candidate_score(item, role=role),
                str(item.get("concept_key") or ""),
            ),
        )
        for candidate in ordered:
            add(candidate, role=role)
            if selected and selected[-1].get("selection_role") == role:
                break

    for candidate in sorted(candidates, key=lambda item: (-_candidate_score(item), str(item.get("concept_key") or ""))):
        add(candidate, role="coverage_backfill")
        if len(selected) >= target_count:
            break

    if len(selected) < target_count:
        for base in list(selected) or candidates[:1]:
            for alternate in _alternate_dimension_candidates(base, role="coverage_backfill"):
                add(alternate, role="coverage_backfill")
                if len(selected) >= target_count:
                    break
            if len(selected) >= target_count:
                break

    coverage_targets = [_candidate_target(candidate, role=str(candidate.get("selection_role") or "")) for candidate in selected]
    stale_targets = [
        _candidate_target(candidate, role=str(candidate.get("selection_role") or ""))
        for candidate in selected
        if _components(candidate)["forgetting_risk"] >= 0.45 or _components(candidate)["staleness"] >= 0.70
    ]
    recently_covered = _recently_covered_targets(recent_probes)
    selection_reason = (
        "high_risk_coverage" if risk == "high"
        else "elevated_recency_weighted_coverage" if risk == "elevated"
        else "recency_weighted_coverage"
    )

    return {
        "probe_version": MICRO_PROBE_VERSION,
        "risk_level": risk,
        "selected_candidates": selected,
        "probe_plan": {
            "selection_reason": selection_reason,
            "coverage_targets": coverage_targets,
            "forgotten_or_stale_targets": stale_targets,
            "recently_covered_targets": recently_covered,
            "risk_level": risk,
            "planner_debug": {
                "candidate_count": len(candidates),
                "target_count": target_count,
                "role_order": roles,
                "selected_keys": ["|".join(_coverage_key(candidate)) for candidate in selected],
            },
        },
    }
