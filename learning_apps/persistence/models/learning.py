from __future__ import annotations

from .accounts import UserProfile
from .base import models

class LearningGoal(models.Model):
    STATUS_GOAL_SUBMITTED = "goal_submitted"
    STATUS_SELF_ASSESSMENT_COMPLETED = "self_assessment_completed"

    VECTOR_STORE_NOT_CONFIGURED = ""
    VECTOR_STORE_CREATING = "creating"
    VECTOR_STORE_READY = "ready"
    VECTOR_STORE_FAILED = "failed"

    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="learning_goals", db_column="user_id")
    title = models.CharField(max_length=255, null=True, blank=True)
    preference_text = models.TextField()
    domain = models.CharField(max_length=100, blank=True, default="")
    branch = models.CharField(max_length=100, blank=True, default="")
    status = models.CharField(max_length=64, default=STATUS_GOAL_SUBMITTED)
    self_assessment_guides = models.JSONField(default=dict, blank=True)
    goal_snapshot = models.JSONField(default=dict, blank=True)
    vector_store_id = models.CharField(max_length=128, blank=True, default="")
    vector_store_status = models.CharField(max_length=32, blank=True, default=VECTOR_STORE_NOT_CONFIGURED)
    vector_store_error = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learning_goals"
        indexes = [
            models.Index(fields=["status"], name="idx_learning_goals_status"),
        ]

class LearningGoalConceptMap(models.Model):
    STATUS_PENDING = "pending"
    STATUS_RUNNING = "running"
    STATUS_READY = "ready"
    STATUS_FAILED = "failed"

    learning_goal = models.OneToOneField(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="concept_map_asset",
        db_column="learning_goal_id",
    )
    status = models.CharField(max_length=32, default=STATUS_PENDING)
    llm_version = models.CharField(max_length=64, blank=True, default="")
    summary = models.TextField(blank=True, default="")
    content = models.JSONField(default=dict, blank=True)
    trace = models.JSONField(default=dict, blank=True)
    error_code = models.CharField(max_length=64, blank=True, default="")
    error_message = models.TextField(blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learning_goal_concept_maps"
        indexes = [
            models.Index(fields=["status"], name="idx_goal_cmap_status"),
        ]
