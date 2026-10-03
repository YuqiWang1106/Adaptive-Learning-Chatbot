from __future__ import annotations

import logging
import uuid
from types import SimpleNamespace
from typing import Any, Dict

from django.core.cache import cache
from django.conf import settings
from django.db import close_old_connections
from django.urls import reverse

from learning_apps.infrastructure.services.task_dispatcher import dispatch_task_by_name
from learning_apps.persistence import job_repository as ai_job_service
from learning_apps.persistence.models import AIJob

from learning_apps.self_assessment.flow_service import submit_self_assessment
from learning_apps.self_assessment.goal_context_service import GoalContextResult

logger = logging.getLogger(__name__)

JOB_TIMEOUT_SECONDS = 30 * 60
JOB_KEY_PREFIX = "self_assessment_submission_job"
TASK_TYPE = "self_assessment_submission"


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


def get_self_assessment_submission_job(job_id: str, username: str) -> Dict[str, Any] | None:
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


def start_self_assessment_submission_job(
    *,
    username: str,
    post_data: Dict[str, str],
    goal_context: GoalContextResult,
) -> str:
    job_id = uuid.uuid4().hex
    _emit(
        f"[SelfAssessmentJob] queued job_id={job_id} user={username} "
        f"goal_id={goal_context.learning_goal_id or ''}"
    )
    ai_job_service.create_job(
        task_type=TASK_TYPE,
        username=username,
        job_id=job_id,
        learning_goal_id=goal_context.learning_goal_id,
        idempotency_key=job_id,
        stage="queued",
        percent=4,
        message="Preparing your assessment.",
    )
    _set_job(
        job_id,
        _build_payload(
            username=username,
            state="running",
            stage="queued",
            percent=4,
            message="Preparing your assessment.",
            learning_goal_id=goal_context.learning_goal_id,
        ),
    )

    dispatch_task_by_name(
        "self_assessment.run_submission_job",
        job_id,
        username,
        post_data,
        goal_context.__dict__,
        fallback=lambda: _run_self_assessment_submission_job(job_id, username, post_data, goal_context),
    )
    return job_id


def _run_self_assessment_submission_job(
    job_id: str,
    username: str,
    post_data: Dict[str, str],
    goal_context: GoalContextResult,
) -> None:
    _emit(
        f"[SelfAssessmentJob] started job_id={job_id} user={username} "
        f"goal_id={goal_context.learning_goal_id or ''}"
    )
    close_old_connections()
    ai_job_service.update_job(
        job_id,
        status=AIJob.STATUS_RUNNING,
        stage="running",
        percent=8,
        message="Running self-assessment evaluation.",
    )

    def update(stage: str, percent: int, message: str) -> None:
        _emit(
            f"[SelfAssessmentJob] progress job_id={job_id} user={username} "
            f"goal_id={goal_context.learning_goal_id or ''} stage={stage} percent={percent} message={message}"
        )
        _set_job(
            job_id,
            _build_payload(
                username=username,
                state="running",
                stage=stage,
                percent=percent,
                message=message,
                learning_goal_id=goal_context.learning_goal_id,
            ),
        )

    try:
        update("reading_response", 16, "Reading your self-assessment.")
        request_like = SimpleNamespace(POST=post_data)
        result = submit_self_assessment(
            request_like,
            username,
            goal_context,
            progress=update,
        )

        if result.ok or (result.needs_revision and result.assessment_id):
            update("saving", 88, "Saving your assessment.")
            if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED:
                redirect_url = reverse(
                    "self_assessment_result",
                    kwargs={"assessment_id": result.assessment_id},
                )
            else:
                target = reverse("chat")
                redirect_url = (
                    f"{target}?learning_goal_id={result.learning_goal_id}&from_loading=1"
                    if result.learning_goal_id
                    else target
                )
            _set_job(
                job_id,
                _build_payload(
                    username=username,
                    state="complete",
                    stage="ready",
                    percent=100,
                    message=(
                        "Your assessment is ready."
                        if result.ok
                        else "Your assessment needs a quick revision."
                    ),
                    learning_goal_id=result.learning_goal_id,
                    redirect_url=redirect_url,
                ),
            )
            _emit(
                f"[SelfAssessmentJob] completed job_id={job_id} user={username} "
                f"goal_id={result.learning_goal_id or goal_context.learning_goal_id or ''}"
            )
            return

        _set_job(
            job_id,
            _build_payload(
                username=username,
                state="failed",
                stage=result.code or "failed",
                percent=100,
                message="Learning Workflow Demo could not evaluate this assessment. Please try again.",
                learning_goal_id=goal_context.learning_goal_id,
                error=result.code or "failed",
            ),
        )
        _emit(
            f"[SelfAssessmentJob] failed job_id={job_id} user={username} "
            f"goal_id={goal_context.learning_goal_id or ''} code={result.code or 'failed'}"
        )
    except Exception as exc:
        logger.exception("Self-assessment job %s failed for user %s: %s", job_id, username, exc)
        _emit(
            f"[SelfAssessmentJob] failed job_id={job_id} user={username} "
            f"goal_id={goal_context.learning_goal_id or ''} code=unexpected_error error={exc}"
        )
        _set_job(
            job_id,
            _build_payload(
                username=username,
                state="failed",
                stage="failed",
                percent=100,
                message="Learning Workflow Demo could not finish this assessment. Please try again.",
                learning_goal_id=goal_context.learning_goal_id,
                error="unexpected_error",
            ),
        )
    finally:
        close_old_connections()
        _emit(
            f"[SelfAssessmentJob] finished job_id={job_id} user={username} "
            f"goal_id={goal_context.learning_goal_id or ''}"
        )
