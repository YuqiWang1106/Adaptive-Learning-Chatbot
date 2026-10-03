"""Agent reactions to events owned by other product domains."""

from learning_apps.chat.domain_events import conversation_fenced

from .run_service import expire_runs_for_conversation


def expire_agent_runs_for_fenced_conversation(sender, *, conversation_key: str, **kwargs) -> None:
    del sender, kwargs
    expire_runs_for_conversation(conversation_key)


def connect_domain_event_handlers() -> None:
    conversation_fenced.connect(
        expire_agent_runs_for_fenced_conversation,
        dispatch_uid="adaptive_agent.expire_runs_for_fenced_conversation",
        weak=False,
    )


__all__ = ["connect_domain_event_handlers", "expire_agent_runs_for_fenced_conversation"]
