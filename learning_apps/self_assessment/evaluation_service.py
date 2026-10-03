from __future__ import annotations

from typing import Any, Callable, Dict, List

from django.conf import settings

from learning_apps.self_assessment.services.self_assessment_evaluation_service import evaluate_self_assessment


def build_assessment_payload(
    domain: str,
    branch: str,
    preference: str,
    problem: str,
    knowledge_types: List[Dict[str, str]],
    learning_goal_id: int | None = None,
    username: str = "",
    *,
    context: str = "",
    evidence_sufficiency: Dict[str, str] | None = None,
    reference_blueprint_id: int | None = None,
    reference_blueprint_sha256: str = "",
    target_task_sha256: str = "",
    scope_confirmed: bool = False,
) -> Dict[str, Any]:
    """Build assessment payload."""
    return {
        "preference_meta": {
            "domain": domain,
            "branch": branch,
            "preference": preference,
            "learning_goal_id": learning_goal_id,
            "username": username,
        },
        "self_assessment": {
            "problem": problem,
            "context": context or problem,
            "target_task": context or problem,
            "target_task_sha256": target_task_sha256,
            "reference_blueprint_id": reference_blueprint_id,
            "reference_blueprint_sha256": reference_blueprint_sha256,
            "scope_confirmed": bool(scope_confirmed),
            "knowledge_types": knowledge_types,
            "evidence_sufficiency": evidence_sufficiency or {},
        },
    }


def run_self_assessment_evaluation(
    student_id: str,
    assessment_payload: Dict[str, Any],
    *,
    progress: Callable[[str, int, str], None] | None = None,
) -> Dict[str, Any]:
    """Run self assessment evaluation."""
    if not settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED:
        from learning_apps.self_assessment.services.legacy_self_assessment_evaluation_service import (
            evaluate_self_assessment as evaluate_v1,
        )

        return evaluate_v1(student_id, assessment_payload)
    return evaluate_self_assessment(
        student_id,
        assessment_payload,
        progress=progress,
    )
