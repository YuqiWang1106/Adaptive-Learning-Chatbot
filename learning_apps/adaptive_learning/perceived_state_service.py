from __future__ import annotations

from typing import Any, Mapping

from django.conf import settings
from django.db import transaction

from learning_apps.persistence.models import (
    LearnerPerceivedState,
    SelfAssessment,
    SelfAssessmentEvidenceDecision,
)

from .constants import DIMENSIONS
from .mastery_service import scores_from_structured_report


PERCEIVED_STATE_POLICY_VERSION = "perceived_state_v2"


def _clean(value: Any, limit: int = 500) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]


def _reported_uncertainties(assessment_payload: Mapping[str, Any]) -> dict[str, str]:
    self_assessment = assessment_payload.get("self_assessment")
    if not isinstance(self_assessment, Mapping):
        return {}
    result: dict[str, str] = {}
    knowledge_types = self_assessment.get("knowledge_types")
    if not isinstance(knowledge_types, list):
        return result
    for item in knowledge_types:
        if not isinstance(item, Mapping):
            continue
        dimension = str(item.get("type") or "").casefold().strip()
        if dimension not in DIMENSIONS:
            continue
        uncertainty = _clean(item.get("uncertainties"))
        if uncertainty:
            result[dimension] = uncertainty
    return result


def _band(score: float) -> str:
    if score < 0.45:
        return "reports_high_support_need"
    if score < 0.75:
        return "reports_moderate_support_need"
    return "reports_relative_confidence"


@transaction.atomic
def project_perceived_state(
    *,
    assessment: SelfAssessment,
    decision: SelfAssessmentEvidenceDecision,
    assessment_payload: Mapping[str, Any],
) -> LearnerPerceivedState:
    """Project an accepted self-assessment without touching observed mastery."""

    if decision.status != SelfAssessmentEvidenceDecision.STATUS_ACCEPTED:
        raise ValueError("perceived_state_requires_accepted_assessment")
    if decision.self_assessment_id != assessment.id:
        raise ValueError("perceived_state_decision_mismatch")
    if not assessment.learning_goal_id or decision.learning_goal_id != assessment.learning_goal_id:
        raise ValueError("perceived_state_goal_mismatch")
    if decision.user.username != assessment.username:
        raise ValueError("perceived_state_user_mismatch")

    metadata = assessment.diagnostic_metadata if isinstance(assessment.diagnostic_metadata, dict) else {}
    sufficiency = metadata.get("evidence_sufficiency")
    sufficiency = sufficiency if isinstance(sufficiency, dict) else {}
    scores = scores_from_structured_report(
        assessment.structured_report or {},
        evidence_sufficiency=(
            sufficiency if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED else None
        ),
    )
    dimension_scores = {dimension: round(float(scores.get(dimension, 0.0)), 4) for dimension in DIMENSIONS}
    uncertainties = _reported_uncertainties(assessment_payload)
    summary = {}
    for dimension in DIMENSIONS:
        evidence_state = str(sufficiency.get(dimension) or "insufficient")
        if not settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED:
            band = _band(dimension_scores[dimension])
        elif evidence_state == "explicit_not_started":
            band = "reports_not_started"
        elif evidence_state in {"thin", "insufficient"}:
            band = "evidence_thin"
        else:
            band = _band(dimension_scores[dimension])
        summary[dimension] = {
            "band": band,
            "evidence_sufficiency": (
                evidence_state
                if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED
                else "legacy_v1"
            ),
            "reported_uncertainty": dimension in uncertainties,
        }
    state, _created = LearnerPerceivedState.objects.update_or_create(
        learning_goal_id=assessment.learning_goal_id,
        defaults={
            "user_id": decision.user_id,
            "source_assessment": assessment,
            "evidence_decision": decision,
            "dimension_scores": dimension_scores,
            "reported_uncertainties": uncertainties,
            "diagnostic_summary": summary,
            "taxonomy_sha256": decision.taxonomy_sha256,
            "authority": LearnerPerceivedState.AUTHORITY_GUIDANCE_ONLY,
            "projection_version": PERCEIVED_STATE_POLICY_VERSION,
            "mastery_write_authorized": False,
        },
    )
    return state


__all__ = ["PERCEIVED_STATE_POLICY_VERSION", "project_perceived_state"]
