from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from django.core.cache import cache
from django.db import close_old_connections

from learning_apps.adaptive_learning.concept_identity_service import (
    ensure_identity_registry_for_goal,
    taxonomy_fingerprint_for_goal,
)
from learning_apps.infrastructure.services.task_dispatcher import dispatch_task_by_name
from learning_apps.persistence import job_repository as ai_job_service
from learning_apps.persistence.models import AIJob, LearningGoal, UserProfile
from learning_apps.self_assessment.csa_framework import CSA_BLUEPRINT_PROMPT_VERSION
from learning_apps.self_assessment.evidence_admission_service import stable_sha256
from learning_apps.self_assessment.goal_context_service import GoalContextResult
from learning_apps.self_assessment.services.csa_blueprint_service import (
    BlueprintValidationError,
    build_or_load_reference_blueprint,
)
from learning_apps.self_assessment.submission_validation_service import (
    MAX_TARGET_TASK_CHARACTERS,
    MIN_TARGET_TASK_CHARACTERS,
)


logger = logging.getLogger(__name__)
TASK_TYPE = "self_assessment_target_scope"
JOB_KEY_PREFIX = "self_assessment_target_scope_job"
JOB_TIMEOUT_SECONDS = 30 * 60


class TargetTaskValidationError(ValueError):
    pass


def normalize_target_task(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "").replace("\x00", "")).strip()


def validate_target_task(value: Any) -> str:
    target_task = normalize_target_task(value)
    meaningful = sum(1 for char in target_task if not char.isspace())
    if meaningful < MIN_TARGET_TASK_CHARACTERS:
        raise TargetTaskValidationError(
            "Describe a specific task or problem type using at least "
            f"{MIN_TARGET_TASK_CHARACTERS} non-space characters."
        )
    if len(target_task) > MAX_TARGET_TASK_CHARACTERS:
        raise TargetTaskValidationError(
            f"Keep the target task to {MAX_TARGET_TASK_CHARACTERS} characters or fewer."
        )
    return target_task


def target_task_sha256(target_task: str) -> str:
    return stable_sha256({"target_task": normalize_target_task(target_task)})


def _job_key(job_id: str) -> str:
    return f"{JOB_KEY_PREFIX}:{job_id}"


def _set_job(job_id: str, payload: dict[str, Any], *, status: str) -> None:
    cache.set(_job_key(job_id), payload, timeout=JOB_TIMEOUT_SECONDS)
    ai_job_service.update_job(
        job_id,
        status=status,
        stage=str(payload.get("stage") or ""),
        percent=int(payload.get("percent") or 0),
        message=str(payload.get("message") or ""),
        result=payload,
        error_code=str(payload.get("error") or ""),
    )


def get_target_scope_job(job_id: str, username: str) -> dict[str, Any] | None:
    payload = cache.get(_job_key(job_id))
    if isinstance(payload, dict) and payload.get("username") == username:
        return payload
    job = ai_job_service.get_job(job_id, username=username)
    if not job or job.task_type != TASK_TYPE:
        return None
    stored = dict(job.result or {})
    if stored:
        return stored
    return {
        "username": username,
        "state": "running",
        "stage": job.stage,
        "percent": job.percent,
        "message": job.message,
        "learning_goal_id": job.learning_goal_id,
    }


