from __future__ import annotations

from .accounts import UserProfile
from .base import generate_learning_material_source_key, models
from .knowledge import RetrievalDecision, RetrievalDecisionEvidence
from .learning import LearningGoal

class LearningConversation(models.Model):
    """Provider-independent, owner/goal-scoped conversation generation."""

    LIFECYCLE_ACTIVE = "active"
    LIFECYCLE_TOMBSTONED = "tombstoned"
    LIFECYCLE_PURGED = "purged"
    LIFECYCLE_DELETED = "deleted"
    LIFECYCLE_CHOICES = [
        (LIFECYCLE_ACTIVE, "active"),
        (LIFECYCLE_TOMBSTONED, "tombstoned"),
        (LIFECYCLE_PURGED, "purged"),
        (LIFECYCLE_DELETED, "deleted"),
    ]

    CLEANUP_NOT_REQUIRED = "not_required"
    CLEANUP_PENDING = "pending"
    CLEANUP_RETRYING = "retrying"
    CLEANUP_SUCCEEDED = "succeeded"
    CLEANUP_ABANDONED = "abandoned"
    CLEANUP_STATUS_CHOICES = [
        (CLEANUP_NOT_REQUIRED, "not_required"),
        (CLEANUP_PENDING, "pending"),
        (CLEANUP_RETRYING, "retrying"),
        (CLEANUP_SUCCEEDED, "succeeded"),
        (CLEANUP_ABANDONED, "abandoned"),
    ]

    conversation_key = models.CharField(
        max_length=64,
        primary_key=True,
        default=generate_learning_material_source_key,
        editable=False,
    )
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.PROTECT,
        related_name="learning_conversations",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.PROTECT,
        related_name="conversation_generations",
        db_column="learning_goal_id",
    )
    generation = models.PositiveIntegerField()
    # Only the active generation carries this key. Nullable + unique works on
    # MySQL/PostgreSQL/SQLite and permits any number of tombstoned generations.
    active_scope_key = models.CharField(max_length=96, null=True, blank=True, unique=True)
    lifecycle = models.CharField(max_length=24, choices=LIFECYCLE_CHOICES, default=LIFECYCLE_ACTIVE)
    provider_conversation_id = models.CharField(max_length=128, null=True, blank=True, unique=True)
    provider_cleanup_status = models.CharField(
        max_length=24,
        choices=CLEANUP_STATUS_CHOICES,
        default=CLEANUP_NOT_REQUIRED,
    )
    provider_cleanup_attempts = models.PositiveSmallIntegerField(default=0)
    provider_cleanup_error = models.CharField(max_length=160, blank=True, default="")
    cleanup_lease_token = models.CharField(max_length=64, blank=True, default="")
    cleanup_lease_expires_at = models.DateTimeField(null=True, blank=True)
    cleanup_next_attempt_at = models.DateTimeField(null=True, blank=True)
    policy_version = models.CharField(max_length=64, default="p2.4-conversation-v1")
    retention_expires_at = models.DateTimeField(null=True, blank=True)
    tombstoned_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    last_activity_at = models.DateTimeField(auto_now_add=True)
    mastery_write_authorized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learning_conversations"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "learning_goal", "generation"],
                name="uix_conv_scope_generation",
            ),
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_conv_no_mastery",
            ),
            models.CheckConstraint(
                check=(
                    (
                        models.Q(lifecycle="active", active_scope_key__isnull=False)
                        & ~models.Q(active_scope_key="")
                    )
                    | (
                        ~models.Q(lifecycle="active")
                        & models.Q(active_scope_key__isnull=True)
                    )
                ),
                name="chk_conv_active_scope_key",
            ),
            models.CheckConstraint(
                check=(
                    ~models.Q(lifecycle__in=["purged", "deleted"])
                    | models.Q(provider_conversation_id__isnull=True)
                ),
                name="chk_conv_purged_provider",
            ),
            models.CheckConstraint(
                check=(
                    ~models.Q(lifecycle__in=["active", "tombstoned"])
                    | models.Q(retention_expires_at__isnull=False)
                ),
                name="chk_conv_retention_required",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "lifecycle"], name="idx_conv_scope_lifecycle"),
            models.Index(fields=["provider_cleanup_status", "cleanup_next_attempt_at"], name="idx_conv_cleanup_due"),
            models.Index(fields=["retention_expires_at"], name="idx_conv_retention"),
        ]

