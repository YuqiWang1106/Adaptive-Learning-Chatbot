from __future__ import annotations

import uuid

from django.core.exceptions import ValidationError
from django.db import models

from learning_apps.persistence.models import LearningConversation, LearningGoal, UserProfile


def _opaque(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def generate_run_id() -> str:
    return _opaque("lar")


def generate_interruption_id() -> str:
    return _opaque("int")


def generate_execution_id() -> str:
    return _opaque("exec")


def generate_invocation_id() -> str:
    return _opaque("cinv")


def generate_outbox_id() -> str:
    return _opaque("out")


def generate_proposal_id() -> str:
    return _opaque("lap")


def generate_release_id() -> str:
    return _opaque("rel")


def generate_mcp_grant_id() -> str:
    return _opaque("mcp")


def generate_micro_check_id() -> str:
    return _opaque("amc")


class AgentReleaseManifest(models.Model):
    """Immutable identity for model, prompt, tool and policy versions."""

    release_id = models.CharField(max_length=48, primary_key=True, default=generate_release_id, editable=False)
    release_name = models.CharField(max_length=96)
    model = models.CharField(max_length=96)
    reasoning_effort = models.CharField(max_length=16, default="medium")
    prompt_version = models.CharField(max_length=48)
    runtime_version = models.CharField(max_length=48)
    capability_catalog_version = models.CharField(max_length=48)
    manifest = models.JSONField(default=dict)
    manifest_sha256 = models.CharField(max_length=64, unique=True)
    active = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_v2_release_manifests"
        indexes = [models.Index(fields=["active", "created_at"], name="idx_av2_release_active")]


class LearningAgentRun(models.Model):
    ORIGIN_NORMAL = "normal"
    ORIGIN_MICRO_CHECK_RESPONSE = "micro_check_response"
    ORIGIN_CHOICES = [
        (ORIGIN_NORMAL, ORIGIN_NORMAL),
        (ORIGIN_MICRO_CHECK_RESPONSE, ORIGIN_MICRO_CHECK_RESPONSE),
    ]
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_WAITING_CLARIFICATION = "waiting_for_clarification"
    STATUS_WAITING_APPROVAL = "waiting_for_approval"
    STATUS_RESUMING = "resuming"
    STATUS_COMPLETED = "completed"
    STATUS_FAILED = "failed"
    STATUS_CANCELLED = "cancelled"
    STATUS_EXPIRED = "expired"
    TERMINAL_STATUSES = {STATUS_COMPLETED, STATUS_FAILED, STATUS_CANCELLED, STATUS_EXPIRED}
    STATUS_CHOICES = [(value, value) for value in (
        STATUS_QUEUED,
        STATUS_RUNNING,
        STATUS_WAITING_CLARIFICATION,
        STATUS_WAITING_APPROVAL,
        STATUS_RESUMING,
        STATUS_COMPLETED,
        STATUS_FAILED,
        STATUS_CANCELLED,
        STATUS_EXPIRED,
    )]

    run_id = models.CharField(max_length=48, primary_key=True, default=generate_run_id, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="learning_agent_runs")
    learning_goal = models.ForeignKey(LearningGoal, on_delete=models.CASCADE, related_name="learning_agent_runs")
    conversation = models.ForeignKey(
        LearningConversation,
        on_delete=models.PROTECT,
        related_name="learning_agent_runs",
    )
    conversation_generation = models.PositiveIntegerField()
    release = models.ForeignKey(AgentReleaseManifest, on_delete=models.PROTECT, related_name="runs")
    origin = models.CharField(max_length=32, choices=ORIGIN_CHOICES, default=ORIGIN_NORMAL)
    origin_reference = models.CharField(max_length=64, blank=True, default="")
    status = models.CharField(max_length=40, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    idempotency_key = models.CharField(max_length=96)
    request_sha256 = models.CharField(max_length=64)
    trace_id = models.CharField(max_length=64, db_index=True)
    model = models.CharField(max_length=96)
    reasoning_effort = models.CharField(max_length=16, default="medium")
    max_turns = models.PositiveSmallIntegerField(default=5)
    max_tool_calls = models.PositiveSmallIntegerField(default=6)
    max_skills = models.PositiveSmallIntegerField(default=2)
    turn_count = models.PositiveSmallIntegerField(default=0)
    tool_call_count = models.PositiveSmallIntegerField(default=0)
    skill_count = models.PositiveSmallIntegerField(default=0)
    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    model_request_count = models.PositiveSmallIntegerField(default=0)
    elapsed_ms = models.PositiveIntegerField(default=0)
    plan = models.JSONField(default=list, blank=True)
    selected_skills = models.JSONField(default=list, blank=True)
    evidence_manifest = models.JSONField(default=list, blank=True)
    adaptive_context_manifest = models.JSONField(default=dict, blank=True)
    conversation_memory_manifest = models.JSONField(default=dict, blank=True)
    result_payload = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    cancel_requested_at = models.DateTimeField(null=True, blank=True)
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    mastery_write_authorized = models.BooleanField(default=False, editable=False)

    class Meta:
        db_table = "learning_agent_runs_v2"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "learning_goal", "idempotency_key"],
                name="uix_av2_run_scope_idem",
            ),
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_av2_run_no_mastery",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "created_at"], name="idx_av2_run_scope"),
            models.Index(fields=["status", "expires_at"], name="idx_av2_run_status"),
        ]

    def clean(self):
        super().clean()
        if self.learning_goal_id and self.learning_goal.user_id != self.user_id:
            raise ValidationError("Agent run user and learning goal do not match.")
        if self.conversation_id and (
            self.conversation.user_id != self.user_id
            or self.conversation.learning_goal_id != self.learning_goal_id
            or self.conversation.generation != self.conversation_generation
        ):
            raise ValidationError("Agent run conversation scope does not match.")
        if self.mastery_write_authorized:
            raise ValidationError("Agent runs cannot authorize mastery writes.")


