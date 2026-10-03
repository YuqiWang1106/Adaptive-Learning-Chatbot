from __future__ import annotations

import hashlib
import json
import secrets
from datetime import timedelta
from typing import Any

from django.utils import timezone

from learning_apps.chat.services.conversation_repository_service import ensure_active_conversation
from learning_apps.persistence.models import LearningGoal, UserProfile

from learning_apps.adaptive_agent.tool_catalog import capability_registry
from learning_apps.application.contracts import CapabilityAuthority, CapabilityContext, CapabilityEntrypoint, CapabilityError, stable_sha256
from learning_apps.application.executor import CapabilityExecutor, complete_execution_run, create_execution_run
from .models import MCPAccessGrant, PluginRegistration
from .plugins import ensure_builtin_plugins


MCP_SERVER_NAME = "adaptive-learning"
MCP_PROTOCOL_VERSION = "2025-11-25"


def issue_mcp_token(
    *,
    username: str,
    learning_goal_id: int,
    audience: str = MCP_SERVER_NAME,
    allowed_tools: list[str] | None = None,
    ttl_seconds: int = 900,
    plugin_id: str = "curriculum_materials",
) -> tuple[MCPAccessGrant, str]:
    ensure_builtin_plugins()
    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(id=learning_goal_id, user=user).first() if user else None
    plugin = PluginRegistration.objects.filter(
        plugin_id=plugin_id,
        status=PluginRegistration.STATUS_ACTIVE,
    ).order_by("-installed_at").first()
    if not user or not goal or not plugin:
        raise ValueError("mcp_scope_or_plugin_not_found")
    conversation = ensure_active_conversation(username, learning_goal_id)
    registry = capability_registry()
    manifest = plugin.manifest if isinstance(plugin.manifest, dict) else {}
    if MCP_SERVER_NAME not in set(manifest.get("mcp_servers") or []):
        raise ValueError("plugin_does_not_expose_adaptive_learning_mcp")
    plugin_tools = {str(name) for name in manifest.get("tools", [])}
    requested = allowed_tools or sorted(plugin_tools)
    safe_tools = []
    for name in requested:
        if name not in plugin_tools:
            raise ValueError("mcp_tool_not_declared_by_plugin")
        spec = registry.get(name)
        if spec.authority is not CapabilityAuthority.READ:
            raise ValueError("mcp_default_grants_are_read_only")
        safe_tools.append(name)
    raw_token = "learning_mcp_" + secrets.token_urlsafe(36)
    grant = MCPAccessGrant.objects.create(
        user=user,
        learning_goal=goal,
        conversation=conversation,
        plugin=plugin,
        audience=audience,
        token_sha256=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        allowed_tools=sorted(set(safe_tools)),
        expires_at=timezone.now() + timedelta(seconds=max(60, min(ttl_seconds, 3600))),
    )
    return grant, raw_token


def authenticate_mcp_token(raw_token: str, *, audience: str = MCP_SERVER_NAME) -> MCPAccessGrant:
    digest = hashlib.sha256(str(raw_token or "").encode("utf-8")).hexdigest()
    grant = MCPAccessGrant.objects.select_related(
        "user", "learning_goal", "conversation", "plugin"
    ).filter(token_sha256=digest, audience=audience).first()
    if (
        not grant
        or grant.revoked_at
        or grant.expires_at <= timezone.now()
        or grant.plugin.status != PluginRegistration.STATUS_ACTIVE
        or grant.conversation.lifecycle != grant.conversation.LIFECYCLE_ACTIVE
    ):
        raise CapabilityError("mcp_token_invalid")
    return grant


def process_mcp_request(grant: MCPAccessGrant, payload: dict[str, Any]) -> dict[str, Any]:
    request_id = payload.get("id")
    method = str(payload.get("method") or "")

    def response(result=None, error=None):
        body = {"jsonrpc": "2.0", "id": request_id}
        if error is not None:
            body["error"] = error
        else:
            body["result"] = result
        return body

    if payload.get("jsonrpc") != "2.0":
        return response(error={"code": -32600, "message": "Invalid Request"})
    if method == "initialize":
        return response(
            {
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "serverInfo": {"name": MCP_SERVER_NAME, "version": "2.0.0"},
                "capabilities": {"tools": {"listChanged": False}},
            }
        )
    registry = capability_registry()
    allowed = set(grant.allowed_tools or [])
    if method == "tools/list":
        tools = []
        for name in sorted(allowed):
            spec = registry.get(name)
            tools.append(
                {
                    "name": spec.name,
                    "description": spec.description,
                    "inputSchema": spec.public_tool_schema(),
                    "annotations": {"readOnlyHint": spec.authority is CapabilityAuthority.READ},
                }
            )
        return response({"tools": tools})
    if method != "tools/call":
        return response(error={"code": -32601, "message": "Method not found"})
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    name = str(params.get("name") or "")
    if name not in allowed:
        return response(error={"code": -32003, "message": "Tool not allowed"})
    spec = registry.get(name)
    if spec.authority is not CapabilityAuthority.READ:
        return response(error={"code": -32003, "message": "Write authority not granted"})
    execution = create_execution_run(
        user=grant.user,
        goal=grant.learning_goal,
        conversation=grant.conversation,
        entrypoint=CapabilityEntrypoint.MCP,
        workflow="private_mcp_tool_call",
        trace_id=secrets.token_hex(16),
    )
    context = CapabilityContext(
        execution_id=execution.execution_id,
        user_id=grant.user_id,
        username=grant.user.username,
        learning_goal_id=grant.learning_goal_id,
        conversation_key=grant.conversation_id,
        conversation_generation=grant.conversation.generation,
        trace_id=execution.trace_id,
        entrypoint=CapabilityEntrypoint.MCP,
        request_sha256=stable_sha256(payload),
    )
    try:
        result = CapabilityExecutor(registry).execute(
            name,
            params.get("arguments") if isinstance(params.get("arguments"), dict) else {},
            context=context,
            idempotency_key=f"mcp:{grant.grant_id}:{request_id}"[:96],
        )
        complete_execution_run(execution)
        return response(
            {
                "content": [{"type": "text", "text": json.dumps(result.payload, ensure_ascii=False)}],
                "isError": False,
            }
        )
    except CapabilityError as exc:
        complete_execution_run(execution, failed=True)
        return response(
            {
                "content": [{"type": "text", "text": json.dumps({"error": exc.code})}],
                "isError": True,
            }
        )