class UserHistory(models.Model):
    SCOPE_ACTIVE = "active"
    SCOPE_TOMBSTONED = "tombstoned"
    SCOPE_STATUS_CHOICES = [
        (SCOPE_ACTIVE, "active"),
        (SCOPE_TOMBSTONED, "tombstoned"),
    ]

    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="histories", db_column="user_id")
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="history_entries",
        db_column="learning_goal_id",
    )
    conversation = models.ForeignKey(
        "LearningConversation",
        on_delete=models.CASCADE,
        related_name="history_entries",
        db_column="conversation_key",
    )
    event_key = models.CharField(max_length=96, unique=True)
    scope_status = models.CharField(
        max_length=24,
        choices=SCOPE_STATUS_CHOICES,
        default=SCOPE_ACTIVE,
    )
    question = models.TextField()
    answer = models.TextField()
    answer_style = models.CharField(max_length=50, default="informational")
    processing_status = models.CharField(max_length=32, default="succeeded")
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "user_histories"
        constraints = [
            models.CheckConstraint(
                check=(
                    ~models.Q(scope_status="active")
                    | (
                        models.Q(conversation__isnull=False)
                        & models.Q(learning_goal__isnull=False)
                        & models.Q(event_key__isnull=False)
                        & ~models.Q(event_key="")
                    )
                ),
                name="chk_hist_active_scope",
            ),
        ]
        indexes = [
            models.Index(fields=["timestamp"], name="idx_user_histories_timestamp"),
            models.Index(fields=["answer_style"], name="idx_hist_ans_style"),
            models.Index(fields=["conversation", "scope_status", "timestamp"], name="idx_hist_conv_scope_time"),
        ]

class LearnerMemoryRecord(models.Model):
    KIND_EXPLICIT_PREFERENCE = "explicit_preference"
    KIND_GOAL_CONSTRAINT = "goal_constraint"
    KIND_LEARNING_STRATEGY_PREFERENCE = "learning_strategy_preference"
    KIND_CHOICES = [
        (KIND_EXPLICIT_PREFERENCE, "explicit_preference"),
        (KIND_GOAL_CONSTRAINT, "goal_constraint"),
        (KIND_LEARNING_STRATEGY_PREFERENCE, "learning_strategy_preference"),
    ]

    LIFECYCLE_PROPOSED = "proposed"
    LIFECYCLE_ACTIVE = "active"
    LIFECYCLE_CONFLICTED = "conflicted"
    LIFECYCLE_REVOKED = "revoked"
    LIFECYCLE_EXPIRED = "expired"
    LIFECYCLE_CHOICES = [
        (LIFECYCLE_PROPOSED, "proposed"),
        (LIFECYCLE_ACTIVE, "active"),
        (LIFECYCLE_CONFLICTED, "conflicted"),
        (LIFECYCLE_REVOKED, "revoked"),
        (LIFECYCLE_EXPIRED, "expired"),
    ]

    memory_id = models.CharField(max_length=40, primary_key=True)
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="learner_memories",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="learner_memories",
        db_column="learning_goal_id",
    )
    kind = models.CharField(max_length=48, choices=KIND_CHOICES)
    memory_key = models.CharField(max_length=64)
    value_payload = models.JSONField()
    value_hash = models.CharField(max_length=64)
    scope_fingerprint = models.CharField(max_length=64)
    active_scope_key = models.CharField(max_length=96, null=True, blank=True, unique=True)
    lifecycle = models.CharField(max_length=24, choices=LIFECYCLE_CHOICES)
    conflict_memory_ids = models.JSONField(default=list, blank=True)
    source = models.CharField(max_length=48)
    policy_version = models.CharField(max_length=64)
    expires_at = models.DateTimeField()
    confirmed_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    mastery_write_authorized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learner_memory_records"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "learning_goal", "kind", "memory_key", "value_hash"],
                name="uix_lmr_scope_identity",
            ),
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_lmr_no_mastery",
            ),
            models.CheckConstraint(
                check=(
                    (
                        models.Q(lifecycle="active", active_scope_key__isnull=False)
                        & ~models.Q(active_scope_key="")
                    )
                    | (
                        ~models.Q(lifecycle="active")
                        & models.Q(active_scope_key__isnull=True)
                    )
                ),
                name="chk_lmr_active_scope_key",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "lifecycle"], name="idx_lmr_scope_lifecycle"),
            models.Index(fields=["lifecycle", "expires_at"], name="idx_lmr_expiry"),
            models.Index(fields=["user", "learning_goal", "memory_key"], name="idx_lmr_scope_key"),
        ]