class AgentRunEvent(models.Model):
    EVENT_TYPES = (
        "run_started",
        "adaptive_context_loaded",
        "memory_loaded",
        "plan_updated",
        "skill_selected",
        "tool_requested",
        "tool_started",
        "tool_completed",
        "evidence_attached",
        "clarification_requested",
        "approval_requested",
        "approval_resolved",
        "answer_streaming",
        "micro_check_offered",
        "micro_check_blocked",
        "micro_check_answered",
        "micro_check_skipped",
        "micro_check_superseded",
        "calibration_offer_blocked",
        "run_completed",
        "run_failed",
    )

    run = models.ForeignKey(LearningAgentRun, on_delete=models.CASCADE, related_name="events")
    sequence = models.PositiveIntegerField()
    event_type = models.CharField(max_length=40, choices=[(value, value) for value in EVENT_TYPES])
    public_payload = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_run_events_v2"
        constraints = [
            models.UniqueConstraint(fields=["run", "sequence"], name="uix_av2_event_sequence"),
        ]
        indexes = [models.Index(fields=["run", "sequence"], name="idx_av2_event_run_seq")]


class AgentRunCheckpoint(models.Model):
    run = models.OneToOneField(LearningAgentRun, on_delete=models.CASCADE, related_name="checkpoint")
    encrypted_state = models.TextField()
    state_sha256 = models.CharField(max_length=64)
    sdk_schema_version = models.CharField(max_length=24, default="1")
    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_run_checkpoints_v2"
        indexes = [models.Index(fields=["expires_at"], name="idx_av2_checkpoint_exp")]


class AgentConversationMemory(models.Model):
    """Encrypted, replay-safe teaching memory for one conversation generation."""

    conversation = models.OneToOneField(
        LearningConversation,
        on_delete=models.CASCADE,
        primary_key=True,
        related_name="agent_conversation_memory",
    )
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="agent_conversation_memories",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="agent_conversation_memories",
    )
    conversation_generation = models.PositiveIntegerField()
    policy_version = models.CharField(max_length=64)
    encrypted_state = models.TextField()
    state_sha256 = models.CharField(max_length=64)
    turn_count = models.PositiveIntegerField(default=0)
    compacted_turn_count = models.PositiveIntegerField(default=0)
    last_history_id = models.PositiveBigIntegerField(default=0)
    mastery_write_authorized = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "agent_conversation_memories_v2"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "learning_goal", "conversation_generation"],
                name="uix_av2_memory_scope_generation",
            ),
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_av2_memory_no_mastery",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "learning_goal", "updated_at"],
                name="idx_av2_memory_scope",
            ),
        ]

    def clean(self):
        super().clean()
        if self.conversation_id and (
            self.conversation.user_id != self.user_id
            or self.conversation.learning_goal_id != self.learning_goal_id
            or self.conversation.generation != self.conversation_generation
        ):
            raise ValidationError("Agent conversation memory scope does not match.")
        if self.mastery_write_authorized:
            raise ValidationError("Agent conversation memory cannot authorize mastery writes.")


