from __future__ import annotations

from .base import models
from .conversation import LearningConversation
from .learning import LearningGoal

class AIJob(models.Model):
    STATUS_QUEUED = "queued"
    STATUS_RUNNING = "running"
    STATUS_SUCCEEDED = "succeeded"
    STATUS_RETRYING = "retrying"
    STATUS_FAILED = "failed"
    STATUS_DEGRADED = "degraded"

    STATUS_CHOICES = [
        (STATUS_QUEUED, "queued"),
        (STATUS_RUNNING, "running"),
        (STATUS_SUCCEEDED, "succeeded"),
        (STATUS_RETRYING, "retrying"),
        (STATUS_FAILED, "failed"),
        (STATUS_DEGRADED, "degraded"),
    ]

    job_id = models.CharField(max_length=64, primary_key=True)
    task_type = models.CharField(max_length=64)
    username = models.CharField(max_length=50, db_index=True)
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="ai_jobs",
        db_column="learning_goal_id",
    )
    conversation = models.ForeignKey(
        LearningConversation,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="ai_jobs",
        db_column="conversation_key",
    )
    idempotency_key = models.CharField(max_length=160, db_index=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_QUEUED)
    stage = models.CharField(max_length=64, blank=True, default="queued")
    percent = models.PositiveSmallIntegerField(default=0)
    message = models.TextField(blank=True, default="")
    result = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=96, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    retry_count = models.PositiveSmallIntegerField(default=0)
    max_retries = models.PositiveSmallIntegerField(default=3)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "ai_jobs"
        constraints = [
            models.UniqueConstraint(
                fields=["task_type", "username", "idempotency_key"],
                name="uix_ai_jobs_task_user_idem",
            ),
        ]
        indexes = [
            models.Index(fields=["task_type", "status"], name="idx_ai_jobs_type_status"),
            models.Index(fields=["created_at"], name="idx_ai_jobs_created"),
        ]

class LLMRequestLog(models.Model):
    STATUS_OK = "ok"
    STATUS_RETRYABLE = "retryable"
    STATUS_RATE_LIMITED = "rate_limited"
    STATUS_TIMEOUT = "timeout"
    STATUS_PROVIDER_ERROR = "provider_error"

    route = models.CharField(max_length=96, db_index=True)
    provider = models.CharField(max_length=32, default="openai")
    model = models.CharField(max_length=96, blank=True, default="")
    job_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    status = models.CharField(max_length=32, db_index=True)
    error_code = models.CharField(max_length=96, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    latency_ms = models.PositiveIntegerField(default=0)
    prompt_tokens = models.PositiveIntegerField(default=0)
    completion_tokens = models.PositiveIntegerField(default=0)
    total_tokens = models.PositiveIntegerField(default=0)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "llm_request_logs"
        indexes = [
            models.Index(fields=["route", "created_at"], name="idx_llm_route_created"),
            models.Index(fields=["status", "created_at"], name="idx_llm_status_created"),
        ]
