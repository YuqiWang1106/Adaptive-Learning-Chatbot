"""Approved Agent proposal persistence.

The Tool Catalog declares model-visible capabilities. This service owns their
scope checks and persistence so the catalog remains a declarative adapter.
"""

from __future__ import annotations

from django.utils import timezone

from learning_apps.application.contracts import stable_sha256
from learning_apps.application.models import LearningActionProposal
from learning_apps.chat.services.learner_memory_policy_service import (
    LearnerMemoryKind,
    LearnerMemorySource,
)
from learning_apps.chat.services.learner_memory_repository_service import (
    confirm_learner_memory,
    propose_learner_memory,
)

from .models import LearningAgentRun


QUIZ_PROPOSAL = LearningActionProposal.TYPE_QUIZ
REVIEW_PLAN_PROPOSAL = LearningActionProposal.TYPE_REVIEW_PLAN
STUDY_SESSION_PROPOSAL = LearningActionProposal.TYPE_EXTERNAL_STUDY_SESSION


class AgentActionProposalError(ValueError):
    pass


def save_explicit_memory(
    *,
    username: str,
    learning_goal_id: int,
    request_sha256: str,
    trusted_user_text: str,
    memory_key: str,
    value: str,
) -> dict:
    if value.casefold() not in trusted_user_text.casefold():
        raise AgentActionProposalError("memory_value_not_grounded_in_user_text")
    kind = (
        LearnerMemoryKind.LEARNING_STRATEGY_PREFERENCE
        if memory_key == "learning_strategy"
        else LearnerMemoryKind.EXPLICIT_PREFERENCE
    )
    decision = propose_learner_memory(
        username=username,
        learning_goal_id=learning_goal_id,
        kind=kind,
        memory_key=memory_key,
        value_payload={"value": value},
        source=LearnerMemorySource.EXPLICIT_USER_STATEMENT,
        idempotency_key=f"agent-v2:{request_sha256}:{memory_key}",
    )
    if not decision.accepted or not decision.memory_id:
        raise AgentActionProposalError("memory_proposal_blocked")
    confirmed = confirm_learner_memory(
        username=username,
        learning_goal_id=learning_goal_id,
        memory_id=decision.memory_id,
        idempotency_key=f"agent-v2-approved:{request_sha256}:{memory_key}",
    )
    if not confirmed.accepted:
        raise AgentActionProposalError("memory_confirmation_blocked")
    return {
        "status": "executed",
        "memory_id": confirmed.memory_id,
        "lifecycle": confirmed.lifecycle,
    }


def create_approved_draft(
    *,
    agent_run_id: str,
    user_id: int,
    learning_goal_id: int,
    conversation_key: str,
    request_sha256: str,
    proposal_type: str,
    payload: dict,
) -> dict:
    if not agent_run_id:
        raise AgentActionProposalError("proposal_requires_agent_run")
    if proposal_type not in {
        QUIZ_PROPOSAL,
        REVIEW_PLAN_PROPOSAL,
        STUDY_SESSION_PROPOSAL,
    }:
        raise AgentActionProposalError("proposal_type_not_allowed")

    run = LearningAgentRun.objects.select_related("conversation").filter(
        run_id=agent_run_id,
        user_id=user_id,
        learning_goal_id=learning_goal_id,
        conversation_id=conversation_key,
    ).first()
    if run is None:
        raise AgentActionProposalError("proposal_scope_fenced")

    idempotency_key = stable_sha256(
        {
            "run": agent_run_id,
            "request": request_sha256,
            "proposal_type": proposal_type,
            "payload": payload,
        }
    )[:96]
    proposal, created = LearningActionProposal.objects.get_or_create(
        idempotency_key=idempotency_key,
        defaults={
            "user_id": user_id,
            "learning_goal_id": learning_goal_id,
            "conversation_id": conversation_key,
            "agent_run": run,
            "proposal_type": proposal_type,
            "status": LearningActionProposal.STATUS_APPROVED_DRAFT,
            "payload": payload,
            "approved_at": timezone.now(),
        },
    )
    return {
        "status": "approved_draft_created",
        "proposal_id": proposal.proposal_id,
        "proposal_type": proposal.proposal_type,
        "created": created,
        "execution_scope": "proposal_only",
        "external_action_executed": False,
    }
