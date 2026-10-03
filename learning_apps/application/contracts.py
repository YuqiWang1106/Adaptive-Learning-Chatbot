from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Mapping, Type

from pydantic import BaseModel, ConfigDict
from learning_apps.infrastructure.services.stable_hash import stable_sha256 as stable_sha256


class CapabilityAuthority(str, Enum):
    READ = "read"
    PROPOSE = "propose"
    COMMAND = "command"
    ADMIN = "admin"


class CapabilityEntrypoint(str, Enum):
    HTTP = "http"
    AGENT = "agent"
    CELERY = "celery"
    MCP = "mcp"


class CapabilityScope(str, Enum):
    """Minimum server-owned scope required before a capability can run."""

    SYSTEM = "system"
    USER = "user"
    GOAL = "goal"
    CONVERSATION = "conversation"


class EmptyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnyOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


@dataclass(frozen=True)
class CapabilityContext:
    """Server-owned scope. No field in this object is model-controlled."""

    execution_id: str
    trace_id: str
    entrypoint: CapabilityEntrypoint
    request_sha256: str
    scope: CapabilityScope = CapabilityScope.CONVERSATION
    user_id: int = 0
    username: str = ""
    learning_goal_id: int = 0
    conversation_key: str = ""
    conversation_generation: int = 0
    trusted_user_text: str = ""
    agent_run_id: str = ""
    metadata: Mapping[str, Any] = field(default_factory=dict)


CapabilityHandler = Callable[[CapabilityContext, BaseModel], Mapping[str, Any] | BaseModel]


@dataclass(frozen=True)
class CapabilitySpec:
    name: str
    version: str
    description: str
    authority: CapabilityAuthority
    input_model: Type[BaseModel]
    output_model: Type[BaseModel] = AnyOutput
    handler: CapabilityHandler | None = None
    allowed_entrypoints: frozenset[CapabilityEntrypoint] = frozenset(CapabilityEntrypoint)
    agent_visible: bool = False
    requires_approval: bool = False
    timeout_seconds: float = 8.0
    display_name: str = ""
    evidence_category: str = ""
    required_scope: CapabilityScope = CapabilityScope.CONVERSATION
    persist_output_payload: bool = True
    retry_failed_invocations: bool = False

    def public_tool_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()


@dataclass(frozen=True)
class CapabilityResult:
    capability_name: str
    capability_version: str
    status: str
    payload: dict[str, Any]
    reason_code: str
    invocation_id: str
    replayed: bool = False


class CapabilityError(RuntimeError):
    def __init__(self, code: str, message: str = ""):
        super().__init__(f"{code}: {message}" if message else code)
        self.code = code
        self.message = message


SERVER_SCOPE_ARGUMENTS = frozenset(
    {
        "user",
        "user_id",
        "username",
        "learning_goal",
        "learning_goal_id",
        "goal_id",
        "conversation",
        "conversation_key",
        "conversation_generation",
        "trace_id",
        "agent_run_id",
        "authority",
    }
)
