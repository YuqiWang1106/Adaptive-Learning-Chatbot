from __future__ import annotations

from collections import Counter
from datetime import timedelta
from statistics import mean
from typing import Any

from django.http import Http404
from django.db.models import Avg, Count, Max

from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    AdaptiveProbe,
    ChatConceptSignal,
    LearnerMasteryState,
    LearningGoal,
    SelfAssessment,
    UserProfile,
)
from learning_apps.chat.services.conversation_repository_service import visible_history_queryset
from learning_apps.teacher_portal.models import ClassroomMembership
from learning_apps.teacher_portal.services.classroom_service import get_student_membership_for_teacher


DIMENSIONS = ("facts", "procedures", "strategies", "rationales")
DIMENSION_META = {
    "facts": {
        "label": "Facts",
        "icon": "bi-journal-text",
        "description": "Accuracy and recall of essential knowledge.",
    },
    "procedures": {
        "label": "Procedures",
        "icon": "bi-list-check",
        "description": "Ability to carry out the required steps.",
    },
    "strategies": {
        "label": "Strategies",
        "icon": "bi-signpost-split",
        "description": "Selection of an effective approach.",
    },
    "rationales": {
        "label": "Rationales",
        "icon": "bi-lightbulb",
        "description": "Ability to explain why an answer or method works.",
    },
}


def _iso(value) -> str:
    return value.isoformat() if value else ""


def _pct(value: Any) -> int:
    try:
        numeric = float(value)
        if numeric > 1:
            numeric /= 100
        return round(max(0.0, min(1.0, numeric)) * 100)
    except (TypeError, ValueError):
        return 0


def _score(value: Any) -> float:
    try:
        return round(float(value or 0.0), 4)
    except (TypeError, ValueError):
        return 0.0


def _label(value: str) -> str:
    return str(value or "").replace("_", " ").strip().title()


def _score_state(value: int) -> dict[str, str]:
    if value >= 75:
        return {"key": "strong", "label": "Strong"}
    if value >= 50:
        return {"key": "developing", "label": "Developing"}
    if value > 0:
        return {"key": "attention", "label": "Needs attention"}
    return {"key": "empty", "label": "No evidence"}


