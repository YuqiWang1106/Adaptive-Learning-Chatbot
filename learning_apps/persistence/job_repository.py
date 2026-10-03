from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, Optional

from django.db import IntegrityError, transaction
from django.utils import timezone

from learning_apps.persistence.models import AIJob, LearningConversation, LearningGoal

logger = logging.getLogger(__name__)


TERMINAL_STATUSES = {
    AIJob.STATUS_SUCCEEDED,
    AIJob.STATUS_FAILED,
    AIJob.STATUS_DEGRADED,
}

_PRIVATE_RESULT_KEYS = {
    "conversation_id",
    "provider_conversation_id",
    "provider_file_id",
    "provider_id",
    "response_id",
    "vector_store_id",
}
_PRIVATE_VALUE_RE = re.compile(
    r"(?:\bsk-[A-Za-z0-9_-]{8,}|\b(?:conv|resp|response|file|vs|vector_store|thread|assistant)_[A-Za-z0-9_-]{6,})",
    re.IGNORECASE,
)


def _public_text(value: Any) -> str:
    return _PRIVATE_VALUE_RE.sub("[private-reference]", str(value or ""))


def _public_result(value):
    if isinstance(value, dict):
        return {
            key: _public_result(item)
            for key, item in value.items()
            if str(key).strip().casefold() not in _PRIVATE_RESULT_KEYS
            and not str(key).strip().casefold().startswith("provider_")
        }
    if isinstance(value, list):
        return [_public_result(item) for item in value]
    if isinstance(value, str):
        return _public_text(value)
    return value


def build_job_id() -> str:
    return uuid.uuid4().hex


def create_job(
    *,
    task_type: str,
    username: str,
    job_id: Optional[str] = None,
    learning_goal_id: Optional[int] = None,
    conversation_key: Optional[str] = None,
    idempotency_key: Optional[str] = None,
    stage: str = "queued",
    percent: int = 0,
    message: str = "Queued.",
    max_retries: int = 3,
) -> AIJob:
    job_id = job_id or build_job_id()
    idempotency_key = idempotency_key or job_id
    learning_goal = None
    if learning_goal_id:
        learning_goal = LearningGoal.objects.filter(id=int(learning_goal_id), user__username=username).first()
    conversation = None
    if conversation_key and learning_goal:
        conversation = LearningConversation.objects.filter(
            conversation_key=str(conversation_key),
            user=learning_goal.user,
            learning_goal=learning_goal,
            lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
        ).first()
        if not conversation:
            raise ValueError("conversation_generation_fenced")

    defaults = {
        "job_id": job_id,
        "learning_goal": learning_goal,
        "conversation": conversation,
        "status": AIJob.STATUS_QUEUED,
        "stage": stage,
        "percent": max(0, min(100, int(percent))),
        "message": message,
        "max_retries": max_retries,
    }
    try:
        with transaction.atomic():
            job, _created = AIJob.objects.get_or_create(
                task_type=task_type,
                username=username,
                idempotency_key=idempotency_key,
                defaults=defaults,
            )
    except IntegrityError:
        job = AIJob.objects.get(task_type=task_type, username=username, idempotency_key=idempotency_key)
    return job


def get_job(job_id: str, username: Optional[str] = None) -> Optional[AIJob]:
    qs = AIJob.objects.filter(job_id=job_id)
    if username is not None:
        qs = qs.filter(username=username)
    return qs.first()


def job_to_payload(job: AIJob) -> Dict[str, Any]:
    return {
        "job_id": job.job_id,
        "task_type": job.task_type,
        "username": job.username,
        "learning_goal_id": job.learning_goal_id,
        "state": job.status,
        "status": job.status,
        "stage": job.stage,
        "percent": job.percent,
        "message": _public_text(job.message),
        "result": _public_result(job.result or {}),
        "error": job.error_code,
        "error_message": _public_text(job.error_message),
        "retry_count": job.retry_count,
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "updated_at": job.updated_at.isoformat() if job.updated_at else "",
        "finished_at": job.finished_at.isoformat() if job.finished_at else "",
    }


def update_job(
    job_id: str,
    *,
    status: Optional[str] = None,
    stage: Optional[str] = None,
    percent: Optional[int] = None,
    message: Optional[str] = None,
    result: Optional[Dict[str, Any]] = None,
    error_code: Optional[str] = None,
    error_message: Optional[str] = None,
    retry_count: Optional[int] = None,
    expected_conversation_key: Optional[str] = None,
) -> Optional[AIJob]:
    with transaction.atomic():
        if expected_conversation_key:
            scoped_job = AIJob.objects.filter(job_id=job_id).values(
                "username", "learning_goal_id", "conversation_id"
            ).first()
            if (
                not scoped_job
                or scoped_job["conversation_id"] != str(expected_conversation_key)
                or not scoped_job["learning_goal_id"]
            ):
                return None
            conversation_is_writable = LearningConversation.objects.select_for_update().filter(
                conversation_key=str(expected_conversation_key),
                user__username=scoped_job["username"],
                learning_goal_id=scoped_job["learning_goal_id"],
                lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
                active_scope_key__isnull=False,
                retention_expires_at__gt=timezone.now(),
            ).exists()
            if not conversation_is_writable:
                return None
        job = AIJob.objects.select_for_update().filter(job_id=job_id).first()
        if not job:
            return None

        update_fields = ["updated_at"]
        if status is not None:
            job.status = status
            update_fields.append("status")
            if status == AIJob.STATUS_RUNNING and not job.started_at:
                job.started_at = timezone.now()
                update_fields.append("started_at")
            if status in TERMINAL_STATUSES and not job.finished_at:
                job.finished_at = timezone.now()
                update_fields.append("finished_at")
        if stage is not None:
            job.stage = stage
            update_fields.append("stage")
        if percent is not None:
            job.percent = max(0, min(100, int(percent)))
            update_fields.append("percent")
        if message is not None:
            job.message = message
            update_fields.append("message")
        if result is not None:
            job.result = result
            update_fields.append("result")
        if error_code is not None:
            job.error_code = error_code
            update_fields.append("error_code")
        if error_message is not None:
            job.error_message = error_message
            update_fields.append("error_message")
        if retry_count is not None:
            job.retry_count = max(0, int(retry_count))
            update_fields.append("retry_count")

        try:
            job.save(update_fields=sorted(set(update_fields)))
        except Exception as exc:
            logger.error("Could not update AIJob %s (error_code=%s)", job_id, exc.__class__.__name__)
            return None
        return job
