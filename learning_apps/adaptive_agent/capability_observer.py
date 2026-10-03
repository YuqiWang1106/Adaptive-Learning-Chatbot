"""Agent-specific policy and public events for capability execution."""

from __future__ import annotations

from django.db import transaction

from learning_apps.application.contracts import CapabilityContext, CapabilityError

from .events import append_run_event
from .models import LearningAgentRun


class AgentCapabilityObserver:
    def before_tool(self, context: CapabilityContext, spec_name: str, display_name: str) -> None:
        if not context.agent_run_id:
            raise CapabilityError("agent_run_required")
        with transaction.atomic():
            run = LearningAgentRun.objects.select_for_update().get(run_id=context.agent_run_id)
            if run.status not in {LearningAgentRun.STATUS_RUNNING, LearningAgentRun.STATUS_RESUMING}:
                raise CapabilityError("agent_run_not_executable")
            if run.cancel_requested_at:
                raise CapabilityError("agent_run_cancelled")
            if run.tool_call_count >= run.max_tool_calls:
                raise CapabilityError("tool_budget_exhausted")
            run.tool_call_count += 1
            run.save(update_fields=["tool_call_count", "updated_at"])
        append_run_event(run, "tool_requested", {"tool": spec_name})
        append_run_event(run, "tool_started", {"tool": spec_name, "display_name": display_name})

    def after_tool(
        self,
        context: CapabilityContext,
        spec_name: str,
        status: str,
        reason_code: str,
        latency_ms: int,
        evidence_category: str,
    ) -> None:
        if not context.agent_run_id:
            return
        run = LearningAgentRun.objects.get(run_id=context.agent_run_id)
        append_run_event(
            run,
            "tool_completed",
            {
                "tool": spec_name,
                "status": status,
                "reason_code": reason_code,
                "latency_ms": latency_ms,
            },
        )
        if evidence_category:
            append_run_event(run, "evidence_attached", {"category": evidence_category, "tool": spec_name})
