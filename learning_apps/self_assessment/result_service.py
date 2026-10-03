from __future__ import annotations

from typing import Any, Mapping

from django.http import Http404

from learning_apps.adaptive_learning.probe_offer_service import (
    active_probe_offer,
    serialize_probe_offer,
)
from learning_apps.persistence.models import (
    CSAReferenceBlueprint,
    LearnerPerceivedState,
    SelfAssessment,
)

from .submission_validation_service import DIMENSIONS, snapshot_to_post_data
from .evidence_admission_service import stable_sha256


DIMENSION_LABELS = {
    "facts": "Facts",
    "strategies": "Strategies",
    "procedures": "Procedures",
    "rationales": "Rationales",
}
BAND_LABELS = {
    "reports_high_support_need": "A useful place to start",
    "reports_moderate_support_need": "Developing confidence",
    "reports_relative_confidence": "Reports relative confidence",
    "reports_not_started": "Reports not started",
    "evidence_thin": "Not enough detail yet",
}
SUFFICIENCY_LABELS = {
    "usable": "Usable self-report",
    "thin": "Thin self-report",
    "explicit_not_started": "Explicitly not started",
    "insufficient": "Insufficient detail",
}


def get_owned_assessment(username: str, assessment_id: int) -> SelfAssessment:
    assessment = (
        SelfAssessment.objects.select_related(
            "learning_goal",
            "learning_goal__user",
            "evidence_decision",
        )
        .filter(
            id=int(assessment_id),
            username=str(username),
            learning_goal__user__username=str(username),
        )
        .first()
    )
    if not assessment or not assessment.learning_goal_id:
        raise Http404("Self-assessment not found.")
    return assessment


def revision_form_values(
    username: str,
    assessment_id: int,
    *,
    learning_goal_id: int,
) -> dict[str, str]:
    assessment = get_owned_assessment(username, assessment_id)
    if assessment.learning_goal_id != int(learning_goal_id):
        raise Http404("Self-assessment does not belong to this learning goal.")
    return snapshot_to_post_data(
        assessment.submission_snapshot or {},
        learning_goal_id=assessment.learning_goal_id,
    )


def prepared_scope_context(
    username: str,
    learning_goal_id: int,
    form_values: Mapping[str, Any],
) -> dict[str, Any] | None:
    try:
        blueprint_id = int(form_values.get("reference_blueprint_id"))
    except (TypeError, ValueError):
        return None
    target_task = " ".join(
        str(
            form_values.get("target_task")
            or form_values.get("context")
            or ""
        ).split()
    )
    target_hash = stable_sha256({"target_task": target_task})
    blueprint = CSAReferenceBlueprint.objects.filter(
        id=blueprint_id,
        user__username=username,
        learning_goal_id=int(learning_goal_id),
        status=CSAReferenceBlueprint.STATUS_FROZEN,
    ).first()
    frozen_target = (
        blueprint.target_problem_snapshot
        if blueprint and isinstance(blueprint.target_problem_snapshot, Mapping)
        else {}
    )
    if (
        not blueprint
        or blueprint.blueprint_sha256
        != str(form_values.get("reference_blueprint_sha256") or "")
        or target_hash != str(form_values.get("target_task_sha256") or "")
        or frozen_target.get("sha256") != target_hash
        or frozen_target.get("raw") != target_task
        or not isinstance(blueprint.scope_contract, Mapping)
        or blueprint.scope_contract.get("status") != "ready"
    ):
        return None
    return {
        "target_task": target_task,
        "target_task_sha256": target_hash,
        "reference_blueprint_id": blueprint.id,
        "reference_blueprint_sha256": blueprint.blueprint_sha256,
        "scope_contract": dict(blueprint.scope_contract),
    }


def _student_words(snapshot: Mapping[str, Any], dimension: str) -> str:
    dimensions = snapshot.get("dimensions")
    item = dimensions.get(dimension) if isinstance(dimensions, Mapping) else {}
    if not isinstance(item, Mapping):
        return ""
    return "\n".join(
        text
        for text in (
            str(item.get("statement") or "").strip(),
            str(item.get("uncertainties") or "").strip(),
        )
        if text
    )


def build_result_context(username: str, assessment_id: int) -> dict[str, Any]:
    assessment = get_owned_assessment(username, assessment_id)
    metadata = assessment.diagnostic_metadata if isinstance(assessment.diagnostic_metadata, dict) else {}
    snapshot = assessment.submission_snapshot if isinstance(assessment.submission_snapshot, dict) else {}
    perceived = LearnerPerceivedState.objects.filter(
        source_assessment=assessment,
        user__username=username,
        learning_goal=assessment.learning_goal,
        mastery_write_authorized=False,
    ).first()
    summary = perceived.diagnostic_summary if perceived and isinstance(perceived.diagnostic_summary, dict) else {}
    report = assessment.structured_report if isinstance(assessment.structured_report, dict) else {}
    dimensions = []
    for dimension in DIMENSIONS:
        canonical = DIMENSION_LABELS[dimension]
        item = report.get(canonical) if isinstance(report.get(canonical), dict) else {}
        state = summary.get(dimension) if isinstance(summary.get(dimension), dict) else {}
        evidence_state = str(
            state.get("evidence_sufficiency")
            or (metadata.get("evidence_sufficiency") or {}).get(dimension)
            or "insufficient"
        )
        aspects = [
            {
                "name": str(aspect.get("aspect") or ""),
                "labels": list(aspect.get("labels") or []),
                "explanation": str(aspect.get("explanation") or ""),
            }
            for aspect in item.get("aspects") or []
            if isinstance(aspect, Mapping)
        ]
        dimensions.append(
            {
                "key": dimension,
                "label": canonical,
                "band": BAND_LABELS.get(str(state.get("band") or ""), "Needs revision"),
                "sufficiency": SUFFICIENCY_LABELS.get(evidence_state, "Insufficient detail"),
                "sufficiency_key": evidence_state,
                "student_words": _student_words(snapshot, dimension),
                "aspects": aspects,
                "next_step": str(item.get("most_critical_gap") or ""),
            }
        )
    decision = getattr(assessment, "evidence_decision", None)
    offer = active_probe_offer(username, assessment.learning_goal_id)
    return {
        "assessment": assessment,
        "goal": assessment.learning_goal,
        "goal_alignment": str(metadata.get("goal_alignment") or "unknown"),
        "goal_alignment_explanation": str(
            metadata.get("goal_alignment_explanation") or ""
        ),
        "response_language": str(metadata.get("response_language") or ""),
        "target_task": str(
            assessment.target_problem
            or snapshot.get("target_task")
            or snapshot.get("context")
            or ""
        ),
        "scope_contract": (
            dict(assessment.scope_contract)
            if isinstance(assessment.scope_contract, Mapping)
            else dict(metadata.get("scope_contract") or {})
        ),
        "dimensions": dimensions,
        "accepted": bool(decision and decision.status == decision.STATUS_ACCEPTED),
        "needs_revision": bool(decision and decision.status != decision.STATUS_ACCEPTED),
        "decision_reason": str(getattr(decision, "reason_code", "") or ""),
        "probe_offer": serialize_probe_offer(offer) if offer else None,
    }


__all__ = [
    "build_result_context",
    "get_owned_assessment",
    "prepared_scope_context",
    "revision_form_values",
]