class LearnerMemoryDecisionRecord(models.Model):
    STATUS_ACCEPTED = "accepted"
    STATUS_BLOCKED = "blocked"
    STATUS_CHOICES = [
        (STATUS_ACCEPTED, "accepted"),
        (STATUS_BLOCKED, "blocked"),
    ]

    decision_id = models.CharField(
        max_length=64,
        primary_key=True,
        default=generate_learning_material_source_key,
        editable=False,
    )
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="learner_memory_decisions",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="learner_memory_decisions",
        db_column="learning_goal_id",
    )
    memory = models.ForeignKey(
        LearnerMemoryRecord,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="decision_records",
        db_column="memory_id",
    )
    event_key = models.CharField(max_length=96, null=True, blank=True, unique=True)
    idempotency_key = models.CharField(max_length=64, unique=True)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES)
    reason_code = models.CharField(max_length=96)
    requested_lifecycle = models.CharField(max_length=24)
    source = models.CharField(max_length=48)
    policy_version = models.CharField(max_length=64)
    request_hash = models.CharField(max_length=64)
    decision_hash = models.CharField(max_length=64)
    decision_metadata = models.JSONField(default=dict, blank=True)
    mastery_write_authorized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learner_memory_decision_records"
        constraints = [
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_lmdr_no_mastery",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "created_at"], name="idx_lmdr_scope_time"),
            models.Index(fields=["status", "created_at"], name="idx_lmdr_status_time"),
        ]

class AnswerGroundingDecision(models.Model):
    STATUS_ACCEPTED = "accepted"
    STATUS_BLOCKED = "blocked"
    STATUS_NOT_REQUIRED = "not_required"
    STATUS_CHOICES = [
        (STATUS_ACCEPTED, "accepted"),
        (STATUS_BLOCKED, "blocked"),
        (STATUS_NOT_REQUIRED, "not_required"),
    ]

    decision_id = models.CharField(
        max_length=64,
        primary_key=True,
        default=generate_learning_material_source_key,
        editable=False,
    )
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="answer_grounding_decisions",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="answer_grounding_decisions",
        db_column="learning_goal_id",
    )
    user_history = models.OneToOneField(
        UserHistory,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="grounding_decision",
        db_column="user_history_id",
    )
    retrieval_decision = models.ForeignKey(
        RetrievalDecision,
        on_delete=models.PROTECT,
        related_name="answer_grounding_decisions",
        db_column="retrieval_decision_id",
    )
    status = models.CharField(max_length=24, choices=STATUS_CHOICES)
    reason_code = models.CharField(max_length=96)
    policy_version = models.CharField(max_length=64)
    answer_sha256 = models.CharField(max_length=64)
    claim_set_sha256 = models.CharField(max_length=64)
    support_decision_sha256 = models.CharField(max_length=64)
    scope_sha256 = models.CharField(max_length=64)
    taxonomy_sha256 = models.CharField(max_length=64)
    lifecycle_bundle_sha256 = models.CharField(max_length=64)
    answer_model = models.CharField(max_length=96, blank=True, default="")
    prompt_version = models.CharField(max_length=64)
    verifier_model = models.CharField(max_length=96, blank=True, default="")
    verifier_prompt_version = models.CharField(max_length=64)
    idempotency_key = models.CharField(max_length=64, unique=True)
    mastery_write_authorized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "answer_grounding_decisions"
        constraints = [
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_agd_no_mastery",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "created_at"], name="idx_agd_scope_time"),
            models.Index(fields=["status", "created_at"], name="idx_agd_status_time"),
        ]

class AnswerCitation(models.Model):
    RELATION_SUPPORTED = "supported"
    RELATION_CONTRADICTED = "contradicted"
    RELATION_INSUFFICIENT = "insufficient"
    RELATION_CHOICES = [
        (RELATION_SUPPORTED, "supported"),
        (RELATION_CONTRADICTED, "contradicted"),
        (RELATION_INSUFFICIENT, "insufficient"),
    ]

    citation_id = models.CharField(
        max_length=64,
        primary_key=True,
        default=generate_learning_material_source_key,
        editable=False,
    )
    grounding_decision = models.ForeignKey(
        AnswerGroundingDecision,
        on_delete=models.CASCADE,
        related_name="citations",
        db_column="grounding_decision_id",
    )
    retrieval_evidence = models.ForeignKey(
        RetrievalDecisionEvidence,
        on_delete=models.PROTECT,
        related_name="answer_citations",
        db_column="retrieval_evidence_id",
    )
    claim_ordinal = models.PositiveIntegerField()
    claim_sha256 = models.CharField(max_length=64)
    answer_start = models.PositiveIntegerField(null=True, blank=True)
    answer_end = models.PositiveIntegerField(null=True, blank=True)
    relation = models.CharField(max_length=24, choices=RELATION_CHOICES)
    confidence_band = models.CharField(max_length=16)
    support_quote_sha256 = models.CharField(max_length=64)
    verifier_version = models.CharField(max_length=64)
    mastery_write_authorized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "answer_citations"
        constraints = [
            models.UniqueConstraint(
                fields=["grounding_decision", "claim_ordinal", "retrieval_evidence"],
                name="uix_acite_decision_claim_evidence",
            ),
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_acite_no_mastery",
            ),
        ]
        indexes = [
            models.Index(fields=["grounding_decision", "claim_ordinal"], name="idx_acite_decision_claim"),
        ]