class AgentMicroCheck(models.Model):
    KIND_NEAR_TRANSFER = "near_transfer"
    KIND_ONE_STEP = "one_step"
    KIND_CHOICE_WITH_REASON = "choice_with_reason"
    KIND_ERROR_DIAGNOSIS = "error_diagnosis"
    KIND_SHORT_RECALL = "short_recall"
    KIND_CHOICES = [(value, value) for value in (
        KIND_NEAR_TRANSFER,
        KIND_ONE_STEP,
        KIND_CHOICE_WITH_REASON,
        KIND_ERROR_DIAGNOSIS,
        KIND_SHORT_RECALL,
    )]

    STATUS_OFFERED = "offered"
    STATUS_ANSWER_SUBMITTED = "answer_submitted"
    STATUS_EVALUATING = "evaluating"
    STATUS_ANSWERED = "answered"
    STATUS_SKIPPED = "skipped"
    STATUS_SUPERSEDED = "superseded"
    STATUS_EXPIRED = "expired"
    STATUS_CHOICES = [(value, value) for value in (
        STATUS_OFFERED,
        STATUS_ANSWER_SUBMITTED,
        STATUS_EVALUATING,
        STATUS_ANSWERED,
        STATUS_SKIPPED,
        STATUS_SUPERSEDED,
        STATUS_EXPIRED,
    )]

    RESPONSE_SHORT_TEXT = "short_text"
    RESPONSE_SINGLE_CHOICE = "single_choice"
    RESPONSE_CHOICES = [
        (RESPONSE_SHORT_TEXT, RESPONSE_SHORT_TEXT),
        (RESPONSE_SINGLE_CHOICE, RESPONSE_SINGLE_CHOICE),
    ]

    check_id = models.CharField(max_length=48, primary_key=True, default=generate_micro_check_id, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="agent_micro_checks")
    learning_goal = models.ForeignKey(LearningGoal, on_delete=models.CASCADE, related_name="agent_micro_checks")
    conversation = models.ForeignKey(
        LearningConversation,
        on_delete=models.CASCADE,
        related_name="agent_micro_checks",
    )
    conversation_generation = models.PositiveIntegerField()
    originating_run = models.OneToOneField(
        LearningAgentRun,
        on_delete=models.CASCADE,
        related_name="micro_check",
    )
    originating_skill_id = models.CharField(max_length=64)
    originating_skill_version = models.CharField(max_length=32)
    kind = models.CharField(max_length=32, choices=KIND_CHOICES)
    prompt = models.TextField()
    options = models.JSONField(default=list, blank=True)
    response_format = models.CharField(max_length=24, choices=RESPONSE_CHOICES)
    concept_key = models.CharField(max_length=160)
    target_dimension = models.CharField(max_length=32)
    encrypted_rubric = models.TextField()
    rubric_sha256 = models.CharField(max_length=64)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_OFFERED)
    open_scope_key = models.CharField(max_length=160, unique=True, null=True, blank=True)
    encrypted_submitted_response = models.TextField(blank=True, default="")
    submitted_response_sha256 = models.CharField(max_length=64, blank=True, default="")
    evaluation_outcome = models.CharField(max_length=24, blank=True, default="")
    evaluation_confidence = models.FloatField(default=0.0)
    evaluation_version = models.CharField(max_length=64, blank=True, default="")
    evaluation_payload = models.JSONField(default=dict, blank=True)
    response_run = models.OneToOneField(
        LearningAgentRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="micro_check_response_for",
    )
    offered_at = models.DateTimeField(auto_now_add=True)
    answered_at = models.DateTimeField(null=True, blank=True)
    skipped_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    superseded_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField()
    mastery_write_authorized = models.BooleanField(default=False, editable=False)

    class Meta:
        db_table = "agent_micro_checks_v2"
        constraints = [
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_amc_no_mastery",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "status"], name="idx_amc_scope_status"),
            models.Index(fields=["conversation", "conversation_generation", "offered_at"], name="idx_amc_conv_generation"),
        ]

    def clean(self):
        super().clean()
        if self.learning_goal_id and self.learning_goal.user_id != self.user_id:
            raise ValidationError("Micro Check goal scope does not match.")
        if self.conversation_id and (
            self.conversation.user_id != self.user_id
            or self.conversation.learning_goal_id != self.learning_goal_id
            or self.conversation.generation != self.conversation_generation
        ):
            raise ValidationError("Micro Check conversation scope does not match.")
        if self.originating_run_id and (
            self.originating_run.user_id != self.user_id
            or self.originating_run.learning_goal_id != self.learning_goal_id
            or self.originating_run.conversation_id != self.conversation_id
        ):
            raise ValidationError("Micro Check originating run scope does not match.")
        if self.mastery_write_authorized:
            raise ValidationError("Micro Checks cannot authorize mastery writes.")


