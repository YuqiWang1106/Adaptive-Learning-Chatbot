from __future__ import annotations

import logging

from django.db import close_old_connections

from learning_apps.infrastructure.services.task_dispatcher import dispatch_task_by_name

from learning_apps.learning_goal.services import learning_goal_service
from learning_apps.learning_goal.title_refinement_service import refine_goal_title_with_llm


logger = logging.getLogger(__name__)


def _emit(message: str) -> None:
    try:
        print(message, flush=True)
    except Exception:
        logger.warning(message)


def start_learning_goal_title_refine_job(username: str, learning_goal_id: int) -> None:
    _emit(f"[GoalTitleJob] queued user={username} goal_id={learning_goal_id}")
    dispatch_task_by_name(
        "learning_goal.run_title_refine_job",
        username,
        int(learning_goal_id),
        fallback=lambda: _run_learning_goal_title_refine_job(username, int(learning_goal_id)),
    )


def _run_learning_goal_title_refine_job(username: str, learning_goal_id: int) -> None:
    _emit(f"[GoalTitleJob] started user={username} goal_id={learning_goal_id}")
    close_old_connections()
    try:
        goal = learning_goal_service.get_learning_goal(username, learning_goal_id)
        if not goal:
            _emit(f"[GoalTitleJob] skipped_not_found user={username} goal_id={learning_goal_id}")
            return

        current_title = str(goal.get("title") or "").strip()
        fallback_title = learning_goal_service.build_local_goal_title(
            str(goal.get("preference_text") or ""),
            str(goal.get("domain") or ""),
            str(goal.get("branch") or ""),
        )
        if not current_title:
            current_title = fallback_title

        refined_title = refine_goal_title_with_llm(
            preference_text=str(goal.get("preference_text") or ""),
            domain=str(goal.get("domain") or ""),
            branch=str(goal.get("branch") or ""),
            fallback_title=current_title,
        )
        if not refined_title:
            _emit(f"[GoalTitleJob] skipped_empty_refined user={username} goal_id={learning_goal_id}")
            return

        updated = learning_goal_service.set_goal_title(username, learning_goal_id, refined_title)
        _emit(
            f"[GoalTitleJob] finished user={username} goal_id={learning_goal_id} "
            f"updated={bool(updated)} title={refined_title}"
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("Goal title refine job failed for user=%s goal_id=%s: %s", username, learning_goal_id, exc)
        _emit(f"[GoalTitleJob] failed user={username} goal_id={learning_goal_id} error={exc}")
    finally:
        close_old_connections()
