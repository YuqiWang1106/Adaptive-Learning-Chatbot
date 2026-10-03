from __future__ import annotations

import logging
import uuid
from typing import Any, Dict

from django.core.cache import cache
from django.db import close_old_connections
from django.urls import reverse

from learning_apps.infrastructure.services.task_dispatcher import dispatch_task_by_name
from learning_apps.persistence import job_repository as ai_job_service
from learning_apps.persistence.models import AIJob

from .flow import create_learning_goal_from_preference

logger = logging.getLogger(__name__)

JOB_TIMEOUT_SECONDS = 30 * 60
JOB_KEY_PREFIX = "learning_goal_creation_job"
TASK_TYPE = "learning_goal_creation"


def _emit(message: str) -> None:
    try:
        print(message, flush=True)
    except Exception:
        logger.warning(message)


def _job_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}:{job_id}"


def _build_payload(
    *,
    username: str,
    state: str,
    stage: str,
    percent: int,
    message: str,
    learning_goal_id: int | None = None,
    redirect_url: str = "",
    error: str = "",
) -> Dict[str, Any]:
    return {
        "username": username,
        "state": state,
        "stage": stage,
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "learning_goal_id": learning_goal_id,
        "redirect_url": redirect_url,
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


def get_learning_goal_creation_job(job_id: str, username: str) -> Dict[str, Any] | None:
    payload = cache.get(_job_key(job_id))
    if not payload or payload.get("username") != username:
        job = ai_job_service.get_job(job_id, username=username)
        if not job:
            return None
        stored = dict(job.result or {})
        if not stored:
            stored = ai_job_service.job_to_payload(job)
        stored.setdefault(
            "state",
            "complete" if job.status == AIJob.STATUS_SUCCEEDED else "failed" if job.status in {AIJob.STATUS_FAILED, AIJob.STATUS_DEGRADED} else job.status,
        )
        stored.setdefault("stage", job.stage)
        stored.setdefault("percent", job.percent)
        stored.setdefault("message", job.message)
        return stored
    return payload


def start_learning_goal_creation_job(username: str, raw_preference_text: str) -> str:
    job_id = uuid.uuid4().hex
    _emit(
        f"[LearningGoalJob] queued job_id={job_id} user={username} "
        f"preference_len={len((raw_preference_text or '').strip())}"
    )
    ai_job_service.create_job(
        task_type=TASK_TYPE,
        username=username,
        job_id=job_id,
        idempotency_key=job_id,
        stage="queued",
        percent=4,
        message="Starting your learning path.",
    )
    _set_job(
        job_id,
        _build_payload(
            username=username,
            state="running",
            stage="queued",
            percent=4,
            message="Starting your learning path.",
        ),
    )

    dispatch_task_by_name(
        "learning_goal.run_creation_job",
        job_id,
        username,
        raw_preference_text,
        fallback=lambda: _run_learning_goal_creation_job(job_id, username, raw_preference_text),
    )
    return job_id


def _run_learning_goal_creation_job(job_id: str, username: str, raw_preference_text: str) -> None:
    _emit(f"[LearningGoalJob] started job_id={job_id} user={username}")
    close_old_connections()
    ai_job_service.update_job(
        job_id,
        status=AIJob.STATUS_RUNNING,
        stage="running",
        percent=8,
        message="Running learning goal creation.",
    )

    def update(stage: str, percent: int, message: str) -> None:
        _emit(
            f"[LearningGoalJob] progress job_id={job_id} user={username} "
            f"stage={stage} percent={percent} message={message}"
        )
        _set_job(
            job_id,
            _build_payload(
                username=username,
                state="running",
                stage=stage,
                percent=percent,
                message=message,
            ),
        )

    try:
        result = create_learning_goal_from_preference(
            username=username,
            raw_preference_text=raw_preference_text,
            progress_callback=update,
        )

        if result.ok and result.learning_goal_id:
            _emit(
                f"[LearningGoalJob] created goal_id={result.learning_goal_id} "
                f"job_id={job_id} user={username}"
            )
            try:
                from .title_jobs import start_learning_goal_title_refine_job

                start_learning_goal_title_refine_job(username, result.learning_goal_id)
                _emit(
                    f"[LearningGoalJob] title_refine_enqueued goal_id={result.learning_goal_id} "
                    f"job_id={job_id} user={username}"
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Goal title refine could not start for %s goal %s: %s",
                    username,
                    result.learning_goal_id,
                    exc,
                )
                _emit(
                    f"[LearningGoalJob] title_refine_enqueue_failed goal_id={result.learning_goal_id} "
                    f"job_id={job_id} user={username} error={exc}"
                )
            try:
                from .concept_map_jobs import start_learning_goal_concept_map_job

                start_learning_goal_concept_map_job(username, result.learning_goal_id, force_refresh=False)
                _emit(
                    f"[LearningGoalJob] concept_map_enqueued goal_id={result.learning_goal_id} "
                    f"job_id={job_id} user={username}"
                )
            except Exception as exc:  # pragma: no cover - defensive
                logger.warning(
                    "Concept map background generation could not start for %s goal %s: %s",
                    username,
                    result.learning_goal_id,
                    exc,
                )
                _emit(
                    f"[LearningGoalJob] concept_map_enqueue_failed goal_id={result.learning_goal_id} "
                    f"job_id={job_id} user={username} error={exc}"
                )
            redirect_url = f"{reverse('self_assessment')}?learning_goal_id={result.learning_goal_id}&from_loading=1"
            _set_job(
                job_id,
                _build_payload(
                    username=username,
                    state="complete",
                    stage="ready",
                    percent=100,
                    message="Your self-assessment is ready.",
                    learning_goal_id=result.learning_goal_id,
                    redirect_url=redirect_url,
                ),
            )
            _emit(
                f"[LearningGoalJob] completed job_id={job_id} user={username} "
                f"goal_id={result.learning_goal_id}"
            )
            return

        message = "Could not create this learning goal. Please try again."
        if result.code == "empty_preference":
            message = "Please describe your learning goal in one sentence."
        _set_job(
            job_id,
            _build_payload(
                username=username,
                state="failed",
                stage=result.code or "failed",
                percent=100,
                message=message,
                error=result.code or "failed",
            ),
        )
        _emit(
            f"[LearningGoalJob] failed job_id={job_id} user={username} "
            f"code={result.code or 'failed'}"
        )
    except Exception as exc:
        logger.exception("Learning goal creation job %s failed for user %s: %s", job_id, username, exc)
        _emit(f"[LearningGoalJob] failed job_id={job_id} user={username} code=unexpected_error error={exc}")
        _set_job(
            job_id,
            _build_payload(
                username=username,
                state="failed",
                stage="failed",
                percent=100,
                message="Learning Workflow Demo saved your request and will keep preparing this learning path.",
                error="unexpected_error",
            ),
        )
    finally:
        close_old_connections()
        _emit(f"[LearningGoalJob] finished job_id={job_id} user={username}")