class AgentInterruption(models.Model):
    KIND_CLARIFICATION = "clarification"
    KIND_APPROVAL = "approval"
    KIND_CHOICES = [(KIND_CLARIFICATION, KIND_CLARIFICATION), (KIND_APPROVAL, KIND_APPROVAL)]
    STATUS_PENDING = "pending"
    STATUS_ANSWERED = "answered"
    STATUS_APPROVED = "approved"
    STATUS_REJECTED = "rejected"
    STATUS_EXPIRED = "expired"
    STATUS_CANCELLED = "cancelled"
    STATUS_CHOICES = [(value, value) for value in (
        STATUS_PENDING,
        STATUS_ANSWERED,
        STATUS_APPROVED,
        STATUS_REJECTED,
        STATUS_EXPIRED,
        STATUS_CANCELLED,
    )]

    interruption_id = models.CharField(
        max_length=48,
        primary_key=True,
        default=generate_interruption_id,
        editable=False,
    )
    run = models.ForeignKey(LearningAgentRun, on_delete=models.CASCADE, related_name="interruptions")
    kind = models.CharField(max_length=24, choices=KIND_CHOICES)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PENDING)
    tool_name = models.CharField(max_length=96, blank=True, default="")
    sdk_call_id = models.CharField(max_length=128, blank=True, default="")
    public_payload = models.JSONField(default=dict)
    encrypted_response = models.TextField(blank=True, default="")
    response_sha256 = models.CharField(max_length=64, blank=True, default="")
    idempotency_key = models.CharField(max_length=96)
    expires_at = models.DateTimeField()
    resolved_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_interruptions_v2"
        constraints = [
            models.UniqueConstraint(fields=["run", "idempotency_key"], name="uix_av2_interrupt_idem"),
        ]
        indexes = [models.Index(fields=["run", "status", "expires_at"], name="idx_av2_interrupt_run")]


class PluginRegistration(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_DISABLED = "disabled"
    STATUS_RETIRED = "retired"
    STATUS_CHOICES = [(value, value) for value in (STATUS_ACTIVE, STATUS_DISABLED, STATUS_RETIRED)]

    plugin_id = models.CharField(max_length=96)
    version = models.CharField(max_length=32)
    manifest = models.JSONField(default=dict)
    manifest_sha256 = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    installed_at = models.DateTimeField(auto_now_add=True)
    disabled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "agent_plugins_v2"
        constraints = [
            models.UniqueConstraint(fields=["plugin_id", "version"], name="uix_av2_plugin_version"),
        ]
        indexes = [models.Index(fields=["plugin_id", "status"], name="idx_av2_plugin_status")]


class MCPAccessGrant(models.Model):
    grant_id = models.CharField(max_length=48, primary_key=True, default=generate_mcp_grant_id, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="agent_mcp_grants")
    learning_goal = models.ForeignKey(LearningGoal, on_delete=models.CASCADE, related_name="agent_mcp_grants")
    conversation = models.ForeignKey(LearningConversation, on_delete=models.CASCADE, related_name="agent_mcp_grants")
    plugin = models.ForeignKey(PluginRegistration, on_delete=models.CASCADE, related_name="mcp_grants")
    audience = models.CharField(max_length=96)
    token_sha256 = models.CharField(max_length=64, unique=True)
    allowed_tools = models.JSONField(default=list)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "agent_mcp_grants_v2"
        indexes = [
            models.Index(fields=["audience", "expires_at"], name="idx_av2_mcp_audience"),
            models.Index(fields=["user", "learning_goal", "revoked_at"], name="idx_av2_mcp_scope"),
        ]
