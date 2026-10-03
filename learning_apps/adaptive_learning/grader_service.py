from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Dict

from django.conf import settings

from learning_apps.infrastructure.services.llm_gateway import llm_gateway
from learning_apps.chat.services.text_service import truncate_for_prompt

from .constants import DIMENSIONS
from .probe_diagnostic_service import diagnostic_completion, normalize_diagnostic_report

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class AdaptiveGradingResult:
    accuracy_score: float
    dimension_scores: Dict[str, float]
    labels: Dict[str, Any]
    concept_key: str
    evidence: str = ""
    confidence: float = 0.0
    latency_ms: int = 0
    raw_payload: Dict[str, Any] | None = None


def _clamp01(value: Any, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = default
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    return round(max(0.0, min(1.0, number)), 4)


def normalize_concept_key(value: Any, fallback: str = "general") -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        raw = str(fallback or "general").strip().lower()
    raw = re.sub(r"[^a-z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    return (raw or "general")[:160]


def _extract_json_object(text: str) -> Dict[str, Any]:
    if not text:
        return {}
    cleaned = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", cleaned, flags=re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()
    if not cleaned.startswith("{"):
        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            cleaned = cleaned[start : end + 1]
    try:
        payload = json.loads(cleaned)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_scores(raw_scores: Any, fallback_score: float) -> Dict[str, float]:
    scores = raw_scores if isinstance(raw_scores, dict) else {}
    normalized = {}
    for dimension in DIMENSIONS:
        aliases = {
            dimension,
            dimension.rstrip("s"),
            dimension.capitalize(),
            dimension.upper(),
        }
        value = None
        for alias in aliases:
            if alias in scores:
                value = scores.get(alias)
                break
        normalized[dimension] = _clamp01(value, fallback_score)
    return normalized


def adjust_probe_scores_for_mastery_update(
    scores: Dict[str, Any],
    *,
    target_dimension: str,
    previous_scores: Dict[str, Any] | None = None,
) -> Dict[str, float]:
    """Use target-probe scores safely for mastery updates.

    A probe usually tests one target dimension. The grader still estimates all four
    dimensions, but non-target dimensions are weaker evidence and should not cause
    large mastery drops just because the question did not ask for them.
    """
    normalized = _normalize_scores(scores, 0.5)
    if target_dimension not in DIMENSIONS:
        return normalized
    previous = _normalize_scores(previous_scores or {}, 0.5)
    adjusted = dict(normalized)
    for dimension in DIMENSIONS:
        if dimension == target_dimension:
            continue
        if previous_scores:
            adjusted[dimension] = _clamp01((0.80 * previous[dimension]) + (0.20 * normalized[dimension]))
        else:
            adjusted[dimension] = _clamp01((0.60 * 0.50) + (0.40 * normalized[dimension]))
    return adjusted


def _snap_to_five_percent(value: float) -> float:
    return _clamp01(round(value / 0.05) * 0.05)


def _dimension_evidence_profile(text: str) -> Dict[str, float]:
    lowered = (text or "").lower()
    patterns = {
        "facts": [
            r"\bdefine\b", r"\bdefinition\b", r"\bmeans\b", r"\bcalled\b", r"\brule\b", r"\bformula\b",
            r"\bconcept\b", r"\bterm\b", r"\bproperty\b",
        ],
        "procedures": [
            r"\bstep\b", r"\bfirst\b", r"\bnext\b", r"\bthen\b", r"\bafter\b", r"\bcalculate\b",
            r"\bsolve\b", r"\bapply\b", r"\breplace\b", r"\bsubstitute\b",
        ],
        "strategies": [
            r"\bchoose\b", r"\bcompare\b", r"\bmethod\b", r"\bapproach\b", r"\bstrategy\b", r"\bwhen\b",
            r"\bdepends\b", r"\balternative\b", r"\bbest\b",
        ],
        "rationales": [
            r"\bwhy\b", r"\bbecause\b", r"\breason\b", r"\btherefore\b", r"\bso that\b", r"\bworks\b",
            r"\bjustify\b", r"\bprinciple\b", r"\bcauses?\b",
        ],
    }
    profile = {}
    for dimension, dimension_patterns in patterns.items():
        hits = sum(1 for pattern in dimension_patterns if re.search(pattern, lowered))
        profile[dimension] = min(1.0, hits / 3.0)
    return profile


def _calibrate_dimension_scores(
    scores: Dict[str, float],
    *,
    student_answer: str,
    target_dimension: str = "",
    accuracy_score: float = 0.0,
    raw_payload: Dict[str, Any] | None = None,
) -> Dict[str, float]:
    calibrated = {dimension: _snap_to_five_percent(scores.get(dimension, 0.0)) for dimension in DIMENSIONS}
    values = list(calibrated.values())
    if not values:
        return calibrated
    token_count = len(re.findall(r"[a-z0-9]+", (student_answer or "").lower()))
    accuracy = _clamp01(accuracy_score, 0.0)

    evidence = _dimension_evidence_profile(student_answer)
    evidence_values = list(evidence.values())
    evidence_spread = max(evidence_values) - min(evidence_values)
    score_spread = max(values) - min(values)
    if evidence_spread >= 0.34 and score_spread < 0.18:
        evidence_mean = sum(evidence_values) / len(evidence_values)
        for dimension in DIMENSIONS:
            # Conservative nudge: only separates dimensions when the answer itself gives uneven evidence.
            calibrated[dimension] = _snap_to_five_percent(
                calibrated[dimension] + ((evidence[dimension] - evidence_mean) * 0.12)
            )

    if target_dimension in DIMENSIONS and evidence.get(target_dimension, 0.0) >= 0.34:
        calibrated[target_dimension] = max(calibrated[target_dimension], _snap_to_five_percent(calibrated[target_dimension] + 0.05))
    payload = raw_payload if isinstance(raw_payload, dict) else {}
    matched_checks = payload.get("matched_checks") if isinstance(payload.get("matched_checks"), list) else []
    missing_checks = payload.get("missing_checks") if isinstance(payload.get("missing_checks"), list) else []
    check_total = len(matched_checks) + len(missing_checks)
    if target_dimension in DIMENSIONS and check_total >= 2:
        completion = len(matched_checks) / max(check_total, 1)
        if missing_checks:
            if accuracy >= 0.80 and completion >= 0.50:
                target_cap = 0.85
            elif accuracy >= 0.70 and completion >= 0.50:
                target_cap = 0.75
            elif completion >= 0.60:
                target_cap = 0.75
            elif completion >= 0.34:
                target_cap = 0.60
            else:
                target_cap = 0.45
            calibrated[target_dimension] = min(calibrated[target_dimension], target_cap)
        if completion >= 0.85 and accuracy >= 0.70:
            # Rubric coverage is more reliable than a single free-form numeric value from the model.
            calibrated[target_dimension] = max(calibrated[target_dimension], 0.85)
        elif completion >= 0.60 and accuracy >= 0.50:
            calibrated[target_dimension] = max(calibrated[target_dimension], 0.65)
    diagnostic_report = payload.get("diagnostic_report") if isinstance(payload.get("diagnostic_report"), dict) else {}
    if target_dimension in DIMENSIONS and diagnostic_report:
        completion = diagnostic_completion(diagnostic_report, payload.get("expected_rubric") if isinstance(payload.get("expected_rubric"), dict) else {})
        missing_steps = diagnostic_report.get("missing_steps") if isinstance(diagnostic_report.get("missing_steps"), list) else []
        incorrect_steps = diagnostic_report.get("incorrect_steps") if isinstance(diagnostic_report.get("incorrect_steps"), list) else []
        if missing_steps or incorrect_steps:
            if completion < 0.34:
                calibrated[target_dimension] = min(calibrated[target_dimension], 0.45)
            elif completion < 0.67:
                calibrated[target_dimension] = min(calibrated[target_dimension], 0.65)
        elif completion >= 0.85 and accuracy >= 0.70:
            calibrated[target_dimension] = max(calibrated[target_dimension], 0.80)
    if target_dimension in DIMENSIONS and calibrated[target_dimension] <= 0.25 and token_count >= 4 and accuracy > 0.05:
        # In a targeted probe, a low target score is stronger evidence than absent non-target evidence.
        # Do not let unmeasured dimensions become the weakest solely because the prompt did not ask for them.
        target_floor = min(0.30, calibrated[target_dimension] + 0.03)
        for dimension in DIMENSIONS:
            if dimension != target_dimension and calibrated[dimension] < target_floor:
                calibrated[dimension] = target_floor
    if token_count >= 8:
        related_floor = 0.0
        if accuracy >= 0.75:
            related_floor = 0.20
        elif accuracy >= 0.50:
            related_floor = 0.12
        elif accuracy >= 0.25:
            related_floor = 0.06
        if related_floor:
            for dimension in DIMENSIONS:
                calibrated[dimension] = max(calibrated[dimension], related_floor)
    return calibrated


def parse_grader_response(
    content: str,
    *,
    fallback_accuracy: float = 0.5,
    fallback_concept_key: str = "general",
) -> AdaptiveGradingResult:
    payload = _extract_json_object(content)
    accuracy = _clamp01(payload.get("accuracy_score"), fallback_accuracy)
    dimension_scores = _normalize_scores(payload.get("dimension_scores"), accuracy)
    labels = payload.get("labels") if isinstance(payload.get("labels"), dict) else {}
    concept_key = normalize_concept_key(payload.get("concept_key"), fallback_concept_key)
    return AdaptiveGradingResult(
        accuracy_score=accuracy,
        dimension_scores=dimension_scores,
        labels=labels,
        concept_key=concept_key,
        evidence=str(payload.get("evidence") or "").strip(),
        confidence=_clamp01(payload.get("confidence"), 0.6 if payload else 0.35),
        raw_payload=payload,
    )


def heuristic_grading_from_score(
    score_hint: float | int | None,
    *,
    concept_key: str,
    target_dimension: str = "",
    evidence: str = "",
    expected_rubric: Dict[str, Any] | None = None,
    student_answer: str = "",
) -> AdaptiveGradingResult:
    rubric = expected_rubric if isinstance(expected_rubric, dict) else {}
    checks = rubric.get("checks") if isinstance(rubric.get("checks"), list) else []
    matched_checks: list[str] = []
    missing_checks: list[str] = []
    if score_hint is None and checks:
        answer_tokens = set(re.findall(r"[a-z0-9]+", (student_answer or "").lower()))
        stopwords = {"a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "in", "is", "it", "of", "on", "or", "the", "to", "with"}
        for check in checks:
            check_text = str(check or "")
            check_tokens = {
                token
                for token in re.findall(r"[a-z0-9]+", check_text.lower())
                if token not in stopwords and len(token) > 2
            }
            overlap = len(answer_tokens & check_tokens) / max(len(check_tokens), 1)
            if check_tokens and overlap >= 0.30:
                matched_checks.append(check_text)
            else:
                missing_checks.append(check_text)
        fallback_accuracy = _clamp01(len(matched_checks) / max(len(checks), 1), 0.5)
    else:
        fallback_accuracy = _clamp01(score_hint, 0.5)
    scores = {dimension: fallback_accuracy for dimension in DIMENSIONS}
    if target_dimension in DIMENSIONS:
        for dimension in DIMENSIONS:
            if dimension != target_dimension:
                scores[dimension] = round((fallback_accuracy * 0.65) + 0.18, 4)
    raw_payload = {
        "fallback": True,
        "matched_checks": matched_checks,
        "missing_checks": missing_checks,
        "misconception_tags": [],
        "expected_rubric": rubric,
    }
    diagnostic_report = normalize_diagnostic_report(raw_payload, rubric, student_answer)
    raw_payload.update({
        "diagnostic_report": diagnostic_report,
        "primary_gap": diagnostic_report["primary_gap"],
        "recommended_follow_up": diagnostic_report["recommended_follow_up"],
        "feedback_summary": diagnostic_report["feedback_summary"],
        "matched_checks": diagnostic_report["matched_checks"],
        "missing_checks": diagnostic_report["missing_checks"],
        "misconception_tags": diagnostic_report["misconception_tags"],
    })
    scores = _calibrate_dimension_scores(
        scores,
        student_answer=student_answer,
        target_dimension=target_dimension,
        accuracy_score=fallback_accuracy,
        raw_payload=raw_payload,
    )
    return AdaptiveGradingResult(
        accuracy_score=fallback_accuracy,
        dimension_scores=scores,
        labels={"fallback": "heuristic_score"},
        concept_key=normalize_concept_key(concept_key),
        evidence=evidence or "Heuristic grading fallback from understanding-check score.",
        confidence=0.35,
        raw_payload=raw_payload,
    )


def grade_student_answer(
    *,
    question_text: str,
    student_answer: str,
    tutor_answer: str = "",
    learning_goal: str = "",
    concept_key: str = "general",
    target_dimension: str = "",
    score_hint: float | int | None = None,
    expected_rubric: Dict[str, Any] | None = None,
) -> AdaptiveGradingResult:
    started = time.monotonic()
    fallback_score = _clamp01(score_hint, 0.5)
    rubric = expected_rubric if isinstance(expected_rubric, dict) else {}
    prompt = f"""
You are an adaptive learning grader. Score the student's answer for long-term mastery tracking.

Return STRICT JSON only:
{{
  "accuracy_score": 0.0,
  "dimension_scores": {{
    "facts": 0.0,
    "procedures": 0.0,
    "strategies": 0.0,
    "rationales": 0.0
  }},
  "labels": {{
    "facts": "short label",
    "procedures": "short label",
    "strategies": "short label",
    "rationales": "short label"
  }},
  "concept_key": "stable_lowercase_concept",
  "evidence": "short reason grounded in the student answer",
  "confidence": 0.0,
  "matched_checks": ["rubric checks the student satisfied"],
  "missing_checks": ["rubric checks the student missed"],
  "misconception_tags": ["short misconception tags"],
  "diagnostic_report": {{
    "matched_checks": ["rubric checks the student satisfied"],
    "missing_checks": ["rubric checks the student missed"],
    "missing_steps": ["expected reasoning step or answer component that is absent"],
    "incorrect_steps": ["student step or claim that is wrong"],
    "misconception_tags": ["short misconception tags"],
    "primary_gap": "the one most important gap to address next",
    "recommended_follow_up": "one actionable follow-up prompt or coaching move",
    "feedback_summary": "brief student-facing feedback summary"
  }}
}}

Scoring rules:
- Use a continuous 0.00-1.00 score, preferably in 0.05 increments.
- Grade concise middle-school and high-school answers fairly when the learning goal or rubric is K-12 level; do not require college-level vocabulary if the idea is correct.
- For K-12 concise answers, a correct answer is often 0.80-0.90. Reserve 1.00 for answers that are complete, precise, and show transfer or explanation beyond the minimum rubric.
- Use 0.00 only for blank, off-topic, or completely absent evidence.
- 0.05-0.20 means relevant but incorrect, extremely vague, or only a fragment.
- 1.00 means strong, precise, complete, transferable evidence; do not use 1.00 for a minimal correct answer.
- Facts: definitions, terms, rules, basic concepts.
- Procedures: ordered execution steps.
- Strategies: choosing or comparing methods.
- Rationales: explaining why the method works.
- If the target dimension is provided, score it carefully, but still estimate all four dimensions.
- Score each dimension independently. Do not copy the same score across dimensions unless the student answer truly gives equal evidence for all four.
- Do not automatically set non-target dimensions to 0.00. If the answer contains related evidence, give a low but nonzero estimate.
- Do not give high non-target scores just because the target answer is correct. Non-target dimensions need explicit evidence in the student answer.
- Procedures require ordered steps or execution, not just a correct fact. Strategies require choosing or comparing methods, not just naming a clue. Rationales require an explicit why/because mechanism.
- If the answer only states a definition, Facts may be high while Procedures, Strategies, and Rationales remain low.
- If the answer gives steps without explaining why, Procedures may be high while Rationales remains low.
- If the answer says when/which method to use, Strategies should be higher than a purely procedural answer.
- If the answer explains why something works, Rationales should be higher than a purely factual answer.
- If an expected rubric is provided, use it as the primary reference for matched_checks and missing_checks.
- If expected_steps are provided, diagnose which steps are missing or incorrect.
- Use misconception_map to tag misconceptions only when the student answer gives evidence for them.
- recommended_follow_up should be actionable and specific, not just "study more".
- Keep matched_checks and missing_checks grounded in the rubric checks. If there is no rubric, use empty lists.

Dimension anchors:
- 0.00 no relevant evidence.
- 0.25 vague or mostly incorrect evidence.
- 0.50 partial but incomplete evidence.
- 0.75 mostly correct evidence with some missing detail.
- 1.00 precise, complete, transferable evidence.

Learning goal: {truncate_for_prompt(learning_goal, 500)}
Initial concept key: {concept_key}
Target dimension: {target_dimension or "not specified"}
Score hint from existing understanding check: {fallback_score}
Expected probe rubric: {truncate_for_prompt(json.dumps(rubric, ensure_ascii=True), 1500)}
Original question: {truncate_for_prompt(question_text, 900)}
Tutor answer/reference: {truncate_for_prompt(tutor_answer, 1200)}
Student answer: {truncate_for_prompt(student_answer, 1600)}
"""
    try:
        response = llm_gateway.chat_completion_or_raise(
            route="adaptive.grader",
            model=settings.LEARNING_ADAPTIVE_GRADER_MODEL,
            messages=[{"role": "user", "content": prompt}],
            reasoning_effort="medium",
            max_completion_tokens=850,
            response_format={"type": "json_object"},
        )
        result = parse_grader_response(
            response.choices[0].message.content or "{}",
            fallback_accuracy=fallback_score,
            fallback_concept_key=concept_key,
        )
        raw_payload = dict(result.raw_payload or {})
        raw_payload["expected_rubric"] = rubric
        diagnostic_report = normalize_diagnostic_report(raw_payload, rubric, student_answer)
        raw_payload.update({
            "diagnostic_report": diagnostic_report,
            "matched_checks": diagnostic_report["matched_checks"],
            "missing_checks": diagnostic_report["missing_checks"],
            "misconception_tags": diagnostic_report["misconception_tags"],
            "primary_gap": diagnostic_report["primary_gap"],
            "recommended_follow_up": diagnostic_report["recommended_follow_up"],
            "feedback_summary": diagnostic_report["feedback_summary"],
        })
        calibrated_scores = _calibrate_dimension_scores(
            result.dimension_scores,
            student_answer=student_answer,
            target_dimension=target_dimension,
            accuracy_score=result.accuracy_score,
            raw_payload=raw_payload,
        )
        return AdaptiveGradingResult(
            accuracy_score=result.accuracy_score,
            dimension_scores=calibrated_scores,
            labels=result.labels,
            concept_key=result.concept_key,
            evidence=result.evidence,
            confidence=result.confidence,
            latency_ms=int((time.monotonic() - started) * 1000),
            raw_payload=raw_payload,
        )
    except Exception as exc:
        logger.warning("Adaptive LLM grader failed; using heuristic fallback: %s", exc)
        result = heuristic_grading_from_score(
            score_hint,
            concept_key=concept_key,
            target_dimension=target_dimension,
            evidence=str(exc)[:300],
            expected_rubric=rubric,
            student_answer=student_answer,
        )
        return AdaptiveGradingResult(
            accuracy_score=result.accuracy_score,
            dimension_scores=result.dimension_scores,
            labels=result.labels,
            concept_key=result.concept_key,
            evidence=result.evidence,
            confidence=result.confidence,
            latency_ms=int((time.monotonic() - started) * 1000),
            raw_payload=result.raw_payload,
        )