def start_target_scope_job(
    *,
    username: str,
    target_task: str,
    goal_context: GoalContextResult,
) -> str:
    normalized = validate_target_task(target_task)
    task_hash = target_task_sha256(normalized)
    idempotency_key = stable_sha256(
        {
            "username": username,
            "learning_goal_id": int(goal_context.learning_goal_id or 0),
            "target_task_sha256": task_hash,
            "prompt_version": CSA_BLUEPRINT_PROMPT_VERSION,
        }
    )
    existing = AIJob.objects.filter(
        task_type=TASK_TYPE,
        username=username,
        idempotency_key=idempotency_key,
    ).first()
    if existing and existing.status not in {AIJob.STATUS_FAILED, AIJob.STATUS_DEGRADED}:
        return existing.job_id
    if existing:
        idempotency_key = f"{idempotency_key[:120]}:{uuid.uuid4().hex[:8]}"
    job_id = uuid.uuid4().hex
    job = ai_job_service.create_job(
        task_type=TASK_TYPE,
        username=username,
        job_id=job_id,
        learning_goal_id=goal_context.learning_goal_id,
        idempotency_key=idempotency_key,
        stage="queued",
        percent=5,
        message="Checking the target task against your learning goal.",
    )
    initial = {
        "username": username,
        "state": "running",
        "stage": "queued",
        "percent": 5,
        "message": "Checking the target task against your learning goal.",
        "learning_goal_id": goal_context.learning_goal_id,
        "target_task": normalized,
        "target_task_sha256": task_hash,
        "error": "",
    }
    _set_job(job.job_id, initial, status=AIJob.STATUS_QUEUED)
    dispatch_task_by_name(
        "self_assessment.run_target_scope_job",
        job.job_id,
        username,
        normalized,
        goal_context.__dict__,
        fallback=lambda: _run_target_scope_job(
            job.job_id,
            username,
            normalized,
            goal_context,
        ),
    )
    return job.job_id


def _run_target_scope_job(
    job_id: str,
    username: str,
    target_task: str,
    goal_context: GoalContextResult,
) -> None:
    close_old_connections()
    task_hash = target_task_sha256(target_task)
    base = {
        "username": username,
        "learning_goal_id": goal_context.learning_goal_id,
        "target_task": target_task,
        "target_task_sha256": task_hash,
        "error": "",
    }
    _set_job(
        job_id,
        {
            **base,
            "state": "running",
            "stage": "defining_scope",
            "percent": 28,
            "message": "Defining a fair assessment scope.",
        },
        status=AIJob.STATUS_RUNNING,
    )
    try:
        user = UserProfile.objects.filter(username=username).first()
        goal = (
            LearningGoal.objects.filter(
                id=goal_context.learning_goal_id,
                user=user,
            ).first()
            if user and goal_context.learning_goal_id
            else None
        )
        if not user or not goal:
            raise BlueprintValidationError("scope_not_found")
        ensure_identity_registry_for_goal(user, goal)
        taxonomy_sha256 = taxonomy_fingerprint_for_goal(user, goal)
        result = build_or_load_reference_blueprint(
            user=user,
            goal=goal,
            taxonomy_sha256=taxonomy_sha256,
            target_task=target_task,
        )
        public = {
            **base,
            "scope_contract": result.scope_contract,
            "percent": 100,
            "stage": result.status,
        }
        if result.status == "needs_revision":
            _set_job(
                job_id,
                {
                    **public,
                    "state": "needs_revision",
                    "message": (
                        result.scope_contract.get("revision_message")
                        or "Revise the target task and try again."
                    ),
                },
                status=AIJob.STATUS_SUCCEEDED,
            )
            return
        blueprint = result.blueprint
        if blueprint is None:
            raise BlueprintValidationError("blueprint_missing_after_ready")
        _set_job(
            job_id,
            {
                **public,
                "state": "ready",
                "message": "Review and confirm this assessment scope.",
                "reference_blueprint_id": blueprint.id,
                "reference_blueprint_sha256": blueprint.blueprint_sha256,
            },
            status=AIJob.STATUS_SUCCEEDED,
        )
    except Exception as exc:
        logger.exception(
            "Target-scope job %s failed for user=%s goal=%s",
            job_id,
            username,
            goal_context.learning_goal_id,
        )
        _set_job(
            job_id,
            {
                **base,
                "state": "failed",
                "stage": "failed",
                "percent": 100,
                "message": "The assessment scope could not be prepared. Please try again.",
                "error": str(exc)[:96] or "unexpected_error",
            },
            status=AIJob.STATUS_DEGRADED,
        )
    finally:
        close_old_connections()


__all__ = [
    "TargetTaskValidationError",
    "_run_target_scope_job",
    "get_target_scope_job",
    "normalize_target_task",
    "start_target_scope_job",
    "target_task_sha256",
    "validate_target_task",
]
