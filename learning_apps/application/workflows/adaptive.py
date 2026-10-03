from __future__ import annotations

from typing import Any

from .base import ProductWorkflowError, execute_capability


def build_progress_payload(username: str, learning_goal_id: int) -> dict[str, Any]:
    try:
        return execute_capability(
            "adaptive.get_progress",
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="adaptive.progress",
        )
    except ProductWorkflowError as exc:
        if exc.code == "goal_scope_fenced":
            return {"ok": False, "error": "learning_goal_not_found"}
        raise


def submit_probe_answer(username: str, learning_goal_id: int, probe_id: int, answer: Any) -> dict[str, Any]:
    try:
        return execute_capability(
            "probe.submit_answer",
            {"probe_id": probe_id, "answer": answer},
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="probe.submit",
        )
    except ProductWorkflowError as exc:
        if exc.code == "goal_scope_fenced":
            return {"ok": False, "error": "learning_goal_not_found"}
        raise


def skip_probe(username: str, learning_goal_id: int, probe_id: int) -> dict[str, Any]:
    try:
        return execute_capability(
            "probe.skip",
            {"probe_id": probe_id},
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="probe.skip",
        )
    except ProductWorkflowError as exc:
        if exc.code == "goal_scope_fenced":
            return {"ok": False, "error": "learning_goal_not_found"}
        raise
