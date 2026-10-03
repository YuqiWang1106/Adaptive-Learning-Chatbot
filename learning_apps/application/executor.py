from __future__ import annotations

import time
from datetime import timedelta
from typing import Any, Mapping, Protocol

from django.db import transaction
from django.utils import timezone
from pydantic import ValidationError as PydanticValidationError

from learning_apps.persistence.models import LearningConversation, LearningGoal, UserProfile

from .contracts import (
    SERVER_SCOPE_ARGUMENTS,
    CapabilityAuthority,
    CapabilityContext,
    CapabilityEntrypoint,
    CapabilityError,
    CapabilityResult,
    CapabilityScope,
    stable_sha256,
)
from .models import CapabilityInvocation, ExecutionRun
from .registry import CapabilityRegistry


class CapabilityObserver(Protocol):
    """Entrypoint-specific lifecycle hooks around the neutral executor."""

    def before_tool(self, context: CapabilityContext, spec_name: str, display_name: str) -> None: ...

    def after_tool(
        self,
        context: CapabilityContext,
        spec_name: str,
        status: str,
        reason_code: str,
        latency_ms: int,
        evidence_category: str,
    ) -> None: ...


class NullCapabilityObserver:
    def before_tool(self, context: CapabilityContext, spec_name: str, display_name: str) -> None:
        return None

    def after_tool(
        self,
        context: CapabilityContext,
        spec_name: str,
        status: str,
        reason_code: str,
        latency_ms: int,
        evidence_category: str,
    ) -> None:
        return None


