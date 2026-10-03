from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from learning_apps.chat.services.conversation_repository_service import (
    ensure_active_conversation,
)
from learning_apps.infrastructure.services.task_dispatcher import dispatch_task
from learning_apps.persistence.models import LearningGoal, UserProfile

from .checkpoints import load_checkpoint, save_checkpoint
from .crypto import seal_json
from .events import append_run_event
from .models import AgentInterruption, LearningAgentRun
from .release import current_release_manifest
from .guardrails import contains_agent_control_injection


class AgentRunScopeError(ValueError):
    pass


class AgentRunConflict(RuntimeError):
    pass


@dataclass(frozen=True)
class AgentEventBatch:
    events: tuple[dict[str, Any], ...]
    status: str
    should_end: bool


def _hash(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _owned_run(username: str, run_id: str, *, lock: bool = False) -> LearningAgentRun:
    queryset = LearningAgentRun.objects.select_related("user", "learning_goal", "conversation", "release")
    if lock:
        queryset = queryset.select_for_update()
    run = queryset.filter(run_id=run_id, user__username=username).first()
    if not run:
        raise AgentRunScopeError("agent_run_not_found")
    return run


def create_agent_run(
    *,
    username: str,
    learning_goal_id: int,
    question: str,
    idempotency_key: str = "",
    origin: str = LearningAgentRun.ORIGIN_NORMAL,
    origin_reference: str = "",
) -> tuple[LearningAgentRun, bool]:
    clean_question = " ".join(str(question or "").split())
    if not clean_question:
        raise ValueError("question_required")
    if len(clean_question) > 8000:
        raise ValueError("question_too_long")
    if contains_agent_control_injection(clean_question):
        raise ValueError("unsafe_agent_control_request")
    if origin not in {choice[0] for choice in LearningAgentRun.ORIGIN_CHOICES}:
        raise ValueError("invalid_agent_run_origin")
    if origin == LearningAgentRun.ORIGIN_MICRO_CHECK_RESPONSE and not str(origin_reference or "").strip():
        raise ValueError("origin_reference_required")
    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(id=learning_goal_id, user=user).first() if user else None
    if not user or not goal:
        raise AgentRunScopeError("learning_goal_not_found")
    conversation = ensure_active_conversation(username, learning_goal_id)
    release = current_release_manifest()
    request_sha256 = _hash(clean_question)
    client_key = str(idempotency_key or "").strip()[:96]
    stable_key = client_key or f"request:{request_sha256}:{conversation.conversation_key}"
    expires_at = timezone.now() + timedelta(seconds=settings.LEARNING_AGENT_INTERRUPTION_TTL_SECONDS)
    with transaction.atomic():
        existing = LearningAgentRun.objects.filter(
            user=user,
            learning_goal=goal,
            idempotency_key=stable_key,
        ).first()
        if existing:
            return existing, False
        run = LearningAgentRun(
            user=user,
            learning_goal=goal,
            conversation=conversation,
            conversation_generation=conversation.generation,
            release=release,
            origin=origin,
            origin_reference=str(origin_reference or "")[:64],
            idempotency_key=stable_key,
            request_sha256=request_sha256,
            trace_id=uuid.uuid4().hex,
            model=release.model,
            reasoning_effort=release.reasoning_effort,
            max_turns=settings.LEARNING_AGENT_MAX_TURNS,
            max_tool_calls=settings.LEARNING_AGENT_MAX_TOOL_CALLS,
            max_skills=settings.LEARNING_AGENT_MAX_SKILLS,
            expires_at=expires_at,
        )
        run.full_clean()
        run.save()
        if origin == LearningAgentRun.ORIGIN_NORMAL:
            from .micro_checks import supersede_open_micro_check

            supersede_open_micro_check(run)
        save_checkpoint(
            run,
            {
                "kind": "initial",
                "question": clean_question,
                "sdk_state": None,
                "resolved_interruption_id": "",
            },
        )
    transaction.on_commit(lambda: dispatch_agent_run(run.run_id))
    return run, True


def dispatch_agent_run(run_id: str) -> None:
    from .tasks import execute_learning_agent_run
    from .runtime import execute_agent_run

    dispatch_task(
        execute_learning_agent_run,
        run_id,
        fallback=lambda: execute_agent_run(run_id),
    )


def serialize_run(run: LearningAgentRun, *, include_events: bool = False) -> dict[str, Any]:
    payload = {
        "run_id": run.run_id,
        "learning_goal_id": run.learning_goal_id,
        "conversation_generation": run.conversation_generation,
        "status": run.status,
        "origin": run.origin,
        "trace_id": run.trace_id,
        "release": {
            "name": run.release.release_name,
            "model": run.model,
            "reasoning_effort": run.reasoning_effort,
            "manifest_sha256": run.release.manifest_sha256,
        },
        "budgets": {
            "turns": {"used": run.turn_count, "max": run.max_turns},
            "tool_calls": {"used": run.tool_call_count, "max": run.max_tool_calls},
            "skills": {"used": run.skill_count, "max": run.max_skills},
        },
        "usage": {
            "input_tokens": run.input_tokens,
            "output_tokens": run.output_tokens,
            "model_requests": run.model_request_count,
            "elapsed_ms": run.elapsed_ms,
        },
        "plan": run.plan,
        "selected_skills": run.selected_skills,
        "evidence": run.evidence_manifest,
        "adaptive_context": {
            key: value
            for key, value in (run.adaptive_context_manifest or {}).items()
            if key not in {"context_sha256", "concept_keys", "misconception_concept_keys"}
        },
        "conversation_memory": {
            key: value
            for key, value in (run.conversation_memory_manifest or {}).items()
            if key not in {"state_sha256", "latest_history_id"}
        },
        "result": run.result_payload,
        "error_code": run.error_code,
        "expires_at": run.expires_at.isoformat(),
        "created_at": run.created_at.isoformat(),
        "updated_at": run.updated_at.isoformat(),
    }
    pending = run.interruptions.filter(status=AgentInterruption.STATUS_PENDING).order_by("created_at").first()
    if pending:
        payload["interruption"] = {
            "interruption_id": pending.interruption_id,
            "kind": pending.kind,
            "status": pending.status,
            "payload": pending.public_payload,
            "expires_at": pending.expires_at.isoformat(),
        }
    if include_events:
        payload["events"] = [
            {
                "sequence": event.sequence,
                "type": event.event_type,
                "payload": event.public_payload,
                "created_at": event.created_at.isoformat(),
            }
            for event in run.events.order_by("sequence")
        ]
    return payload


def get_agent_run(username: str, run_id: str) -> LearningAgentRun:
    return _owned_run(username, run_id)


def get_agent_event_batch(
    username: str,
    run_id: str,
    *,
    after_sequence: int = 0,
    limit: int = 250,
) -> AgentEventBatch:
    run = _owned_run(username, run_id)
    rows = run.events.filter(sequence__gt=max(0, after_sequence)).order_by("sequence")[:max(1, min(limit, 250))]
    events = tuple(
        {
            "sequence": item.sequence,
            "type": item.event_type,
            "payload": item.public_payload,
        }
        for item in rows
    )
    should_end = run.status in LearningAgentRun.TERMINAL_STATUSES or run.status in {
        LearningAgentRun.STATUS_WAITING_CLARIFICATION,
        LearningAgentRun.STATUS_WAITING_APPROVAL,
    }
    return AgentEventBatch(events=events, status=run.status, should_end=should_end)


def resolve_interruption(
    *,
    username: str,
    run_id: str,
    interruption_id: str,
    kind: str,
    response: Any,
) -> LearningAgentRun:
    with transaction.atomic():
        run = _owned_run(username, run_id, lock=True)
        if run.expires_at <= timezone.now():
            run.status = LearningAgentRun.STATUS_EXPIRED
            run.save(update_fields=["status", "updated_at"])
            raise AgentRunConflict("agent_run_expired")
        interruption = AgentInterruption.objects.select_for_update().filter(
            interruption_id=interruption_id,
            run=run,
            kind=kind,
        ).first()
        if not interruption:
            raise AgentRunScopeError("interruption_not_found")
        if interruption.status != AgentInterruption.STATUS_PENDING:
            return run
        if interruption.expires_at <= timezone.now():
            interruption.status = AgentInterruption.STATUS_EXPIRED
            interruption.resolved_at = timezone.now()
            interruption.save(update_fields=["status", "resolved_at"])
            run.status = LearningAgentRun.STATUS_EXPIRED
            run.save(update_fields=["status", "updated_at"])
            raise AgentRunConflict("interruption_expired")
        encrypted, digest = seal_json({"response": response})
        interruption.encrypted_response = encrypted
        interruption.response_sha256 = digest
        if kind == AgentInterruption.KIND_CLARIFICATION:
            if not isinstance(response, str) or not response.strip():
                raise ValueError("clarification_answer_required")
            interruption.status = AgentInterruption.STATUS_ANSWERED
        else:
            approved = bool(response)
            interruption.status = AgentInterruption.STATUS_APPROVED if approved else AgentInterruption.STATUS_REJECTED
        interruption.resolved_at = timezone.now()
        interruption.save(update_fields=["encrypted_response", "response_sha256", "status", "resolved_at"])
        checkpoint = load_checkpoint(run)
        checkpoint["resolved_interruption_id"] = interruption.interruption_id
        save_checkpoint(run, checkpoint)
        run.status = LearningAgentRun.STATUS_RESUMING
        run.save(update_fields=["status", "updated_at"])
        append_run_event(
            run,
            "approval_resolved",
            {"interruption_id": interruption.interruption_id, "kind": kind, "status": interruption.status},
        )
    transaction.on_commit(lambda: dispatch_agent_run(run.run_id))
    return run


def answer_agent_clarification(
    *, username: str, run_id: str, interruption_id: str, answer: str
) -> LearningAgentRun:
    return resolve_interruption(
        username=username,
        run_id=run_id,
        interruption_id=interruption_id,
        kind=AgentInterruption.KIND_CLARIFICATION,
        response=answer,
    )


def decide_agent_approval(
    *, username: str, run_id: str, interruption_id: str, approved: bool
) -> LearningAgentRun:
    return resolve_interruption(
        username=username,
        run_id=run_id,
        interruption_id=interruption_id,
        kind=AgentInterruption.KIND_APPROVAL,
        response=approved,
    )


def cancel_agent_run(*, username: str, run_id: str) -> LearningAgentRun:
    with transaction.atomic():
        run = _owned_run(username, run_id, lock=True)
        if run.status in LearningAgentRun.TERMINAL_STATUSES:
            return run
        run.cancel_requested_at = timezone.now()
        if run.status in {
            LearningAgentRun.STATUS_QUEUED,
            LearningAgentRun.STATUS_WAITING_CLARIFICATION,
            LearningAgentRun.STATUS_WAITING_APPROVAL,
        }:
            run.status = LearningAgentRun.STATUS_CANCELLED
            run.completed_at = timezone.now()
            run.interruptions.filter(status=AgentInterruption.STATUS_PENDING).update(
                status=AgentInterruption.STATUS_CANCELLED,
                resolved_at=timezone.now(),
            )
        run.save(update_fields=["cancel_requested_at", "status", "completed_at", "updated_at"])
    return run


def expire_runs_for_conversation(conversation_key: str) -> int:
    now = timezone.now()
    with transaction.atomic():
        runs = LearningAgentRun.objects.select_for_update().filter(
            conversation_id=conversation_key,
            status__in=[
                LearningAgentRun.STATUS_QUEUED,
                LearningAgentRun.STATUS_RUNNING,
                LearningAgentRun.STATUS_RESUMING,
                LearningAgentRun.STATUS_WAITING_CLARIFICATION,
                LearningAgentRun.STATUS_WAITING_APPROVAL,
            ],
        )
        run_ids = list(runs.values_list("run_id", flat=True))
        if run_ids:
            LearningAgentRun.objects.filter(run_id__in=run_ids).update(
                status=LearningAgentRun.STATUS_EXPIRED,
                completed_at=now,
                error_code="conversation_cleared",
            )
            AgentInterruption.objects.filter(
                run_id__in=run_ids,
                status=AgentInterruption.STATUS_PENDING,
            ).update(status=AgentInterruption.STATUS_CANCELLED, resolved_at=now)
            from .models import AgentRunCheckpoint

            AgentRunCheckpoint.objects.filter(run_id__in=run_ids).delete()
        from .models import AgentConversationMemory

        AgentConversationMemory.objects.filter(conversation_id=conversation_key).delete()
        return len(run_ids)


def expire_stale_agent_runs(*, limit: int = 200) -> int:
    now = timezone.now()
    run_ids = list(
        LearningAgentRun.objects.filter(
            expires_at__lte=now,
            status__in=[
                LearningAgentRun.STATUS_QUEUED,
                LearningAgentRun.STATUS_RUNNING,
                LearningAgentRun.STATUS_RESUMING,
                LearningAgentRun.STATUS_WAITING_CLARIFICATION,
                LearningAgentRun.STATUS_WAITING_APPROVAL,
            ],
        )
        .order_by("expires_at")
        .values_list("run_id", flat=True)[: max(1, min(limit, 1000))]
    )
    if not run_ids:
        return 0
    with transaction.atomic():
        LearningAgentRun.objects.select_for_update().filter(run_id__in=run_ids).update(
            status=LearningAgentRun.STATUS_EXPIRED,
            completed_at=now,
            error_code="run_expired",
        )
        AgentInterruption.objects.filter(
            run_id__in=run_ids,
            status=AgentInterruption.STATUS_PENDING,
        ).update(status=AgentInterruption.STATUS_EXPIRED, resolved_at=now)
        from .models import AgentRunCheckpoint

        AgentRunCheckpoint.objects.filter(run_id__in=run_ids).delete()
    return len(run_ids)


def recover_stale_agent_runs(*, limit: int = 100) -> int:
    cutoff = timezone.now() - timedelta(
        seconds=max(30, int(settings.LEARNING_AGENT_TIMEOUT_SECONDS) + 30)
    )
    candidate_ids = list(
        LearningAgentRun.objects.filter(
            status__in=[LearningAgentRun.STATUS_RUNNING, LearningAgentRun.STATUS_RESUMING],
            updated_at__lte=cutoff,
            expires_at__gt=timezone.now(),
        )
        .order_by("updated_at")
        .values_list("run_id", flat=True)[: max(1, min(limit, 500))]
    )
    recovered = []
    for run_id in candidate_ids:
        with transaction.atomic():
            run = LearningAgentRun.objects.select_for_update().filter(
                run_id=run_id,
                status__in=[LearningAgentRun.STATUS_RUNNING, LearningAgentRun.STATUS_RESUMING],
                updated_at__lte=cutoff,
            ).first()
            if not run:
                continue
            run.status = LearningAgentRun.STATUS_QUEUED
            run.error_code = "worker_recovery_queued"
            run.save(update_fields=["status", "error_code", "updated_at"])
            recovered.append(run.run_id)
    for run_id in recovered:
        dispatch_agent_run(run_id)
    return len(recovered)
