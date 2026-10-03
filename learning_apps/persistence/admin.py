from django.contrib import admin

from .models import (
    AdaptiveInteractionEvent,
    AdaptiveProbe,
    AIJob,
    CSAReferenceBlueprint,
    ChatConceptSignal,
    ConceptIdentityDecision,
    ConceptRegistryEntry,
    KnowledgeChunk,
    LearnerBehaviorEvidence,
    LearnerMasteryState,
    LearnerMisconceptionState,
    LearningGoal,
    LLMRequestLog,
    RetrievalDecision,
    SelfAssessment,
    UploadedLearningMaterial,
    UserHistory,
    UserProfile,
)


@admin.register(UserProfile)
class UserProfileAdmin(admin.ModelAdmin):
    list_display = ("user_id", "username", "email", "role", "is_admin", "preferences_completed")
    search_fields = ("username", "email")
    list_filter = ("role", "is_admin", "preferences_completed", "academic_level")


@admin.register(LearningGoal)
class LearningGoalAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "title", "domain", "branch", "status", "vector_store_status", "updated_at")
    search_fields = ("user__username", "title", "preference_text", "domain", "branch", "vector_store_id")
    list_filter = ("status", "domain", "branch", "vector_store_status")


@admin.register(UploadedLearningMaterial)
class UploadedLearningMaterialAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "original_filename",
        "file_extension",
        "status",
        "created_at",
    )
    search_fields = ("user__username", "original_filename", "openai_file_id", "vector_store_id")
    list_filter = ("status", "file_extension")
    readonly_fields = ("created_at", "updated_at")


@admin.register(KnowledgeChunk)
class KnowledgeChunkAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "source_key",
        "source_version",
        "chunk_index",
        "locator",
        "lifecycle",
        "transform_version",
    )
    search_fields = ("chunk_id", "source_key", "content_sha256")
    list_filter = ("lifecycle", "locator_type", "transform_version")
    readonly_fields = tuple(field.name for field in KnowledgeChunk._meta.fields)


@admin.register(RetrievalDecision)
class RetrievalDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "decision_id",
        "user",
        "learning_goal",
        "status",
        "reason_code",
        "candidate_count",
        "evidence_count",
        "created_at",
    )
    search_fields = ("decision_id", "query_sha256", "scope_sha256")
    list_filter = ("status", "policy_version", "mastery_write_authorized")
    readonly_fields = tuple(field.name for field in RetrievalDecision._meta.fields)


@admin.register(UserHistory)
class UserHistoryAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "learning_goal", "answer_style", "timestamp")
    search_fields = ("user__username", "question", "answer")
    list_filter = ("answer_style",)


@admin.register(CSAReferenceBlueprint)
class CSAReferenceBlueprintAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "status",
        "framework_version",
        "model",
        "created_at",
    )
    search_fields = (
        "user__username",
        "learning_goal__title",
        "blueprint_sha256",
        "generation_input_sha256",
    )
    list_filter = ("status", "framework_version", "model")
    readonly_fields = tuple(
        field.name for field in CSAReferenceBlueprint._meta.fields
    )


@admin.register(SelfAssessment)
class SelfAssessmentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "username",
        "learning_goal",
        "reference_blueprint",
        "domain",
        "branch",
        "created_at",
    )
    search_fields = ("username", "domain", "branch")
    list_filter = ("domain", "branch")


@admin.register(AdaptiveInteractionEvent)
class AdaptiveInteractionEventAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "concept_key",
        "source",
        "accuracy_score",
        "confidence",
        "created_at",
    )
    search_fields = ("user__username", "concept_key", "source", "student_answer", "question_text")
    list_filter = ("source", "concept_key")
    readonly_fields = ("created_at",)


@admin.register(ChatConceptSignal)
class ChatConceptSignalAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "concept_key",
        "concept_label",
        "source",
        "confidence",
        "created_at",
    )
    search_fields = ("user__username", "concept_key", "concept_label", "evidence_snippet")
    list_filter = ("source", "concept_key")
    readonly_fields = ("created_at",)


@admin.register(ConceptRegistryEntry)
class ConceptRegistryEntryAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "concept_key",
        "concept_label",
        "status",
        "source",
        "taxonomy_version",
        "usage_count",
        "updated_at",
    )
    search_fields = ("user__username", "concept_key", "concept_label", "description")
    list_filter = ("status", "source", "taxonomy_version")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ConceptIdentityDecision)
class ConceptIdentityDecisionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "selected_concept",
        "relation",
        "decision_status",
        "admission_status",
        "model",
        "created_at",
    )
    search_fields = ("user__username", "request_hash", "input_hash", "selected_concept__concept_key")
    list_filter = ("relation", "decision_status", "admission_status", "model")
    readonly_fields = tuple(field.name for field in ConceptIdentityDecision._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LearnerMasteryState)
class LearnerMasteryStateAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "concept_key",
        "weakest_dimension",
        "curve_pattern",
        "feedback_tier",
        "quality_score",
        "updated_at",
    )
    search_fields = ("user__username", "concept_key", "weakest_dimension", "feedback_tier")
    list_filter = ("weakest_dimension", "curve_pattern", "feedback_tier")
    readonly_fields = ("created_at", "updated_at")


@admin.register(LearnerBehaviorEvidence)
class LearnerBehaviorEvidenceAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "learning_goal", "event_type", "source", "confidence", "occurred_at")
    search_fields = ("user__username", "concept_key", "source", "trace_id", "idempotency_key")
    list_filter = ("event_type", "source")
    readonly_fields = tuple(field.name for field in LearnerBehaviorEvidence._meta.fields)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(LearnerMisconceptionState)
class LearnerMisconceptionStateAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "concept_key",
        "misconception_key",
        "status",
        "confidence",
        "occurrence_count",
        "updated_at",
    )
    search_fields = ("user__username", "concept_key", "misconception_key", "description")
    list_filter = ("status", "policy_version")
    readonly_fields = (
        "first_evidence",
        "last_evidence",
        "resolved_by_evidence",
        "source_summary",
        "schema_version",
        "policy_version",
        "created_at",
        "updated_at",
        "resolved_at",
    )


@admin.register(AdaptiveProbe)
class AdaptiveProbeAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "learning_goal",
        "concept_key",
        "target_dimension",
        "status",
        "due_at",
        "completed_at",
    )
    search_fields = ("user__username", "concept_key", "target_dimension", "question_text")
    list_filter = ("status", "target_dimension")
    readonly_fields = ("created_at", "updated_at")


@admin.register(AIJob)
class AIJobAdmin(admin.ModelAdmin):
    list_display = ("job_id", "task_type", "username", "status", "stage", "percent", "retry_count", "updated_at")
    search_fields = ("job_id", "username", "task_type", "error_code")
    list_filter = ("task_type", "status", "stage")
    readonly_fields = ("created_at", "updated_at", "started_at", "finished_at")


@admin.register(LLMRequestLog)
class LLMRequestLogAdmin(admin.ModelAdmin):
    list_display = ("id", "route", "model", "status", "latency_ms", "total_tokens", "job_id", "created_at")
    search_fields = ("route", "model", "job_id", "error_code")
    list_filter = ("route", "status", "model")
    readonly_fields = ("created_at",)