class CapabilityExecutor:
    """Single policy, budget, validation and audit gateway for every entrypoint."""

    def __init__(self, registry: CapabilityRegistry, *, observer: CapabilityObserver | None = None):
        self.registry = registry
        self.observer = observer or NullCapabilityObserver()

    def _validate_scope(self, context: CapabilityContext, required_scope: CapabilityScope) -> None:
        if required_scope is CapabilityScope.SYSTEM:
            if context.scope is not CapabilityScope.SYSTEM:
                raise CapabilityError("system_scope_required")
            return

        user = UserProfile.objects.filter(user_id=context.user_id, username=context.username).first()
        if not user:
            raise CapabilityError("user_scope_fenced")
        if required_scope is CapabilityScope.USER:
            return

        goal = LearningGoal.objects.filter(id=context.learning_goal_id, user=user).first()
        if not goal:
            raise CapabilityError("goal_scope_fenced")
        if required_scope is CapabilityScope.GOAL:
            return

        conversation = LearningConversation.objects.filter(
            conversation_key=context.conversation_key,
            user=user,
            learning_goal=goal,
            generation=context.conversation_generation,
            lifecycle=LearningConversation.LIFECYCLE_ACTIVE,
        ).first()
        if not conversation:
            raise CapabilityError("conversation_scope_fenced")

    def execute(
        self,
        name: str,
        arguments: Mapping[str, Any] | None,
        *,
        context: CapabilityContext,
        idempotency_key: str,
    ) -> CapabilityResult:
        spec = self.registry.get(name)
        raw_arguments = dict(arguments or {})
        forbidden = SERVER_SCOPE_ARGUMENTS.intersection(raw_arguments)
        if forbidden:
            raise CapabilityError("server_scope_argument_forbidden")
        if context.entrypoint not in spec.allowed_entrypoints:
            raise CapabilityError("entrypoint_not_allowed")
        if context.entrypoint is CapabilityEntrypoint.AGENT:
            if not spec.agent_visible or spec.authority in {CapabilityAuthority.COMMAND, CapabilityAuthority.ADMIN}:
                raise CapabilityError("agent_authority_blocked")
            if spec.requires_approval and not bool(context.metadata.get("sdk_approval_enforced")):
                raise CapabilityError("approval_required_before_execution")

        self._validate_scope(context, spec.required_scope)
        try:
            validated_input = spec.input_model.model_validate(raw_arguments)
        except PydanticValidationError as exc:
            raise CapabilityError("invalid_capability_input") from exc

        execution = ExecutionRun.objects.select_related("user", "learning_goal", "conversation").get(
            execution_id=context.execution_id
        )
        input_sha256 = stable_sha256(validated_input.model_dump(mode="json"))
        normalized_idempotency_key = str(idempotency_key or "")[:96]
        if not normalized_idempotency_key:
            raise CapabilityError("idempotency_key_required")
        with transaction.atomic():
            invocation, created = CapabilityInvocation.objects.get_or_create(
                capability_name=spec.name,
                idempotency_key=normalized_idempotency_key,
                defaults={
                    "execution": execution,
                    "capability_version": spec.version,
                    "authority": spec.authority.value,
                    "status": CapabilityInvocation.STATUS_PENDING,
                    "input_sha256": input_sha256,
                    "output_sha256": stable_sha256({}),
                    "output_payload": {},
                    "reason_code": "reserved",
                    "latency_ms": 0,
                    "attempt_count": 1,
                    "lease_expires_at": timezone.now() + timedelta(seconds=max(2.0, spec.timeout_seconds + 2.0)),
                    "mastery_write_authorized": False,
                },
            )
        if not created:
            with transaction.atomic():
                invocation = CapabilityInvocation.objects.select_for_update().select_related("execution").get(
                    invocation_id=invocation.invocation_id
                )
                original = invocation.execution
                if (
                    invocation.input_sha256 != input_sha256
                    or (original.user_id or 0) != (context.user_id or 0)
                    or (original.learning_goal_id or 0) != (context.learning_goal_id or 0)
                    or (original.conversation_id or "") != (context.conversation_key or "")
                ):
                    raise CapabilityError("idempotency_scope_or_input_collision")
                if invocation.status == CapabilityInvocation.STATUS_SUCCEEDED:
                    if not spec.persist_output_payload:
                        raise CapabilityError("non_replayable_capability_result")
                    return CapabilityResult(
                        capability_name=spec.name,
                        capability_version=invocation.capability_version,
                        status=invocation.status,
                        payload=dict(invocation.output_payload or {}),
                        reason_code=invocation.reason_code,
                        invocation_id=invocation.invocation_id,
                        replayed=True,
                    )
                if invocation.status == CapabilityInvocation.STATUS_PENDING:
                    if invocation.lease_expires_at and invocation.lease_expires_at <= timezone.now():
                        invocation.execution = execution
                        invocation.attempt_count += 1
                        invocation.lease_expires_at = timezone.now() + timedelta(
                            seconds=max(2.0, spec.timeout_seconds + 2.0)
                        )
                        invocation.reason_code = "reclaimed_after_expired_lease"
                        invocation.save(
                            update_fields=[
                                "execution",
                                "attempt_count",
                                "lease_expires_at",
                                "reason_code",
                            ]
                        )
                        created = True
                    else:
                        raise CapabilityError("capability_invocation_in_progress")
                elif (
                    invocation.status == CapabilityInvocation.STATUS_FAILED
                    and spec.retry_failed_invocations
                ):
                    invocation.execution = execution
                    invocation.status = CapabilityInvocation.STATUS_PENDING
                    invocation.attempt_count += 1
                    invocation.lease_expires_at = timezone.now() + timedelta(
                        seconds=max(2.0, spec.timeout_seconds + 2.0)
                    )
                    invocation.reason_code = "retrying_failed_invocation"
                    invocation.save(
                        update_fields=[
                            "execution",
                            "status",
                            "attempt_count",
                            "lease_expires_at",
                            "reason_code",
                        ]
                    )
                    created = True
                else:
                    raise CapabilityError(invocation.reason_code or "capability_replay_blocked")

        try:
            self.observer.before_tool(context, spec.name, spec.display_name or spec.name)
        except CapabilityError as exc:
            CapabilityInvocation.objects.filter(invocation_id=invocation.invocation_id).update(
                status=CapabilityInvocation.STATUS_BLOCKED,
                reason_code=exc.code,
                lease_expires_at=None,
            )
            raise
        except Exception as exc:
            CapabilityInvocation.objects.filter(invocation_id=invocation.invocation_id).update(
                status=CapabilityInvocation.STATUS_FAILED,
                reason_code="reservation_failed",
                lease_expires_at=None,
            )
            raise CapabilityError("capability_reservation_failed") from exc

        started = time.monotonic()
        status = CapabilityInvocation.STATUS_SUCCEEDED
        reason_code = "ok"
        blocked_message = ""
        output_payload: dict[str, Any] = {}
        try:
            assert spec.handler is not None
            output = spec.handler(context, validated_input)
            output_payload = output.model_dump(mode="json") if hasattr(output, "model_dump") else dict(output)
            validated_output = spec.output_model.model_validate(output_payload)
            output_payload = validated_output.model_dump(mode="json")
        except CapabilityError as exc:
            status = CapabilityInvocation.STATUS_BLOCKED
            reason_code = exc.code
            blocked_message = exc.message
        except PydanticValidationError as exc:
            status = CapabilityInvocation.STATUS_FAILED
            reason_code = "invalid_capability_output"
            raise CapabilityError(reason_code) from exc
        except Exception as exc:
            status = CapabilityInvocation.STATUS_FAILED
            reason_code = f"handler_{exc.__class__.__name__.lower()}"
            raise CapabilityError("capability_failed") from exc
        finally:
            latency_ms = max(0, int((time.monotonic() - started) * 1000))
            persisted_output = output_payload if spec.persist_output_payload else {
                "redacted": True,
                "schema": "capability-output-redaction-v1",
            }
            CapabilityInvocation.objects.filter(invocation_id=invocation.invocation_id).update(
                status=status,
                output_sha256=stable_sha256(output_payload),
                output_payload=persisted_output,
                reason_code=reason_code,
                latency_ms=latency_ms,
                lease_expires_at=None,
            )
            self.observer.after_tool(
                context,
                spec.name,
                status,
                reason_code,
                latency_ms,
                spec.evidence_category,
            )
        if status != CapabilityInvocation.STATUS_SUCCEEDED:
            raise CapabilityError(reason_code, blocked_message)
        return CapabilityResult(
            capability_name=spec.name,
            capability_version=spec.version,
            status=status,
            payload=output_payload,
            reason_code=reason_code,
            invocation_id=invocation.invocation_id,
            replayed=False,
        )


def create_execution_run(
    *,
    user: UserProfile | None = None,
    goal: LearningGoal | None = None,
    conversation: LearningConversation | None = None,
    entrypoint: CapabilityEntrypoint,
    workflow: str,
    trace_id: str,
    agent_run: Any | None = None,
) -> ExecutionRun:
    return ExecutionRun.objects.create(
        agent_run=agent_run,
        user=user,
        learning_goal=goal,
        conversation=conversation,
        entrypoint=entrypoint.value,
        workflow=workflow,
        trace_id=trace_id,
    )


def complete_execution_run(execution: ExecutionRun, *, failed: bool = False) -> None:
    execution.status = ExecutionRun.STATUS_FAILED if failed else ExecutionRun.STATUS_COMPLETED
    execution.completed_at = timezone.now()
    execution.save(update_fields=["status", "completed_at"])
