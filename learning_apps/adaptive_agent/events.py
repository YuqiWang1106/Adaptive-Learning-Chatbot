from __future__ import annotations

from django.db import transaction

from .models import AgentRunEvent, LearningAgentRun


def append_run_event(run: LearningAgentRun, event_type: str, payload: dict | None = None) -> AgentRunEvent:
    if event_type not in AgentRunEvent.EVENT_TYPES:
        raise ValueError("unsupported_agent_event_type")
    with transaction.atomic():
        locked = LearningAgentRun.objects.select_for_update().get(run_id=run.run_id)
        last = locked.events.order_by("-sequence").values_list("sequence", flat=True).first() or 0
        return AgentRunEvent.objects.create(
            run=locked,
            sequence=last + 1,
            event_type=event_type,
            public_payload=payload or {},
        )
