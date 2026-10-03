from __future__ import annotations

from typing import Any

from learning_apps.application.learning_goals.flow import OpenLearningGoalResult

from .base import ProductWorkflowError, execute_capability


def get_learning_goal(username: str, learning_goal_id: int) -> dict[str, Any] | None:
    try:
        payload = execute_capability(
            "learning.get_goal_record",
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="learning.goal.read",
        )
    except ProductWorkflowError as exc:
        if exc.code == "goal_scope_fenced":
            return None
        raise
    return payload.get("goal") or None


def list_user_learning_goals(username: str) -> list[dict[str, Any]]:
    return list(
        execute_capability("learning.list_goals", username=username, workflow="learning.goals.list").get(
            "learning_goals", []
        )
    )


def start_learning_goal_creation_job(username: str, raw_preference_text: str) -> str:
    return str(
        execute_capability(
            "learning.goal_creation.start",
            {"preference": raw_preference_text},
            username=username,
            workflow="learning.goal.create",
        )["job_id"]
    )


def get_learning_goal_creation_job(job_id: str, username: str) -> dict[str, Any] | None:
    return execute_capability(
        "learning.goal_creation.status",
        {"job_id": job_id},
        username=username,
        workflow="learning.goal.creation_status",
    ).get("job")


def resolve_open_learning_goal(username: str, learning_goal_id: int) -> OpenLearningGoalResult:
    try:
        payload = execute_capability(
            "learning.open_goal",
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="learning.goal.open",
        )
    except ProductWorkflowError as exc:
        if exc.code == "goal_scope_fenced":
            return OpenLearningGoalResult(ok=False, code="goal_not_found")
        raise
    return OpenLearningGoalResult(**payload)
