from __future__ import annotations

from dataclasses import asdict
from typing import Any

from learning_apps.self_assessment.goal_context_service import GoalContextResult
from learning_apps.self_assessment.result_service import (
    build_result_context,
    prepared_scope_context,
    revision_form_values,
)
from learning_apps.self_assessment.submission_validation_service import (
    SubmissionValidation,
    validate_self_assessment_submission,
)

from .base import execute_capability


def resolve_request_goal_context(request, username: str) -> GoalContextResult:
    raw_goal_id = request.GET.get("learning_goal_id")
    if request.method == "POST" and not raw_goal_id:
        raw_goal_id = request.POST.get("learning_goal_id")
    try:
        goal_id = int(raw_goal_id) if raw_goal_id else 0
    except (TypeError, ValueError):
        goal_id = 0
    payload = execute_capability(
        "assessment.resolve_context",
        {
            "domain": request.GET.get("domain", ""),
            "branch": request.GET.get("branch", ""),
            "preference": request.GET.get("preference", ""),
        },
        username=username,
        workflow="assessment.context",
        metadata={"requested_learning_goal_id": goal_id},
    )
    return GoalContextResult(**payload)


def build_self_assessment_template_context(username: str, goal_context: GoalContextResult) -> dict[str, Any]:
    return execute_capability(
        "assessment.page_context",
        {"goal_context": asdict(goal_context)},
        username=username,
        workflow="assessment.page",
    )


def start_self_assessment_submission_job(
    *, username: str, post_data: dict[str, Any], goal_context: GoalContextResult
) -> str:
    return str(
        execute_capability(
            "assessment.submission.start",
            {"post_data": post_data, "goal_context": asdict(goal_context)},
            username=username,
            workflow="assessment.submit",
        )["job_id"]
    )


def get_self_assessment_submission_job(job_id: str, username: str) -> dict[str, Any] | None:
    return execute_capability(
        "assessment.submission.status",
        {"job_id": job_id},
        username=username,
        workflow="assessment.status",
    ).get("job")


def start_target_scope_job(
    *,
    username: str,
    target_task: str,
    goal_context: GoalContextResult,
) -> str:
    return str(
        execute_capability(
            "assessment.target_scope.start",
            {
                "target_task": target_task,
                "goal_context": asdict(goal_context),
            },
            username=username,
            workflow="assessment.target_scope",
        )["job_id"]
    )


def get_target_scope_job(job_id: str, username: str) -> dict[str, Any] | None:
    return execute_capability(
        "assessment.target_scope.status",
        {"job_id": job_id},
        username=username,
        workflow="assessment.target_scope.status",
    ).get("job")


def validate_submission(post_data: dict[str, Any]) -> SubmissionValidation:
    return validate_self_assessment_submission(post_data)


def build_self_assessment_result_context(
    username: str,
    assessment_id: int,
) -> dict[str, Any]:
    return build_result_context(username, assessment_id)


def build_revision_form_values(
    username: str,
    assessment_id: int,
    *,
    learning_goal_id: int,
) -> dict[str, str]:
    return revision_form_values(
        username,
        assessment_id,
        learning_goal_id=learning_goal_id,
    )


def build_prepared_scope_context(
    username: str,
    learning_goal_id: int,
    form_values: dict[str, Any],
) -> dict[str, Any] | None:
    return prepared_scope_context(username, learning_goal_id, form_values)
