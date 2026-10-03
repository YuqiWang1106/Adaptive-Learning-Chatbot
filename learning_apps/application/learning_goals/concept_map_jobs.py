from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Tuple

from django.core.cache import cache
from django.db import close_old_connections

from learning_apps.infrastructure.services.task_dispatcher import dispatch_task_by_name
from learning_apps.persistence import job_repository as ai_job_service
from learning_apps.persistence.models import LearningGoalConceptMap
from learning_apps.persistence.models import AIJob
from learning_apps.learning_goal.services.concept_map_service import (
    generate_learning_goal_concept_map,
    set_learning_goal_concept_map_status,
)


logger = logging.getLogger(__name__)

JOB_TIMEOUT_SECONDS = 30 * 60
JOB_KEY_PREFIX = "learning_goal_concept_map_job"
GOAL_JOB_KEY_PREFIX = "learning_goal_concept_map_goal_job"
TASK_TYPE = "concept_map_generation"


def _job_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}:{job_id}"


def _goal_job_key(username: str, learning_goal_id: int) -> str:
    return f"{GOAL_JOB_KEY_PREFIX}:{username}:{learning_goal_id}"


def _build_payload(
    *,
    username: str,
    learning_goal_id: int,
    state: str,
    stage: str,
    percent: int,
    message: str,
    error: str = "",
) -> Dict[str, Any]:
    return {
        "username": username,
        "learning_goal_id": int(learning_goal_id),
        "state": state,
        "stage": stage,
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "error": error,
    }


def _set_job(job_id: str, payload: Dict[str, Any]) -> None:
    cache.set(_job_key(job_id), payload, timeout=JOB_TIMEOUT_SECONDS)
    state = payload.get("state")
    status = {
        "complete": AIJob.STATUS_SUCCEEDED,
        "failed": AIJob.STATUS_DEGRADED,
        "expired": AIJob.STATUS_FAILED,
    }.get(str(state), state)
    ai_job_service.update_job(
        job_id,
        status=status,
        stage=payload.get("stage"),
        percent=payload.get("percent"),
        message=payload.get("message"),
        result=payload,
        error_code=payload.get("error") or "",
    )


def _set_goal_job(username: str, learning_goal_id: int, job_id: str) -> None:
    cache.set(_goal_job_key(username, learning_goal_id), job_id, timeout=JOB_TIMEOUT_SECONDS)


def _clear_goal_job(username: str, learning_goal_id: int) -> None:
    cache.delete(_goal_job_key(username, learning_goal_id))


def get_active_learning_goal_concept_map_job(username: str, learning_goal_id: int) -> Dict[str, Any] | None:
    job_id = cache.get(_goal_job_key(username, learning_goal_id))
    if not job_id:
        return None

    payload = cache.get(_job_key(job_id))
    if not payload or payload.get("username") != username:
        job = ai_job_service.get_job(job_id, username=username)
        if not job:
            _clear_goal_job(username, learning_goal_id)
            return None
        payload = dict(job.result or {})
        if not payload:
            payload = ai_job_service.job_to_payload(job)
            payload["state"] = (
                "complete"
                if job.status == AIJob.STATUS_SUCCEEDED
                else "failed"
                if job.status in {AIJob.STATUS_FAILED, AIJob.STATUS_DEGRADED}
                else job.status
            )

    if payload.get("state") != "running":
        _clear_goal_job(username, learning_goal_id)
        return None

    result = dict(payload)
    result["job_id"] = job_id
    return result


def start_learning_goal_concept_map_job(
    username: str,
    learning_goal_id: int,
    *,
    force_refresh: bool = False,
) -> Tuple[str, Dict[str, Any]]:
    existing = get_active_learning_goal_concept_map_job(username, learning_goal_id)
    if existing:
        return str(existing["job_id"]), existing

    job_id = uuid.uuid4().hex
    job = ai_job_service.create_job(
        task_type=TASK_TYPE,
        username=username,
        job_id=job_id,
        learning_goal_id=learning_goal_id,
        idempotency_key=f"{username}:{learning_goal_id}:{int(bool(force_refresh))}",
        stage="queued",
        percent=4,
        message="Starting concept map generation.",
    )
    job_id = job.job_id
    initial_payload = _build_payload(
        username=username,
        learning_goal_id=learning_goal_id,
        state="running",
        stage="queued",
        percent=4,
        message="Starting concept map generation.",
    )
    _set_job(job_id, initial_payload)
    _set_goal_job(username, learning_goal_id, job_id)

    try:
        set_learning_goal_concept_map_status(
            username,
            learning_goal_id,
            status=LearningGoalConceptMap.STATUS_PENDING,
            error_code="",
            error_message="",
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Could not mark concept map as pending for %s/%s: %s", username, learning_goal_id, exc)

    dispatch_task_by_name(
        "learning_goal.run_concept_map_job",
        job_id,
        username,
        learning_goal_id,
        force_refresh,
        fallback=lambda: _run_learning_goal_concept_map_job(job_id, username, learning_goal_id, force_refresh),
    )
    payload = dict(initial_payload)
    payload["job_id"] = job_id
    return job_id, payload


def _run_learning_goal_concept_map_job(job_id: str, username: str, learning_goal_id: int, force_refresh: bool) -> None:
    close_old_connections()
    ai_job_service.update_job(
        job_id,
        status=AIJob.STATUS_RUNNING,
        stage="running",
        percent=8,
        message="Running concept map generation.",
    )

    def update(stage: str, percent: int, message: str) -> None:
        _set_job(
            job_id,
            _build_payload(
                username=username,
                learning_goal_id=learning_goal_id,
                state="running",
                stage=stage,
                percent=percent,
                message=message,
            ),
        )

    try:
        result = generate_learning_goal_concept_map(
            username=username,
            learning_goal_id=learning_goal_id,
            progress_callback=update,
            force_refresh=force_refresh,
        )
        if result.ok:
            _set_job(
                job_id,
                _build_payload(
                    username=username,
                    learning_goal_id=learning_goal_id,
                    state="complete",
                    stage="ready",
                    percent=100,
                    message="Concept map ready.",
                ),
            )
            return

        _set_job(
            job_id,
            _build_payload(
                username=username,
                learning_goal_id=learning_goal_id,
                state="failed",
                stage=result.code or "failed",
                percent=100,
                message="Learning Workflow Demo could not finish this concept map yet.",
                error=result.code or "failed",
            ),
        )
    except Exception as exc:
        logger.exception("Concept map job %s failed for user %s goal %s: %s", job_id, username, learning_goal_id, exc)
        set_learning_goal_concept_map_status(
            username,
            learning_goal_id,
            status=LearningGoalConceptMap.STATUS_FAILED,
            error_code="unexpected_error",
            error_message="Learning Workflow Demo could not finish this concept map yet.",
        )
        _set_job(
            job_id,
            _build_payload(
                username=username,
                learning_goal_id=learning_goal_id,
                state="failed",
                stage="failed",
                percent=100,
                message="Learning Workflow Demo could not finish this concept map yet.",
                error="unexpected_error",
            ),
        )
    finally:
        _clear_goal_job(username, learning_goal_id)
        close_old_connections()


__all__ = [
    "get_active_learning_goal_concept_map_job",
    "start_learning_goal_concept_map_job",
]
