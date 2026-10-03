from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from typing import Any

from django.conf import settings
from django.db import close_old_connections, transaction
from django.utils import timezone

from learning_apps.infrastructure.services.llm_gateway import llm_gateway

from .adaptive_context import assemble_adaptive_context
from .checkpoints import delete_checkpoint, load_checkpoint, save_checkpoint
from .conversation_memory import prepare_conversation_input
from .crypto import unseal_json
from .events import append_run_event
from learning_apps.application.executor import complete_execution_run, create_execution_run
from .models import AgentInterruption, LearningAgentRun
from .micro_checks import admit_micro_check, feedback_context_item, serialize_micro_check
from .output import TutorTurnOutput
from .plugins import active_plugin_policy
from .release import release_is_available
from .sdk_adapter import build_sdk_agent, canonical_tool_name
from .turn_commit import commit_tutor_turn
from learning_apps.application.contracts import CapabilityEntrypoint, CapabilityError
from .skills import enabled_capabilities


logger = logging.getLogger(__name__)


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, CapabilityError):
        return exc.code[:64]
    message = str(exc).strip()
    if re.fullmatch(r"[a-z][a-z0-9_]{1,63}", message):
        return message
    return exc.__class__.__name__[:64]


def _load_resolved_response(interruption: AgentInterruption) -> Any:
    if not interruption.encrypted_response:
        return None
    payload = unseal_json(interruption.encrypted_response, interruption.response_sha256)
    return payload.get("response") if isinstance(payload, dict) else None


def _public_interruption_payload(tool_name: str, arguments: dict) -> tuple[str, dict]:
    canonical = canonical_tool_name(tool_name)
    if canonical == "runtime_request_clarification":
        return AgentInterruption.KIND_CLARIFICATION, {
            "question": str(arguments.get("question") or "")[:400],
            "options": [str(value)[:120] for value in arguments.get("options", [])[:4]],
            "why_needed": str(arguments.get("why_needed") or "")[:240],
        }
    impact_by_tool = {
        "memory.propose": "Save one explicit learning preference for this learning goal.",
        "quiz.propose": "Create an approved quiz draft. This does not activate or deliver a quiz.",
        "review_plan.propose": "Create an approved review-plan draft. This does not schedule a review.",
        "external.study_session.propose": "Create an approved study-session draft. No external action will run.",
    }
    return AgentInterruption.KIND_APPROVAL, {
        "action": canonical,
        "summary": str(arguments.get("reason") or arguments.get("focus") or arguments.get("title") or canonical)[:240],
        "impact": impact_by_tool.get(canonical, "Create a proposal scoped to this learning goal."),
        "reversible": True,
        "permission": "student_approved_proposal",
    }


