from __future__ import annotations

from .accounts import UserProfile
from .base import generate_learning_material_source_key, learning_material_upload_path, models
from .learning import LearningGoal

class UploadedLearningMaterial(models.Model):
    STATUS_UPLOADED = "uploaded"
    STATUS_QUEUED = "queued"
    STATUS_PROCESSING = "processing"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"
    STATUS_DELETING = "deleting"
    STATUS_DELETE_FAILED = "delete_failed"
    STATUS_DELETED = "deleted"

    STATUS_CHOICES = [
        (STATUS_UPLOADED, "uploaded"),
        (STATUS_QUEUED, "queued"),
        (STATUS_PROCESSING, "processing"),
        (STATUS_READY, "ready"),
        (STATUS_FAILED, "failed"),
        (STATUS_DELETING, "deleting"),
        (STATUS_DELETE_FAILED, "delete_failed"),
        (STATUS_DELETED, "deleted"),
    ]

    CHUNK_STATUS_NOT_REQUESTED = "not_requested"
    CHUNK_STATUS_QUEUED = "queued"
    CHUNK_STATUS_PROCESSING = "processing"
    CHUNK_STATUS_READY = "ready"
    CHUNK_STATUS_FAILED = "failed"
    CHUNK_STATUS_LEGACY_UNVERIFIED = "legacy_unverified"
    CHUNK_STATUS_CHOICES = [
        (CHUNK_STATUS_NOT_REQUESTED, "not_requested"),
        (CHUNK_STATUS_QUEUED, "queued"),
        (CHUNK_STATUS_PROCESSING, "processing"),
        (CHUNK_STATUS_READY, "ready"),
        (CHUNK_STATUS_FAILED, "failed"),
        (CHUNK_STATUS_LEGACY_UNVERIFIED, "legacy_unverified"),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="uploaded_learning_materials",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="uploaded_materials",
        db_column="learning_goal_id",
    )
    original_filename = models.CharField(max_length=255)
    file_extension = models.CharField(max_length=16)
    content_type = models.CharField(max_length=128, blank=True, default="")
    file_size = models.PositiveIntegerField(default=0)
    source_key = models.CharField(
        max_length=64,
        default=generate_learning_material_source_key,
        db_index=True,
    )
    source_version = models.PositiveIntegerField(default=1)
    content_sha256 = models.CharField(max_length=64, null=True, blank=True)
    active_content_sha256 = models.CharField(max_length=64, null=True, blank=True)
    content_signature = models.CharField(max_length=64, blank=True, default="")
    chunk_status = models.CharField(
        max_length=32,
        choices=CHUNK_STATUS_CHOICES,
        default=CHUNK_STATUS_NOT_REQUESTED,
    )
    chunk_error_code = models.CharField(max_length=96, blank=True, default="")
    chunk_transform_version = models.CharField(max_length=64, blank=True, default="")
    chunks_built_at = models.DateTimeField(null=True, blank=True)
    source_file = models.FileField(
        upload_to=learning_material_upload_path,
        max_length=500,
        blank=True,
    )
    ingestion_job_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    ingestion_attempts = models.PositiveSmallIntegerField(default=0)
    processing_started_at = models.DateTimeField(null=True, blank=True)
    openai_file_id = models.CharField(max_length=128, blank=True, default="")
    vector_store_id = models.CharField(max_length=128, blank=True, default="")
    vector_store_batch_id = models.CharField(max_length=128, blank=True, default="")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_UPLOADED)
    error_message = models.TextField(blank=True, default="")
    deletion_job_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    provider_cleanup_pending = models.BooleanField(default=False)
    cleanup_attempts = models.PositiveSmallIntegerField(default=0)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "uploaded_learning_materials"
        indexes = [
            models.Index(fields=["learning_goal", "status"], name="idx_ulm_goal_status"),
            models.Index(fields=["user", "created_at"], name="idx_ulm_user_created"),
            models.Index(fields=["learning_goal", "source_key"], name="idx_ulm_goal_source"),
            models.Index(fields=["status", "updated_at"], name="idx_ulm_status_updated"),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=["user", "learning_goal", "active_content_sha256"],
                name="uix_ulm_user_goal_active_sha",
            ),
            models.UniqueConstraint(
                fields=["learning_goal", "source_key", "source_version"],
                name="uix_ulm_goal_source_version",
            ),
            models.CheckConstraint(
                check=models.Q(source_version__gte=1),
                name="chk_ulm_source_version_gte_1",
            ),
        ]

