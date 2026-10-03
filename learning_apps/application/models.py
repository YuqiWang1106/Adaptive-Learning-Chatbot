"""Persistence owned by the application orchestration layer."""

from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from learning_apps.persistence.models import LearningConversation, LearningGoal, UserProfile


def _opaque(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def generate_execution_id() -> str:
    return _opaque("exec")


def generate_invocation_id() -> str:
    return _opaque("cinv")


def generate_proposal_id() -> str:
    return _opaque("lap")


class ExecutionRun(models.Model):
    STATUS_RUNNING = "running"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(value, value) for value in (STATUS_RUNNING, STATUS_COMPLETED, STATUS_FAILED)]

    execution_id = models.CharField(max_length=48, primary_key=True, default=generate_execution_id, editable=False)
    agent_run = models.ForeignKey(
        "adaptive_agent.LearningAgentRun",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="executions",
    )
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="capability_executions",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="capability_executions",
    )
    conversation = models.ForeignKey(
        LearningConversation,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="capability_executions",
    )
    entrypoint = models.CharField(max_length=24)
    workflow = models.CharField(max_length=96)
    trace_id = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_RUNNING)
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "capability_execution_runs_v2"
        indexes = [models.Index(fields=["user", "learning_goal", "started_at"], name="idx_cap_exec_scope")]


class CapabilityInvocation(models.Model):
    STATUS_PENDING = "pending"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_BLOCKED = "blocked"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [(value, value) for value in (STATUS_PENDING, STATUS_SUCCEEDED, STATUS_BLOCKED, STATUS_FAILED)]

    invocation_id = models.CharField(
        max_length=48,
        primary_key=True,
        default=generate_invocation_id,
        editable=False,
    )
    execution = models.ForeignKey(ExecutionRun, on_delete=models.CASCADE, related_name="invocations")
    capability_name = models.CharField(max_length=96)
    capability_version = models.CharField(max_length=32)
    authority = models.CharField(max_length=24)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES)
    idempotency_key = models.CharField(max_length=96)
    input_sha256 = models.CharField(max_length=64)
    output_sha256 = models.CharField(max_length=64)
    output_payload = models.JSONField(default=dict, blank=True)
    reason_code = models.CharField(max_length=64, blank=True, default="")
    latency_ms = models.PositiveIntegerField(default=0)
    attempt_count = models.PositiveSmallIntegerField(default=1)
    lease_expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    mastery_write_authorized = models.BooleanField(default=False, editable=False)

    class Meta:
        db_table = "capability_invocations_v2"
        constraints = [
            models.UniqueConstraint(
                fields=["capability_name", "idempotency_key"],
                name="uix_cap_invocation_idem",
            ),
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_cap_inv_no_mastery",
            ),
        ]
        indexes = [models.Index(fields=["capability_name", "status", "created_at"], name="idx_cap_inv_status")]


class LearningActionProposal(models.Model):
    """An approved draft; activation remains a deterministic workflow."""

    TYPE_QUIZ = "quiz"
    TYPE_PROBE = "probe"
    TYPE_REVIEW_PLAN = "review_plan"
    TYPE_EXTERNAL_STUDY_SESSION = "external_study_session"
    TYPE_CHOICES = [(value, value) for value in (
        TYPE_QUIZ,
        TYPE_PROBE,
        TYPE_REVIEW_PLAN,
        TYPE_EXTERNAL_STUDY_SESSION,
    )]

    STATUS_APPROVED_DRAFT = "approved_draft"
    STATUS_ACTIVATED = "activated"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [(value, value) for value in (
        STATUS_APPROVED_DRAFT,
        STATUS_ACTIVATED,
        STATUS_CANCELLED,
        STATUS_EXPIRED,
    )]

    proposal_id = models.CharField(max_length=48, primary_key=True, default=generate_proposal_id, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="learning_action_proposals")
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="learning_action_proposals",
    )
    conversation = models.ForeignKey(
        LearningConversation,
        on_delete=models.PROTECT,
        related_name="learning_action_proposals",
    )
    agent_run = models.ForeignKey(
        "adaptive_agent.LearningAgentRun",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="action_proposals",
    )
    proposal_type = models.CharField(max_length=40, choices=TYPE_CHOICES)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_APPROVED_DRAFT)
    payload = models.JSONField(default=dict)
    idempotency_key = models.CharField(max_length=96, unique=True)
    approved_at = models.DateTimeField()
    external_action_executed = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learning_action_proposals_v2"
        constraints = [
            models.CheckConstraint(
                check=models.Q(external_action_executed=False),
                name="chk_lap_no_external_execute",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "learning_goal", "status", "created_at"],
                name="idx_lap_scope_status",
            ),
        ]

    def clean(self):
        super().clean()
        if self.learning_goal_id and self.learning_goal.user_id != self.user_id:
            raise ValidationError("Proposal user and learning goal do not match.")
        if self.conversation_id and (
            self.conversation.user_id != self.user_id
            or self.conversation.learning_goal_id != self.learning_goal_id
        ):
            raise ValidationError("Proposal conversation scope does not match.")
        if self.agent_run_id and (
            self.agent_run.user_id != self.user_id
            or self.agent_run.learning_goal_id != self.learning_goal_id
            or self.agent_run.conversation_id != self.conversation_id
        ):
            raise ValidationError("Proposal run scope does not match.")
        if self.external_action_executed:
            raise ValidationError("Proposal records cannot execute external actions.")