def _execute_agent_run_sync(run_id: str) -> None:
    from agents import RunConfig, RunState, Runner

    segment_started = time.monotonic()
    segment_accounted = False
    with transaction.atomic():
        run = LearningAgentRun.objects.select_for_update().select_related(
            "user", "learning_goal", "conversation", "release"
        ).get(run_id=run_id)
        if run.status in LearningAgentRun.TERMINAL_STATUSES:
            return
        if run.cancel_requested_at:
            run.status = LearningAgentRun.STATUS_CANCELLED
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "completed_at", "updated_at"])
            return
        if run.conversation.lifecycle != run.conversation.LIFECYCLE_ACTIVE or run.conversation.generation != run.conversation_generation:
            run.status = LearningAgentRun.STATUS_EXPIRED
            run.error_code = "conversation_fenced"
            run.completed_at = timezone.now()
            run.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
            return
        run.status = LearningAgentRun.STATUS_RUNNING
        if not run.started_at:
            run.started_at = timezone.now()
        run.save(update_fields=["status", "started_at", "updated_at"])

    append_run_event(run, "run_started", {"release": run.release.release_name, "resumed": run.turn_count > 0})
    if not release_is_available(run.release):
        raise CapabilityError("agent_release_artifact_unavailable")
    checkpoint = load_checkpoint(run)
    question = str(checkpoint.get("question") or "")
    execution = create_execution_run(
        user=run.user,
        goal=run.learning_goal,
        conversation=run.conversation,
        entrypoint=CapabilityEntrypoint.AGENT,
        workflow="adaptive_tutor_chat_v2",
        trace_id=run.trace_id,
        agent_run=run,
    )
    plugin_policy = active_plugin_policy()
    context = {
        "run_id": run.run_id,
        "execution_id": execution.execution_id,
        "trusted_user_text": question,
        "clarification_answer": "",
        "enabled_capabilities": sorted(
            set(enabled_capabilities(run.selected_skills)) & set(plugin_policy.tool_names)
        ),
    }
    agent = build_sdk_agent(
        model=run.model,
        reasoning_effort=run.reasoning_effort,
        capability_allowlist=set(plugin_policy.tool_names),
        skill_allowlist=set(plugin_policy.skill_ids),
    )
    runner_input: Any = question
    runner_context: Any = context
    resolved_id = str(checkpoint.get("resolved_interruption_id") or "")
    if checkpoint.get("sdk_state"):
        resolved_interruption = None
        resolved_response = None
        if resolved_id:
            resolved_interruption = AgentInterruption.objects.get(interruption_id=resolved_id, run=run)
            resolved_response = _load_resolved_response(resolved_interruption)
            if resolved_interruption.kind == AgentInterruption.KIND_CLARIFICATION:
                context["clarification_answer"] = str(resolved_response or "")
        state = asyncio.run(
            RunState.from_json(
                agent,
                checkpoint["sdk_state"],
                context_override=context,
                strict_context=True,
            )
        )
        if resolved_interruption:
            pending = state.get_interruptions()
            match = next((item for item in pending if item.call_id == resolved_interruption.sdk_call_id), None)
            if not match:
                raise CapabilityError("sdk_interruption_not_found")
            if resolved_interruption.kind == AgentInterruption.KIND_CLARIFICATION:
                state.approve(match)
            elif resolved_interruption.status == AgentInterruption.STATUS_APPROVED:
                state.approve(match)
            else:
                state.reject(match, rejection_message="The student rejected this proposed action. Continue without executing it.")
        runner_input = state
        # Passing a fresh context to Runner while resuming would replace the
        # SDK's context wrapper and discard its restored approval records.
        runner_context = None
    else:
        prepared_context = assemble_adaptive_context(run, question)
        prepared_memory = prepare_conversation_input(run, question)
        feedback_item = feedback_context_item(run)
        runner_input = [prepared_context.input_item]
        if feedback_item:
            runner_input.append(feedback_item)
        runner_input.extend(prepared_memory.input_items)
        with transaction.atomic():
            locked = LearningAgentRun.objects.select_for_update().get(run_id=run.run_id)
            locked.adaptive_context_manifest = prepared_context.manifest
            locked.conversation_memory_manifest = prepared_memory.manifest
            locked.save(update_fields=["adaptive_context_manifest", "conversation_memory_manifest", "updated_at"])
        append_run_event(
            run,
            "adaptive_context_loaded",
            {
                "policy_version": prepared_context.manifest["policy_version"],
                "observed_status": prepared_context.manifest["observed_status"],
                "perceived_status": prepared_context.manifest["perceived_status"],
                "adaptation_level": prepared_context.manifest["adaptation_level"],
                "scaffolding": prepared_context.manifest["scaffolding"],
                "focus_status": prepared_context.manifest["focus_status"],
                "focus_concept_label": prepared_context.manifest["focus_concept_label"],
            },
        )
        append_run_event(
            run,
            "memory_loaded",
            {
                "strategy": prepared_memory.manifest["strategy"],
                "policy_version": prepared_memory.manifest["policy_version"],
                "total_prior_turns": prepared_memory.manifest["total_prior_turns"],
                "recent_turns_loaded": prepared_memory.manifest["recent_turns_loaded"],
                "compacted_turns_loaded": prepared_memory.manifest["compacted_turns_loaded"],
            },
        )

    try:
        async def run_with_deadline():
            return await asyncio.wait_for(
                Runner.run(
                    agent,
                    runner_input,
                    context=runner_context,
                    max_turns=run.max_turns,
                    run_config=RunConfig(
                        tracing_disabled=True,
                        trace_include_sensitive_data=False,
                        workflow_name="Adaptive Tutor Agent V2",
                    ),
                ),
                timeout=max(1, int(settings.LEARNING_AGENT_TIMEOUT_SECONDS)),
            )

        try:
            # Agents SDK manages its own OpenAI calls, so reserve the same
            # configured route slot explicitly.  Do not hold the gateway's
            # global slot because agent tools may make nested gateway calls.
            with llm_gateway.route_slot("agent.tutor_v2", include_global=False):
                result = asyncio.run(run_with_deadline())
        except (TimeoutError, asyncio.TimeoutError) as exc:
            raise CapabilityError("agent_run_timeout") from exc
        raw_responses = list(result.raw_responses)
        prior_response_count = int(run.turn_count or 0)
        if checkpoint.get("sdk_state") and len(raw_responses) >= prior_response_count:
            new_responses = raw_responses[prior_response_count:]
        else:
            new_responses = raw_responses
        with transaction.atomic():
            locked = LearningAgentRun.objects.select_for_update().get(run_id=run.run_id)
            locked.turn_count = min(locked.max_turns, locked.turn_count + len(new_responses))
            locked.input_tokens += sum(
                int(getattr(getattr(response, "usage", None), "input_tokens", 0) or 0)
                for response in new_responses
            )
            locked.output_tokens += sum(
                int(getattr(getattr(response, "usage", None), "output_tokens", 0) or 0)
                for response in new_responses
            )
            locked.model_request_count += sum(
                int(getattr(getattr(response, "usage", None), "requests", 0) or 0)
                for response in new_responses
            )
            locked.elapsed_ms += max(0, int((time.monotonic() - segment_started) * 1000))
            locked.save(
                update_fields=[
                    "turn_count",
                    "input_tokens",
                    "output_tokens",
                    "model_request_count",
                    "elapsed_ms",
                    "updated_at",
                ]
            )
            segment_accounted = True
        if result.interruptions:
            if len(result.interruptions) != 1:
                raise CapabilityError("multiple_simultaneous_interruptions_blocked")
            item = result.interruptions[0]
            arguments = json.loads(item.arguments or "{}")
            kind, public_payload = _public_interruption_payload(item.name, arguments)
            sdk_state = result.to_state().to_json(strict_context=True)
            save_checkpoint(
                run,
                {
                    "kind": "interrupted",
                    "question": question,
                    "sdk_state": sdk_state,
                    "resolved_interruption_id": "",
                },
                sdk_schema_version=str(sdk_state.get("$schemaVersion") or "1"),
            )
            interruption, _created = AgentInterruption.objects.get_or_create(
                run=run,
                idempotency_key=f"sdk:{item.call_id}",
                defaults={
                    "kind": kind,
                    "status": AgentInterruption.STATUS_PENDING,
                    "tool_name": canonical_tool_name(item.name),
                    "sdk_call_id": item.call_id,
                    "public_payload": public_payload,
                    "expires_at": run.expires_at,
                },
            )
            run.status = (
                LearningAgentRun.STATUS_WAITING_CLARIFICATION
                if kind == AgentInterruption.KIND_CLARIFICATION
                else LearningAgentRun.STATUS_WAITING_APPROVAL
            )
            run.save(update_fields=["status", "updated_at"])
            append_run_event(
                run,
                "clarification_requested" if kind == AgentInterruption.KIND_CLARIFICATION else "approval_requested",
                {"interruption_id": interruption.interruption_id, **public_payload},
            )
            complete_execution_run(execution)
            return

        output = result.final_output_as(TutorTurnOutput, raise_if_incorrect_type=True)
        run.refresh_from_db()
        committed = commit_tutor_turn(run, question, output)
        with transaction.atomic():
            locked = LearningAgentRun.objects.select_for_update().get(run_id=run.run_id)
            if (
                locked.status == LearningAgentRun.STATUS_EXPIRED
                or locked.error_code == "agent_release_retired"
                or not locked.release.active
            ):
                raise CapabilityError("agent_release_retired")
            if locked.cancel_requested_at:
                raise CapabilityError("agent_run_cancelled")
            locked.status = LearningAgentRun.STATUS_COMPLETED
            locked.result_payload = committed
            locked.completed_at = timezone.now()
            locked.error_code = ""
            locked.save(update_fields=["status", "result_payload", "completed_at", "error_code", "updated_at"])
        try:
            admission = admit_micro_check(locked, output)
            if admission.accepted and admission.check:
                committed["micro_check"] = serialize_micro_check(admission.check)
                with transaction.atomic():
                    locked = LearningAgentRun.objects.select_for_update().get(run_id=run.run_id)
                    locked.result_payload = committed
                    locked.save(update_fields=["result_payload", "updated_at"])
        except Exception as micro_check_exc:
            logger.exception("Optional Micro Check was blocked after run %s", run.run_id)
            append_run_event(
                run,
                "micro_check_blocked",
                {"reason": _safe_error_code(micro_check_exc)},
            )
        try:
            from learning_apps.adaptive_learning.probe_offer_service import create_initial_calibration_offer

            locked.refresh_from_db()
            create_initial_calibration_offer(locked)
        except Exception as calibration_exc:
            logger.exception("Initial calibration Offer was blocked after run %s", run.run_id)
            append_run_event(
                run,
                "calibration_offer_blocked",
                {"reason": f"initial_calibration:{_safe_error_code(calibration_exc)}"},
            )
        append_run_event(run, "run_completed", {"history_id": committed["history_id"], "citation_count": len(committed["citations"])})
        delete_checkpoint(run)
        complete_execution_run(execution)
    except Exception as exc:
        logger.exception("Adaptive Tutor Agent V2 run %s failed", run.run_id)
        complete_execution_run(execution, failed=True)
        release_retired = False
        with transaction.atomic():
            locked = LearningAgentRun.objects.select_for_update().get(run_id=run.run_id)
            if not segment_accounted:
                locked.elapsed_ms += max(0, int((time.monotonic() - segment_started) * 1000))
            if (
                locked.status == LearningAgentRun.STATUS_EXPIRED
                and locked.error_code == "agent_release_retired"
            ):
                release_retired = True
            elif locked.cancel_requested_at:
                locked.status = LearningAgentRun.STATUS_CANCELLED
                locked.error_code = "cancelled"
            else:
                locked.status = LearningAgentRun.STATUS_FAILED
                locked.error_code = _safe_error_code(exc)
            if not locked.completed_at:
                locked.completed_at = timezone.now()
            locked.save(update_fields=["status", "error_code", "completed_at", "elapsed_ms", "updated_at"])
        if not release_retired:
            append_run_event(run, "run_failed", {"error_code": locked.error_code, "retryable": True})


