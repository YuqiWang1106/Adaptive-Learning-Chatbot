from __future__ import annotations

import uuid
from typing import Any, Mapping

from learning_apps.infrastructure.services.trace_context import current_trace_context, normalize_trace_id
from learning_apps.persistence.models import LearningGoal, UploadedLearningMaterial, UserProfile

from ..capabilities.catalog import capability_registry
from ..contracts import (
    CapabilityContext,
    CapabilityEntrypoint,
    CapabilityError,
    CapabilityScope,
    stable_sha256,
)
from ..executor import CapabilityExecutor, complete_execution_run, create_execution_run


class ProductWorkflowError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


def execute_capability(
    capability_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    username: str = "",
    learning_goal_id: int | None = None,
    entrypoint: CapabilityEntrypoint = CapabilityEntrypoint.HTTP,
    workflow: str = "",
    metadata: Mapping[str, Any] | None = None,
    idempotency_key: str = "",
) -> dict[str, Any]:
    registry = capability_registry()
    spec = registry.get(capability_name)
    if entrypoint not in spec.allowed_entrypoints:
        raise ProductWorkflowError("entrypoint_not_allowed")

    user = None
    goal = None
    if spec.required_scope is not CapabilityScope.SYSTEM:
        user = UserProfile.objects.filter(username=username).first()
        if not user:
            raise ProductWorkflowError("user_scope_fenced")
    if not learning_goal_id and user and metadata and metadata.get("material_id"):
        learning_goal_id = (
            UploadedLearningMaterial.objects.filter(id=int(metadata["material_id"]), user=user)
            .values_list("learning_goal_id", flat=True)
            .first()
        )
    if learning_goal_id:
        goal = LearningGoal.objects.filter(id=int(learning_goal_id), user=user).first()

    trace = current_trace_context()
    trace_id = normalize_trace_id(trace.trace_id)
    safe_arguments = dict(arguments or {})
    request_sha256 = stable_sha256(
        {
            "capability": capability_name,
            "arguments": safe_arguments,
            "scope": {"username": username, "learning_goal_id": int(learning_goal_id or 0)},
            "metadata_keys": sorted(str(key) for key in (metadata or {}).keys()),
        }
    )
    execution = create_execution_run(
        user=user,
        goal=goal,
        conversation=None,
        entrypoint=entrypoint,
        workflow=workflow or capability_name,
        trace_id=trace_id,
    )
    context = CapabilityContext(
        execution_id=execution.execution_id,
        trace_id=trace_id,
        entrypoint=entrypoint,
        request_sha256=request_sha256,
        scope=spec.required_scope,
        user_id=user.user_id if user else 0,
        username=user.username if user else "",
        learning_goal_id=goal.id if goal else int(learning_goal_id or 0),
        metadata=dict(metadata or {}),
    )
    invocation_key = str(idempotency_key or "").strip()[:96] or (
        f"{entrypoint.value}:{capability_name}:{uuid.uuid4().hex}"[:96]
    )
    try:
        result = CapabilityExecutor(registry).execute(
            capability_name,
            safe_arguments,
            context=context,
            idempotency_key=invocation_key,
        )
    except CapabilityError as exc:
        complete_execution_run(execution, failed=True)
        raise ProductWorkflowError(exc.code, exc.message) from exc
    except Exception:
        complete_execution_run(execution, failed=True)
        raise
    complete_execution_run(execution)
    return result.payload
