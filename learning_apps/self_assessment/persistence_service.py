from __future__ import annotations

from dataclasses import replace
import logging
from typing import Any, Dict, Mapping

from django.db import transaction
from django.conf import settings

from learning_apps.adaptive_learning.perceived_state_service import project_perceived_state
from learning_apps.learning_goal.services import learning_goal_service
from learning_apps.persistence.models import ConceptRegistryEntry, LearningGoal

from .evidence_repository_service import (
    PersistedSelfAssessmentResult,
    accepted_decision_is_current,
    persist_evaluation_with_evidence,
)


class SelfAssessmentProjectionError(RuntimeError):
    """Raised when an accepted assessment cannot establish perceived state."""


logger = logging.getLogger(__name__)


def _create_calibration_offer(assessment_id: int) -> None:
    try:
        from learning_apps.adaptive_learning.probe_offer_service import (
            ensure_initial_calibration_offer_for_assessment,
        )

        ensure_initial_calibration_offer_for_assessment(assessment_id)
    except Exception:
        logger.exception(
            "Optional initial calibration offer failed for assessment=%s",
            assessment_id,
        )


@transaction.atomic
def save_evaluation_record(
    username: str,
    learning_goal_id: int,
    evaluation: Dict[str, Any],
    *,
    assessment_payload: Mapping[str, Any],
    submission_snapshot: Mapping[str, Any] | None = None,
) -> PersistedSelfAssessmentResult:
    """Atomically persist admission, perceived guidance, and goal transition."""

    goal = (
        LearningGoal.objects.select_for_update()
        .select_related("user")
        .filter(id=int(learning_goal_id), user__username=str(username))
        .first()
    )
    if not goal:
        raise SelfAssessmentProjectionError("scope_not_found")
    # Hold both the taxonomy parent and all existing registry rows until the
    # assessment/projection/completion transaction commits.  The parent lock also
    # serializes FK-backed registry inserts; row locks serialize updates.
    list(
        ConceptRegistryEntry.objects.select_for_update()
        .filter(user=goal.user, learning_goal=goal)
        .order_by("id")
        .values_list("id", flat=True)
    )
    evaluation = dict(evaluation)
    evaluation["submission_snapshot"] = dict(submission_snapshot or {})
    result = persist_evaluation_with_evidence(
        username=username,
        learning_goal_id=int(learning_goal_id),
        assessment_payload=assessment_payload,
        evaluation=evaluation,
    )
    if result.accepted:
        perceived = project_perceived_state(
            assessment=result.assessment,
            decision=result.decision,
            assessment_payload=assessment_payload,
        )
        # Catch taxonomy/lifecycle changes while the projection is being
        # committed. Raising rolls back assessment, decision, projection and
        # goal transition without ever creating observed mastery.
        if not accepted_decision_is_current(result.assessment, lock=True):
            raise SelfAssessmentProjectionError("perceived_state_evidence_changed")
        if not mark_goal_completed(username, learning_goal_id):
            raise SelfAssessmentProjectionError("goal_completion_failed")
        if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED:
            transaction.on_commit(
                lambda assessment_id=result.assessment.id: _create_calibration_offer(
                    assessment_id
                )
            )
        return replace(
            result,
            perceived_state_id=perceived.id,
            perceived_state_persisted=True,
            goal_completed=True,
        )
    return result


def mark_goal_completed(username: str, learning_goal_id: int | None) -> bool:
    """Mark an admitted assessment's goal completed."""
    if not learning_goal_id:
        return False
    return learning_goal_service.update_learning_goal_status(
        username=username,
        learning_goal_id=learning_goal_id,
        status="self_assessment_completed",
    )