def execute_agent_run(run_id: str) -> None:
    close_old_connections()
    try:
        try:
            _execute_agent_run_sync(run_id)
        except Exception as exc:
            # Initialization failures (checkpoint integrity, release/plugin
            # policy, SDK construction) occur before the inner Runner guard.
            # They must still terminate the persistent run deterministically.
            logger.exception("Adaptive Tutor Agent V2 run %s failed during initialization", run_id)
            now = timezone.now()
            failed_run = None
            with transaction.atomic():
                run = LearningAgentRun.objects.select_for_update().filter(run_id=run_id).first()
                if run and run.status not in LearningAgentRun.TERMINAL_STATUSES:
                    run.status = (
                        LearningAgentRun.STATUS_CANCELLED
                        if run.cancel_requested_at
                        else LearningAgentRun.STATUS_FAILED
                    )
                    run.error_code = "cancelled" if run.cancel_requested_at else _safe_error_code(exc)
                    run.completed_at = now
                    run.save(update_fields=["status", "error_code", "completed_at", "updated_at"])
                    failed_run = run
            from learning_apps.application.models import ExecutionRun

            ExecutionRun.objects.filter(
                agent_run_id=run_id,
                status=ExecutionRun.STATUS_RUNNING,
            ).update(status=ExecutionRun.STATUS_FAILED, completed_at=now)
            if failed_run:
                append_run_event(
                    failed_run,
                    "run_failed",
                    {"error_code": failed_run.error_code, "retryable": False},
                )
    finally:
        close_old_connections()