class KnowledgeChunk(models.Model):
    LIFECYCLE_ACTIVE = "active"
    LIFECYCLE_SUPERSEDED = "superseded"
    LIFECYCLE_DELETED = "deleted"
    LIFECYCLE_CHOICES = [
        (LIFECYCLE_ACTIVE, "active"),
        (LIFECYCLE_SUPERSEDED, "superseded"),
        (LIFECYCLE_DELETED, "deleted"),
    ]

    LOCATOR_LINE = "line"
    LOCATOR_PARAGRAPH = "paragraph"
    LOCATOR_SLIDE = "slide"
    LOCATOR_PAGE = "page"
    LOCATOR_CHOICES = [
        (LOCATOR_LINE, "line"),
        (LOCATOR_PARAGRAPH, "paragraph"),
        (LOCATOR_SLIDE, "slide"),
        (LOCATOR_PAGE, "page"),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="knowledge_chunks",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="knowledge_chunks",
        db_column="learning_goal_id",
    )
    material = models.ForeignKey(
        UploadedLearningMaterial,
        on_delete=models.CASCADE,
        related_name="knowledge_chunks",
        db_column="material_id",
    )
    source_key = models.CharField(max_length=64)
    source_version = models.PositiveIntegerField()
    source_content_sha256 = models.CharField(max_length=64)
    chunk_id = models.CharField(max_length=64, unique=True)
    chunk_index = models.PositiveIntegerField()
    content = models.TextField()
    content_sha256 = models.CharField(max_length=64)
    transform_version = models.CharField(max_length=64)
    locator = models.CharField(max_length=128)
    locator_type = models.CharField(max_length=24, choices=LOCATOR_CHOICES)
    locator_start = models.PositiveIntegerField()
    locator_end = models.PositiveIntegerField()
    locator_part = models.PositiveIntegerField(default=1)
    lifecycle = models.CharField(
        max_length=24,
        choices=LIFECYCLE_CHOICES,
        default=LIFECYCLE_ACTIVE,
    )
    trust_level = models.CharField(max_length=48, default="untrusted_retrieved_content")
    superseded_at = models.DateTimeField(null=True, blank=True)
    deleted_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "knowledge_chunks"
        constraints = [
            models.UniqueConstraint(
                fields=["material", "transform_version", "chunk_index"],
                name="uix_kchunk_material_tx_idx",
            ),
            models.CheckConstraint(
                check=models.Q(locator_start__gte=1),
                name="chk_kchunk_locator_start",
            ),
            models.CheckConstraint(
                check=models.Q(locator_end__gte=1),
                name="chk_kchunk_locator_end",
            ),
            models.CheckConstraint(
                check=models.Q(locator_part__gte=1),
                name="chk_kchunk_locator_part",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "learning_goal", "lifecycle"],
                name="idx_kchunk_scope_lifecycle",
            ),
            models.Index(
                fields=["material", "lifecycle"],
                name="idx_kchunk_material_life",
            ),
            models.Index(
                fields=["source_key", "source_version"],
                name="idx_kchunk_source_version",
            ),
        ]

class RetrievalDecision(models.Model):
    STATUS_ACCEPTED = "accepted"
    STATUS_ABSTAINED = "abstained"
    STATUS_FAILED = "failed"
    STATUS_CHOICES = [
        (STATUS_ACCEPTED, "accepted"),
        (STATUS_ABSTAINED, "abstained"),
        (STATUS_FAILED, "failed"),
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
        related_name="retrieval_decisions",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="retrieval_decisions",
        db_column="learning_goal_id",
    )
    query_sha256 = models.CharField(max_length=64)
    scope_sha256 = models.CharField(max_length=64)
    purpose = models.CharField(max_length=64, default="general")
    request_sha256 = models.CharField(max_length=64, blank=True, default="")
    taxonomy_sha256 = models.CharField(max_length=64, blank=True, default="")
    lifecycle_bundle_sha256 = models.CharField(max_length=64, blank=True, default="")
    selected_bundle_sha256 = models.CharField(max_length=64, blank=True, default="")
    idempotency_key = models.CharField(
        max_length=64,
        null=True,
        blank=True,
        default=None,
        unique=True,
    )
    policy_version = models.CharField(max_length=64)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES)
    reason_code = models.CharField(max_length=96)
    failure_code = models.CharField(max_length=96, blank=True, default="")
    candidate_count = models.PositiveIntegerField(default=0)
    candidate_set_sha256 = models.CharField(max_length=64)
    selected_chunk_hashes = models.JSONField(default=list, blank=True)
    evidence_count = models.PositiveIntegerField(default=0)
    mastery_write_authorized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "retrieval_decisions"
        constraints = [
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_retrieval_no_mastery",
            ),
        ]
        indexes = [
            models.Index(
                fields=["user", "learning_goal", "created_at"],
                name="idx_retrieval_scope_created",
            ),
            models.Index(
                fields=["query_sha256", "created_at"],
                name="idx_retrieval_query_created",
            ),
            models.Index(fields=["status", "created_at"], name="idx_retrieval_status"),
            models.Index(
                fields=["user", "learning_goal", "purpose", "request_sha256"],
                name="idx_retrieval_scope_req",
            ),
        ]

class RetrievalDecisionEvidence(models.Model):
    """Immutable hash snapshot connecting a retrieval decision to one DB chunk."""

    retrieval_decision = models.ForeignKey(
        RetrievalDecision,
        on_delete=models.CASCADE,
        related_name="evidence_rows",
        db_column="retrieval_decision_id",
    )
    knowledge_chunk = models.ForeignKey(
        KnowledgeChunk,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="retrieval_evidence_rows",
        db_column="knowledge_chunk_id",
    )
    evidence_ref = models.CharField(max_length=40)
    rank = models.PositiveIntegerField()
    scores = models.JSONField(default=dict, blank=True)
    snapshot_sha256 = models.CharField(max_length=64)
    mastery_write_authorized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "retrieval_decision_evidence"
        constraints = [
            models.UniqueConstraint(
                fields=["retrieval_decision", "evidence_ref"],
                name="uix_rde_decision_ref",
            ),
            models.UniqueConstraint(
                fields=["retrieval_decision", "rank"],
                name="uix_rde_decision_rank",
            ),
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_rde_no_mastery",
            ),
        ]
        indexes = [
            models.Index(fields=["retrieval_decision", "rank"], name="idx_rde_decision_rank"),
            models.Index(fields=["knowledge_chunk"], name="idx_rde_chunk"),
        ]