def _dimension_rows(values: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for key in DIMENSIONS:
        value = _pct(values.get(key, 0))
        rows.append(
            {
                "key": key,
                "value": value,
                "state": _score_state(value),
                **DIMENSION_META[key],
            }
        )
    return rows


def _display_value(value: Any) -> str:
    if isinstance(value, dict):
        return "; ".join(f"{_label(key)}: {_display_value(item)}" for key, item in value.items())
    if isinstance(value, list):
        return "; ".join(_display_value(item) for item in value)
    if isinstance(value, bool):
        return "Yes" if value else "No"
    return str(value or "").strip()


def _structured_sections(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict):
        return []
    sections = []
    for key, value in payload.items():
        score = None
        summary = ""
        details = []
        if isinstance(value, dict):
            score = _pct(value.get("score")) if value.get("score") is not None else None
            summary = _display_value(value.get("summary") or value.get("assessment") or value.get("feedback"))
            details = [
                {"label": _label(item_key), "value": _display_value(item_value)}
                for item_key, item_value in value.items()
                if item_key not in {"score", "summary", "assessment", "feedback"} and _display_value(item_value)
            ]
        else:
            summary = _display_value(value)
        if summary or details or score is not None:
            sections.append(
                {
                    "key": str(key).lower(),
                    "label": _label(key),
                    "summary": summary,
                    "score": score,
                    "details": details,
                }
            )
    return sections


def _strategy_items(payload: Any) -> list[dict[str, str]]:
    if not isinstance(payload, dict):
        return []
    return [
        {"label": _label(key), "value": _display_value(value)}
        for key, value in payload.items()
        if _display_value(value)
    ]


def _assessment_summary(assessment: SelfAssessment | None) -> dict[str, Any]:
    if not assessment:
        return {
            "exists": False,
            "student_text": "",
            "evaluation_report": "",
            "structured_sections": [],
            "strategy_items": [],
            "perceived_dimensions": [],
            "created_at": "",
        }
    perceived = assessment.perceived_state_projections.filter(
        mastery_write_authorized=False
    ).first()
    diagnostic_summary = (
        perceived.diagnostic_summary
        if perceived and isinstance(perceived.diagnostic_summary, dict)
        else {}
    )
    metadata = assessment.diagnostic_metadata if isinstance(assessment.diagnostic_metadata, dict) else {}
    sufficiency = metadata.get("evidence_sufficiency")
    sufficiency = sufficiency if isinstance(sufficiency, dict) else {}
    band_labels = {
        "reports_high_support_need": "Self-reports a useful starting point",
        "reports_moderate_support_need": "Self-reports developing confidence",
        "reports_relative_confidence": "Self-reports relative confidence",
        "reports_not_started": "Reports not started",
        "evidence_thin": "Too little detail for personalization",
    }
    perceived_dimensions = []
    for key in DIMENSIONS:
        item = diagnostic_summary.get(key) if isinstance(diagnostic_summary.get(key), dict) else {}
        evidence_state = str(
            item.get("evidence_sufficiency") or sufficiency.get(key) or "insufficient"
        )
        perceived_dimensions.append(
            {
                "key": key,
                "label": DIMENSION_META[key]["label"],
                "band": band_labels.get(str(item.get("band") or ""), "Needs revision"),
                "evidence_sufficiency": evidence_state.replace("_", " ").title(),
            }
        )
    return {
        "exists": True,
        "target_problem": str(
            assessment.target_problem
            or (assessment.submission_snapshot or {}).get("target_task")
            or ""
        ),
        "scope_contract": (
            dict(assessment.scope_contract)
            if isinstance(assessment.scope_contract, dict)
            else {}
        ),
        "student_text": assessment.student_text,
        "evaluation_report": assessment.evaluation_report,
        "structured_sections": _structured_sections(assessment.structured_report or {}),
        "strategy_items": _strategy_items(assessment.operational_strategy or {}),
        "perceived_dimensions": perceived_dimensions,
        "goal_alignment": str(metadata.get("goal_alignment") or "unknown").replace("_", " ").title(),
        "created_at": _iso(assessment.created_at),
    }


def _goal_mastery(goal: LearningGoal) -> dict[str, Any]:
    states = list(
        LearnerMasteryState.objects.filter(learning_goal=goal)
        .order_by("-updated_at")
        .values(
            "concept_key",
            "facts_mastery",
            "procedures_mastery",
            "strategies_mastery",
            "rationales_mastery",
            "dimension_mastery_score",
            "quality_score",
            "weakest_dimension",
            "curve_pattern",
            "curve_confidence",
            "curve_evidence_count",
            "feedback_tier",
            "event_count",
            "updated_at",
        )
    )
    if not states:
        empty_values = {dimension: 0 for dimension in DIMENSIONS}
        return {
            "has_data": False,
            "average_mastery": empty_values,
            "dimension_rows": _dimension_rows(empty_values),
            "quality_score": 0,
            "quality_state": _score_state(0),
            "weakest_dimension": "",
            "weakest_label": "Not enough evidence",
            "curve_pattern": "insufficient_data",
            "curve_label": "Collecting evidence",
            "curve_confidence": 0,
            "evidence_count": 0,
            "concept_count": 0,
            "concepts": [],
        }

    average_mastery = {
        dimension: _pct(mean(_score(row[f"{dimension}_mastery"]) for row in states))
        for dimension in DIMENSIONS
    }
    weakest_counter = Counter(row["weakest_dimension"] for row in states if row["weakest_dimension"])
    pattern_counter = Counter(row["curve_pattern"] for row in states if row["curve_pattern"])
    weakest_dimension = weakest_counter.most_common(1)[0][0] if weakest_counter else ""
    curve_pattern = pattern_counter.most_common(1)[0][0] if pattern_counter else "insufficient_data"
    quality_score = _pct(mean(_score(row["quality_score"]) for row in states))
    concepts = []
    for row in states:
        dimension_values = {dimension: _pct(row[f"{dimension}_mastery"]) for dimension in DIMENSIONS}
        concepts.append(
            {
                "concept_key": row["concept_key"],
                "concept_label": _label(row["concept_key"]),
                "quality_score": _pct(row["quality_score"]),
                "quality_state": _score_state(_pct(row["quality_score"])),
                "dimension_mastery_score": _pct(row["dimension_mastery_score"]),
                "dimension_rows": _dimension_rows(dimension_values),
                "weakest_dimension": row["weakest_dimension"],
                "weakest_label": _label(row["weakest_dimension"]),
                "curve_pattern": row["curve_pattern"],
                "curve_label": _label(row["curve_pattern"]),
                "curve_confidence": _pct(row["curve_confidence"]),
                "feedback_tier": _label(row["feedback_tier"]),
                "event_count": row["event_count"],
                "updated_at": _iso(row["updated_at"]),
            }
        )
    return {
        "has_data": True,
        "average_mastery": average_mastery,
        "dimension_rows": _dimension_rows(average_mastery),
        "quality_score": quality_score,
        "quality_state": _score_state(quality_score),
        "weakest_dimension": weakest_dimension,
        "weakest_label": _label(weakest_dimension) or "Not enough evidence",
        "curve_pattern": curve_pattern,
        "curve_label": _label(curve_pattern),
        "curve_confidence": round(mean(_pct(row["curve_confidence"]) for row in states)),
        "evidence_count": sum(int(row["event_count"] or 0) for row in states),
        "concept_count": len(states),
        "concepts": concepts,
    }


def _goal_analysis(
    *,
    assessment: dict[str, Any],
    mastery: dict[str, Any],
    chat_count: int,
    probes: list[dict[str, Any]],
) -> dict[str, Any]:
    strengths = [row["label"] for row in mastery["dimension_rows"] if row["value"] >= 75]
    risks = [row["label"] for row in mastery["dimension_rows"] if 0 < row["value"] < 50]
    pending_probes = sum(1 for probe in probes if probe["status"] == AdaptiveProbe.STATUS_PENDING)
    actions = []
    if risks:
        actions.append(f"Plan a short intervention focused on {', '.join(risks)}.")
    elif mastery["weakest_dimension"]:
        actions.append(f"Use the next check to strengthen {mastery['weakest_label']}.")
    if not assessment["exists"]:
        actions.append("Ask the student to complete the baseline self-assessment.")
    if chat_count == 0:
        actions.append("Start a guided conversation to collect applied learning evidence.")
    if pending_probes:
        actions.append(f"Review {pending_probes} pending diagnostic probe{'s' if pending_probes != 1 else ''} after completion.")
    if not actions:
        actions.append("Continue the current learning sequence and review the next scored probe.")

    if not mastery["has_data"]:
        status = {"key": "collecting", "label": "Collecting evidence"}
        headline = "There is not enough scored evidence for a confident teaching decision yet."
    elif mastery["quality_score"] < 50:
        status = {"key": "priority", "label": "Priority review"}
        headline = f"The goal needs targeted support, especially in {mastery['weakest_label']}."
    elif mastery["quality_score"] < 75:
        status = {"key": "monitor", "label": "Monitor closely"}
        headline = f"Progress is developing; {mastery['weakest_label']} is the clearest next focus."
    else:
        status = {"key": "on_track", "label": "On track"}
        headline = "Current evidence shows a stable foundation with room for continued challenge."
    return {
        "status": status,
        "headline": headline,
        "strengths": strengths,
        "risks": risks,
        "next_actions": actions,
    }


def _goal_trend(goal: LearningGoal) -> dict[str, Any]:
    """Aggregate scored interaction evidence into weekly mastery checkpoints."""
    rows = list(
        AdaptiveInteractionEvent.objects.filter(learning_goal=goal)
        .order_by("created_at", "id")
        .values("created_at", "accuracy_score", "dimension_scores")
    )
    weekly: dict[Any, dict[str, list[float]]] = {}
    for row in rows:
        created_at = row["created_at"]
        week_start = created_at.date() - timedelta(days=created_at.weekday())
        bucket = weekly.setdefault(week_start, {dimension: [] for dimension in DIMENSIONS})
        scores = row["dimension_scores"] if isinstance(row["dimension_scores"], dict) else {}
        for dimension in DIMENSIONS:
            raw_score = scores.get(dimension, row["accuracy_score"])
            bucket[dimension].append(_score(raw_score))

    points = []
    for week_start, values in sorted(weekly.items())[-52:]:
        dimensions = {
            dimension: _pct(mean(values[dimension])) if values[dimension] else 0
            for dimension in DIMENSIONS
        }
        points.append(
            {
                "date": week_start.isoformat(),
                "label": week_start.strftime("%b %d"),
                **dimensions,
                "overall": round(mean(dimensions.values())),
            }
        )

    start_score = points[0]["overall"] if points else 0
    current_score = points[-1]["overall"] if points else 0
    return {
        "has_data": len(points) >= 2,
        "points": points,
        "event_count": len(rows),
        "checkpoint_count": len(points),
        "start_score": start_score,
        "current_score": current_score,
        "delta": current_score - start_score,
    }


def _goal_report(goal: LearningGoal) -> dict[str, Any]:
    assessment_model = SelfAssessment.objects.filter(learning_goal=goal).order_by("-created_at", "-id").first()
    event_rows = list(AdaptiveInteractionEvent.objects.filter(learning_goal=goal).order_by("-created_at")[:16])
    probe_rows = list(AdaptiveProbe.objects.filter(learning_goal=goal).order_by("-created_at")[:16])
    chat_rows = list(visible_history_queryset().filter(learning_goal=goal).order_by("-timestamp")[:12])
    concept_signals = list(
        ChatConceptSignal.objects.filter(learning_goal=goal)
        .values("concept_label", "concept_key")
        .annotate(count=Count("id"), last_seen=Max("created_at"), confidence=Avg("confidence"))
        .order_by("-count", "-last_seen")[:12]
    )

    assessment = _assessment_summary(assessment_model)
    mastery = _goal_mastery(goal)
    trend = _goal_trend(goal)
    events = [
        {
            "id": row.id,
            "source": row.source,
            "source_label": _label(row.source),
            "concept_key": row.concept_key,
            "concept_label": _label(row.concept_key),
            "question_text": row.question_text,
            "student_answer": row.student_answer,
            "accuracy_score": _pct(row.accuracy_score),
            "confidence": _pct(row.confidence),
            "evidence": row.evidence,
            "dimension_scores": _dimension_rows(row.dimension_scores or {}),
            "grader_labels": _strategy_items(row.grader_labels or {}),
            "created_at": _iso(row.created_at),
        }
        for row in event_rows
    ]
    probes = [
        {
            "id": row.id,
            "concept_key": row.concept_key,
            "concept_label": _label(row.concept_key),
            "target_dimension": row.target_dimension,
            "target_label": _label(row.target_dimension),
            "question_text": row.question_text,
            "status": row.status,
            "status_label": _label(row.status),
            "student_answer": row.student_answer,
            "created_at": _iso(row.created_at),
            "completed_at": _iso(row.completed_at),
        }
        for row in probe_rows
    ]
    recent_chat = [
        {
            "id": row.id,
            "question": row.question,
            "answer": row.answer,
            "answer_style": row.answer_style,
            "answer_style_label": _label(row.answer_style),
            "processing_status": row.processing_status,
            "timestamp": _iso(row.timestamp),
        }
        for row in chat_rows
    ]
    timeline = []
    for row in event_rows:
        timeline.append(
            {
                "type": "event",
                "icon": "bi-activity",
                "label": _label(row.source),
                "title": row.question_text or f"{_label(row.concept_key)} evidence updated",
                "meta": f"Accuracy {_pct(row.accuracy_score)}% · Confidence {_pct(row.confidence)}%",
                "at": _iso(row.created_at),
                "sort_at": row.created_at,
            }
        )
    for row in chat_rows:
        timeline.append(
            {
                "type": "chat",
                "icon": "bi-chat-square-text",
                "label": "Learning conversation",
                "title": row.question,
                "meta": _label(row.answer_style),
                "at": _iso(row.timestamp),
                "sort_at": row.timestamp,
            }
        )
    for row in probe_rows:
        timeline.append(
            {
                "type": "probe",
                "icon": "bi-bullseye",
                "label": f"{_label(row.status)} probe",
                "title": row.question_text,
                "meta": _label(row.target_dimension),
                "at": _iso(row.completed_at or row.created_at),
                "sort_at": row.completed_at or row.created_at,
            }
        )
    timeline.sort(key=lambda item: item["sort_at"], reverse=True)
    for item in timeline:
        item.pop("sort_at", None)

    chat_count = visible_history_queryset().filter(learning_goal=goal).count()
    probe_count = AdaptiveProbe.objects.filter(learning_goal=goal).count()
    analysis = _goal_analysis(
        assessment=assessment,
        mastery=mastery,
        chat_count=chat_count,
        probes=probes,
    )
    evidence_total = chat_count + trend["event_count"] + probe_count + (1 if assessment["exists"] else 0)
    last_candidates = [
        goal.updated_at,
        assessment_model.created_at if assessment_model else None,
        event_rows[0].created_at if event_rows else None,
        probe_rows[0].updated_at if probe_rows else None,
        chat_rows[0].timestamp if chat_rows else None,
    ]
    last_activity = max(value for value in last_candidates if value is not None)
    return {
        "id": goal.id,
        "title": goal.title or goal.preference_text[:80],
        "preference_text": goal.preference_text,
        "domain": goal.domain,
        "branch": goal.branch,
        "status": goal.status,
        "status_label": _label(goal.status),
        "created_at": _iso(goal.created_at),
        "updated_at": _iso(goal.updated_at),
        "last_activity_at": _iso(last_activity),
        "chat_count": chat_count,
        "event_count": trend["event_count"],
        "probe_count": probe_count,
        "evidence_total": evidence_total,
        "answered_probe_count": sum(1 for probe in probes if probe["status"] == AdaptiveProbe.STATUS_ANSWERED),
        "pending_probe_count": sum(1 for probe in probes if probe["status"] == AdaptiveProbe.STATUS_PENDING),
        "assessment": assessment,
        "mastery": mastery,
        "trend": trend,
        "analysis": analysis,
        "events": events,
        "probes": probes,
        "concept_signals": [
            {
                "concept_label": row["concept_label"] or _label(row["concept_key"]),
                "concept_key": row["concept_key"],
                "count": row["count"],
                "confidence": _pct(row["confidence"]),
                "last_seen": _iso(row["last_seen"]),
            }
            for row in concept_signals
        ],
        "recent_chat": recent_chat,
        "timeline": timeline[:16],
    }


def _teaching_recommendations(goals: list[dict[str, Any]]) -> list[dict[str, str]]:
    recommendations: list[dict[str, str]] = []
    priority_goals = [goal for goal in goals if goal["analysis"]["status"]["key"] == "priority"]
    missing_assessments = [goal for goal in goals if not goal["assessment"]["exists"]]
    pending_probes = sum(goal["pending_probe_count"] for goal in goals)
    if priority_goals:
        recommendations.append(
            {
                "tone": "priority",
                "icon": "bi-exclamation-diamond",
                "title": f"Review {len(priority_goals)} priority goal{'s' if len(priority_goals) != 1 else ''}",
                "body": "Start with the lowest-quality goal and use its weakest dimension to plan the intervention.",
            }
        )
    if missing_assessments:
        recommendations.append(
            {
                "tone": "monitor",
                "icon": "bi-clipboard2-pulse",
                "title": f"Complete {len(missing_assessments)} missing baseline{'s' if len(missing_assessments) != 1 else ''}",
                "body": "A self-assessment baseline will make later mastery evidence easier to interpret.",
            }
        )
    if pending_probes:
        recommendations.append(
            {
                "tone": "info",
                "icon": "bi-bullseye",
                "title": f"{pending_probes} diagnostic probe{'s' if pending_probes != 1 else ''} pending",
                "body": "Revisit the report after these probes are answered to compare the new evidence.",
            }
        )
    if not recommendations:
        recommendations.append(
            {
                "tone": "success",
                "icon": "bi-check2-circle",
                "title": "Learning evidence is up to date",
                "body": "Continue monitoring the next conversation and probe before changing the current plan.",
            }
        )
    return recommendations


def _student_summary(student: UserProfile, goals: list[dict[str, Any]]) -> dict[str, Any]:
    all_mastery = LearnerMasteryState.objects.filter(user=student).aggregate(
        quality=Avg("quality_score"),
        facts=Avg("facts_mastery"),
        procedures=Avg("procedures_mastery"),
        strategies=Avg("strategies_mastery"),
        rationales=Avg("rationales_mastery"),
    )
    recent_activity = visible_history_queryset().filter(user=student).aggregate(last_chat=Max("timestamp"))
    latest_event = AdaptiveInteractionEvent.objects.filter(user=student).aggregate(last_event=Max("created_at"))
    last_candidates = [
        _iso(recent_activity["last_chat"]),
        _iso(latest_event["last_event"]),
        max((goal["last_activity_at"] for goal in goals), default=""),
    ]
    return {
        "learning_goal_count": len(goals),
        "assessment_count": SelfAssessment.objects.filter(username=student.username).count(),
        "chat_count": visible_history_queryset().filter(user=student).count(),
        "pending_probe_count": AdaptiveProbe.objects.filter(user=student, status=AdaptiveProbe.STATUS_PENDING).count(),
        "answered_probe_count": AdaptiveProbe.objects.filter(user=student, status=AdaptiveProbe.STATUS_ANSWERED).count(),
        "priority_goal_count": sum(1 for goal in goals if goal["analysis"]["status"]["key"] == "priority"),
        "monitor_goal_count": sum(1 for goal in goals if goal["analysis"]["status"]["key"] == "monitor"),
        "last_chat_at": _iso(recent_activity["last_chat"]),
        "last_adaptive_event_at": _iso(latest_event["last_event"]),
        "last_activity_at": max((value for value in last_candidates if value), default=""),
        "overall_quality_score": _pct(all_mastery["quality"]),
        "quality_state": _score_state(_pct(all_mastery["quality"])),
        "dimension_average": {
            "facts": _pct(all_mastery["facts"]),
            "procedures": _pct(all_mastery["procedures"]),
            "strategies": _pct(all_mastery["strategies"]),
            "rationales": _pct(all_mastery["rationales"]),
        },
        "dimension_rows": _dimension_rows(
            {
                "facts": _pct(all_mastery["facts"]),
                "procedures": _pct(all_mastery["procedures"]),
                "strategies": _pct(all_mastery["strategies"]),
                "rationales": _pct(all_mastery["rationales"]),
            }
        ),
    }


def _student_payload(student: UserProfile, membership: ClassroomMembership) -> dict[str, Any]:
    return {
        "student": {
            "id": student.user_id,
            "username": student.username,
            "email": student.email,
            "age": student.age,
            "academic_level": student.academic_level,
            "created_at": _iso(student.created_at),
        },
        "classroom": {
            "id": membership.classroom_id,
            "name": membership.classroom.name,
            "subject": membership.classroom.subject,
            "term": membership.classroom.term,
            "accepted_at": _iso(membership.accepted_at),
        },
    }


def build_student_report(teacher: UserProfile, classroom_id: int, student_id: int) -> dict[str, Any]:
    membership = get_student_membership_for_teacher(teacher, classroom_id, student_id)
    student = membership.student
    goal_models = list(LearningGoal.objects.filter(user=student).order_by("-updated_at", "-created_at"))
    goals = [_goal_report(goal) for goal in goal_models]
    return {
        **_student_payload(student, membership),
        "summary": _student_summary(student, goals),
        "goals": goals,
        "recommendations": _teaching_recommendations(goals),
    }


def build_student_goal_report(
    teacher: UserProfile,
    classroom_id: int,
    student_id: int,
    learning_goal_id: int,
) -> dict[str, Any]:
    membership = get_student_membership_for_teacher(teacher, classroom_id, student_id)
    student = membership.student
    goal_models = list(LearningGoal.objects.filter(user=student).order_by("-updated_at", "-created_at"))
    try:
        selected_model = next(goal for goal in goal_models if goal.id == learning_goal_id)
    except StopIteration as exc:
        raise Http404("Learning goal not found for this student.") from exc

    selected_goal = _goal_report(selected_model)
    goal_navigation = []
    for goal in goal_models:
        if goal.id == selected_model.id:
            report = selected_goal
        else:
            report = _goal_report(goal)
        goal_navigation.append(
            {
                "id": report["id"],
                "title": report["title"],
                "domain": report["domain"],
                "branch": report["branch"],
                "quality_score": report["mastery"]["quality_score"],
                "status": report["analysis"]["status"],
                "last_activity_at": report["last_activity_at"],
                "is_selected": report["id"] == selected_model.id,
            }
        )
    return {
        **_student_payload(student, membership),
        "goal": selected_goal,
        "goal_navigation": goal_navigation,
        "goal_count": len(goal_navigation),
    }
