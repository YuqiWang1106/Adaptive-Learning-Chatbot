from __future__ import annotations

import re
from typing import Any, Dict, List


def _string_list(value: Any) -> List[str]:
    raw_items = value if isinstance(value, list) else []
    return [str(item).strip() for item in raw_items if str(item).strip()]


def _token_set(text: str) -> set[str]:
    stopwords = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "the",
        "to",
        "with",
    }
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if token not in stopwords and len(token) > 2
    }


def _overlap_ratio(source: str, target: str) -> float:
    source_tokens = _token_set(source)
    target_tokens = _token_set(target)
    if not source_tokens or not target_tokens:
        return 0.0
    return len(source_tokens & target_tokens) / max(len(source_tokens), 1)


def _misconception_tags_from_map(misconception_map: Any, student_answer: str) -> List[str]:
    tags: List[str] = []
    for item in misconception_map if isinstance(misconception_map, list) else []:
        if not isinstance(item, dict):
            continue
        tag = str(item.get("tag") or "").strip()
        pattern = str(item.get("evidence_pattern") or "").strip()
        if not tag:
            continue
        if not pattern:
            continue
        if _overlap_ratio(pattern, student_answer) >= 0.22:
            tags.append(tag)
    return tags


def _repair_hint_for_tag(misconception_map: Any, tags: List[str]) -> str:
    tag_set = set(tags)
    for item in misconception_map if isinstance(misconception_map, list) else []:
        if not isinstance(item, dict):
            continue
        if str(item.get("tag") or "").strip() in tag_set:
            hint = str(item.get("repair_hint") or "").strip()
            if hint:
                return hint
    return ""


def normalize_diagnostic_report(
    raw_payload: Dict[str, Any] | None,
    expected_rubric: Dict[str, Any] | None,
    student_answer: str = "",
) -> Dict[str, Any]:
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    rubric = expected_rubric if isinstance(expected_rubric, dict) else {}
    report = payload.get("diagnostic_report") if isinstance(payload.get("diagnostic_report"), dict) else {}

    matched_checks = _string_list(report.get("matched_checks")) or _string_list(payload.get("matched_checks"))
    missing_checks = _string_list(report.get("missing_checks")) or _string_list(payload.get("missing_checks"))
    expected_checks = _string_list(rubric.get("checks"))
    if expected_checks and not matched_checks and not missing_checks:
        for check in expected_checks:
            if _overlap_ratio(check, student_answer) >= 0.30:
                matched_checks.append(check)
            else:
                missing_checks.append(check)

    missing_steps = _string_list(report.get("missing_steps")) or _string_list(payload.get("missing_steps"))
    incorrect_steps = _string_list(report.get("incorrect_steps")) or _string_list(payload.get("incorrect_steps"))
    expected_steps = _string_list(rubric.get("expected_steps"))
    if expected_steps and not missing_steps and not incorrect_steps:
        for step in expected_steps:
            if _overlap_ratio(step, student_answer) < 0.22:
                missing_steps.append(step)

    misconception_tags = _string_list(report.get("misconception_tags")) or _string_list(payload.get("misconception_tags"))
    misconception_tags.extend(
        tag for tag in _misconception_tags_from_map(rubric.get("misconception_map"), student_answer)
        if tag not in misconception_tags
    )

    primary_gap = str(report.get("primary_gap") or payload.get("primary_gap") or "").strip()
    if not primary_gap:
        if missing_steps:
            primary_gap = missing_steps[0]
        elif incorrect_steps:
            primary_gap = incorrect_steps[0]
        elif missing_checks:
            primary_gap = missing_checks[0]
        elif misconception_tags:
            primary_gap = misconception_tags[0].replace("_", " ")
        else:
            primary_gap = "No major gap detected."

    recommended_follow_up = str(report.get("recommended_follow_up") or payload.get("recommended_follow_up") or "").strip()
    if not recommended_follow_up:
        recommended_follow_up = _repair_hint_for_tag(rubric.get("misconception_map"), misconception_tags)
    if not recommended_follow_up:
        recommended_follow_up = str(rubric.get("feedback_focus") or "").strip()
    if not recommended_follow_up:
        recommended_follow_up = "Ask one focused follow-up that targets the primary gap."

    feedback_summary = str(report.get("feedback_summary") or payload.get("feedback_summary") or "").strip()
    if not feedback_summary:
        if primary_gap == "No major gap detected.":
            feedback_summary = "The response covered the main rubric targets."
        else:
            feedback_summary = f"Main next step: {primary_gap}"

    return {
        "matched_checks": matched_checks,
        "missing_checks": missing_checks,
        "missing_steps": missing_steps,
        "incorrect_steps": incorrect_steps,
        "misconception_tags": misconception_tags,
        "primary_gap": primary_gap,
        "recommended_follow_up": recommended_follow_up,
        "feedback_summary": feedback_summary,
        "fallback": bool(payload.get("fallback") or report.get("fallback")),
    }


def diagnostic_completion(diagnostic_report: Dict[str, Any] | None, expected_rubric: Dict[str, Any] | None) -> float:
    report = diagnostic_report if isinstance(diagnostic_report, dict) else {}
    rubric = expected_rubric if isinstance(expected_rubric, dict) else {}
    expected_checks = _string_list(rubric.get("checks"))
    expected_steps = _string_list(rubric.get("expected_steps"))

    matched_checks = _string_list(report.get("matched_checks"))
    missing_checks = _string_list(report.get("missing_checks"))
    missing_steps = _string_list(report.get("missing_steps"))
    incorrect_steps = _string_list(report.get("incorrect_steps"))

    components: List[float] = []
    check_total = len(matched_checks) + len(missing_checks)
    if expected_checks or check_total:
        components.append(len(matched_checks) / max(check_total or len(expected_checks), 1))
    if expected_steps:
        complete_steps = max(0, len(expected_steps) - len(missing_steps) - len(incorrect_steps))
        components.append(complete_steps / max(len(expected_steps), 1))
    if not components:
        return 0.5
    return round(max(0.0, min(1.0, sum(components) / len(components))), 4)
