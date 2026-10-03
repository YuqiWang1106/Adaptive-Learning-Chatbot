from __future__ import annotations

from typing import Any

from learning_apps.chat.services.page_service import ChatAccessResult
from learning_apps.chat.services.conversation_repository_service import ConversationClearResult, ConversationScopeError
from learning_apps.chat.services.learner_memory_policy_service import LearnerMemoryDecision, LearnerMemoryDecisionStatus
from learning_apps.chat.services.learner_memory_repository_service import LearnerMemoryScopeError

from .base import ProductWorkflowError, execute_capability


def resolve_chat_access(username: str, learning_goal_id: int | None) -> ChatAccessResult:
    payload = execute_capability(
        "conversation.resolve_access",
        username=username,
        workflow="conversation.access",
        metadata={"requested_learning_goal_id": int(learning_goal_id or 0)},
    )
    return ChatAccessResult(**payload)


def build_chat_page_context(username: str, learning_goal_id: int, goal: dict[str, Any]) -> dict[str, Any]:
    del goal
    return execute_capability(
        "conversation.page_context",
        username=username,
        learning_goal_id=learning_goal_id,
        workflow="conversation.page",
    )


def load_chat_history_page(username: str, learning_goal_id: int, before_cursor: str | None) -> dict[str, Any]:
    return execute_capability(
        "conversation.history",
        {"before_cursor": str(before_cursor or "")},
        username=username,
        learning_goal_id=learning_goal_id,
        workflow="conversation.history",
    )


def _memory_decision(payload: dict[str, Any]) -> LearnerMemoryDecision:
    data = dict(payload)
    data["status"] = LearnerMemoryDecisionStatus(data["status"])
    data["conflict_ids"] = tuple(data.get("conflict_ids") or ())
    return LearnerMemoryDecision(**data)


def list_learner_memories(username: str, learning_goal_id: int) -> list[dict[str, Any]]:
    try:
        return list(
            execute_capability(
                "memory.list_all",
                username=username,
                learning_goal_id=learning_goal_id,
                workflow="memory.list",
            ).get("memories", [])
        )
    except ProductWorkflowError as exc:
        if exc.code in {"goal_scope_fenced", "learner_memory_scope_error"}:
            raise LearnerMemoryScopeError(exc.code) from exc
        raise


def propose_learner_memory(**kwargs) -> LearnerMemoryDecision:
    username = str(kwargs.pop("username"))
    goal_id = int(kwargs.pop("learning_goal_id"))
    try:
        payload = execute_capability(
            "memory.propose_direct",
            {
                "kind": kwargs.get("kind") or "",
                "memory_key": kwargs.get("memory_key") or "",
                "value_payload": kwargs.get("value_payload") or {},
                "source": kwargs.get("source") or "explicit_user_statement",
                "idempotency_key": kwargs.get("idempotency_key") or "",
            },
            username=username,
            learning_goal_id=goal_id,
            workflow="memory.propose",
        )
    except ProductWorkflowError as exc:
        if exc.code in {"goal_scope_fenced", "learner_memory_scope_error"}:
            raise LearnerMemoryScopeError(exc.code) from exc
        raise
    return _memory_decision(payload["decision"])


def confirm_learner_memory(
    username: str,
    learning_goal_id: int,
    memory_id: str,
    *,
    idempotency_key: str | None = None,
) -> LearnerMemoryDecision:
    try:
        payload = execute_capability(
            "memory.confirm_direct",
            {"memory_id": memory_id, "idempotency_key": idempotency_key or ""},
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="memory.confirm",
        )
    except ProductWorkflowError as exc:
        if exc.code in {"goal_scope_fenced", "learner_memory_scope_error"}:
            raise LearnerMemoryScopeError(exc.code) from exc
        raise
    return _memory_decision(payload["decision"])


def revoke_learner_memory(
    username: str,
    learning_goal_id: int,
    memory_id: str,
    *,
    idempotency_key: str | None = None,
) -> LearnerMemoryDecision:
    try:
        payload = execute_capability(
            "memory.revoke_direct",
            {"memory_id": memory_id, "idempotency_key": idempotency_key or ""},
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="memory.revoke",
        )
    except ProductWorkflowError as exc:
        if exc.code in {"goal_scope_fenced", "learner_memory_scope_error"}:
            raise LearnerMemoryScopeError(exc.code) from exc
        raise
    return _memory_decision(payload["decision"])


def clear_conversation(username: str, learning_goal_id: int) -> ConversationClearResult:
    try:
        payload = execute_capability(
            "conversation.clear",
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="conversation.clear",
        )
    except ProductWorkflowError as exc:
        if exc.code in {"goal_scope_fenced", "conversation_scope_error"}:
            raise ConversationScopeError(exc.code) from exc
        raise
    return ConversationClearResult(conversation_key="", **payload)
