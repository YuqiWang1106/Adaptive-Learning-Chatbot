from __future__ import annotations

from typing import Any, Mapping

from learning_apps.application.contracts import CapabilityEntrypoint

from .base import execute_capability


def run_celery_workflow(
    capability_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    username: str = "",
    learning_goal_id: int | None = None,
    metadata: Mapping[str, Any] | None = None,
    idempotency_key: str,
) -> dict[str, Any]:
    return execute_capability(
        capability_name,
        arguments,
        username=username,
        learning_goal_id=learning_goal_id,
        entrypoint=CapabilityEntrypoint.CELERY,
        workflow=capability_name,
        metadata=metadata,
        idempotency_key=idempotency_key,
    )
