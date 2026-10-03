from __future__ import annotations

from .accounts import UserProfile
from .base import generate_learning_material_source_key, models
from .knowledge import RetrievalDecision
from .learning import LearningGoal


class CSAReferenceBlueprint(models.Model):
    """Frozen, target-task-scoped CSA criteria generated before comparison."""

    STATUS_FROZEN = "frozen"
    STATUS_CHOICES = [(STATUS_FROZEN, "frozen")]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="csa_reference_blueprints",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="csa_reference_blueprints",
        db_column="learning_goal_id",
    )
    status = models.CharField(
        max_length=24,
        choices=STATUS_CHOICES,
        default=STATUS_FROZEN,
        editable=False,
    )
    framework_version = models.CharField(max_length=64)
    prompt_version = models.CharField(max_length=64)
    taxonomy_sha256 = models.CharField(max_length=64)
    generation_input_sha256 = models.CharField(max_length=64, unique=True)
    blueprint_sha256 = models.CharField(max_length=64, db_index=True)
    goal_snapshot = models.JSONField(default=dict)
    target_problem_snapshot = models.JSONField(default=dict)
    scope_contract = models.JSONField(default=dict)
    learner_context_snapshot = models.JSONField(default=dict)
    core_concepts_snapshot = models.JSONField(default=list)
    blueprint = models.JSONField(default=dict)
    model = models.CharField(max_length=96)
    model_configuration = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "csa_reference_blueprints"
        indexes = [
            models.Index(
                fields=["user", "learning_goal", "created_at"],
                name="idx_csarb_scope_created",
            ),
            models.Index(
                fields=["taxonomy_sha256"],
                name="idx_csarb_taxonomy",
            ),
        ]


class SelfAssessment(models.Model):
    username = models.CharField(max_length=50)
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="self_assessments",
        db_column="learning_goal_id",
    )
    reference_blueprint = models.ForeignKey(
        CSAReferenceBlueprint,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="self_assessments",
        db_column="reference_blueprint_id",
    )
    target_problem = models.TextField(blank=True, default="")
    scope_contract = models.JSONField(default=dict, blank=True)
    domain = models.CharField(max_length=100, blank=True, default="")
    branch = models.CharField(max_length=100, blank=True, default="")
    student_text = models.TextField(blank=True, default="")
    evaluation_report = models.TextField(blank=True, default="")
    structured_report = models.JSONField(default=dict, blank=True)
    operational_strategy = models.JSONField(default=dict, blank=True)
    submission_snapshot = models.JSONField(default=dict, blank=True)
    diagnostic_metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "self_assessments"
        indexes = [
            models.Index(fields=["username", "created_at"], name="idx_sa_user_created"),
            models.Index(fields=["learning_goal", "created_at"], name="idx_sa_goal_created"),
        ]


class CSAReferenceBlueprintEvidenceRetrieval(models.Model):
    DIMENSION_CHOICES = [
        ("Facts", "Facts"),
        ("Strategies", "Strategies"),
        ("Procedures", "Procedures"),
        ("Rationales", "Rationales"),
    ]

    blueprint = models.ForeignKey(
        CSAReferenceBlueprint,
        on_delete=models.CASCADE,
        related_name="retrieval_links",
        db_column="blueprint_id",
    )
    retrieval_decision = models.ForeignKey(
        RetrievalDecision,
        on_delete=models.PROTECT,
        related_name="csa_blueprint_links",
        db_column="retrieval_decision_id",
    )
    dimension = models.CharField(max_length=24, choices=DIMENSION_CHOICES)

    class Meta:
        db_table = "csa_reference_blueprint_retrievals"
        constraints = [
            models.UniqueConstraint(
                fields=["blueprint", "dimension"],
                name="uix_csarbr_blueprint_dimension",
            ),
            models.UniqueConstraint(
                fields=["blueprint", "retrieval_decision"],
                name="uix_csarbr_blueprint_retrieval",
            ),
        ]


class SelfAssessmentEvidenceDecision(models.Model):
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
        related_name="self_assessment_evidence_decisions",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="self_assessment_evidence_decisions",
        db_column="learning_goal_id",
    )
    self_assessment = models.OneToOneField(
        SelfAssessment,
        on_delete=models.CASCADE,
        related_name="evidence_decision",
        db_column="self_assessment_id",
    )
    status = models.CharField(max_length=24, choices=STATUS_CHOICES)
    reason_code = models.CharField(max_length=96)
    policy_version = models.CharField(max_length=64)
    assessment_sha256 = models.CharField(max_length=64)
    structured_report_sha256 = models.CharField(max_length=64)
    retrieval_bundle_sha256 = models.CharField(max_length=64)
    scope_sha256 = models.CharField(max_length=64)
    taxonomy_sha256 = models.CharField(max_length=64)
    lifecycle_bundle_sha256 = models.CharField(max_length=64)
    decision_sha256 = models.CharField(max_length=64)
    prompt_version = models.CharField(max_length=64)
    model = models.CharField(max_length=96, blank=True, default="")
    model_configuration = models.JSONField(default=dict, blank=True)
    dimension_status = models.JSONField(default=dict, blank=True)
    quote_validation_sha256 = models.CharField(max_length=64, blank=True, default="")
    is_partial = models.BooleanField(default=False)
    idempotency_key = models.CharField(max_length=64, unique=True)
    invalidated_at = models.DateTimeField(null=True, blank=True)
    mastery_write_authorized = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "self_assessment_evidence_decisions"
        constraints = [
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_saed_no_mastery",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "status"], name="idx_saed_scope_status"),
            models.Index(fields=["taxonomy_sha256"], name="idx_saed_taxonomy"),
        ]

class SelfAssessmentEvidenceRetrieval(models.Model):
    DIMENSION_CHOICES = [
        ("Facts", "Facts"),
        ("Strategies", "Strategies"),
        ("Procedures", "Procedures"),
        ("Rationales", "Rationales"),
    ]

    evidence_decision = models.ForeignKey(
        SelfAssessmentEvidenceDecision,
        on_delete=models.CASCADE,
        related_name="retrieval_links",
        db_column="evidence_decision_id",
    )
    retrieval_decision = models.ForeignKey(
        RetrievalDecision,
        on_delete=models.PROTECT,
        related_name="self_assessment_links",
        db_column="retrieval_decision_id",
    )
    dimension = models.CharField(max_length=24, choices=DIMENSION_CHOICES)

    class Meta:
        db_table = "self_assessment_evidence_retrievals"
        constraints = [
            models.UniqueConstraint(
                fields=["evidence_decision", "dimension"],
                name="uix_saer_decision_dimension",
            ),
            models.UniqueConstraint(
                fields=["evidence_decision", "retrieval_decision"],
                name="uix_saer_decision_retrieval",
            ),
        ]

class LearnerPerceivedState(models.Model):
    """Goal-scoped self-report projection that never authorizes mastery writes."""

    AUTHORITY_GUIDANCE_ONLY = "guidance_only"
    PROJECTION_VERSION = "perceived_state_v1"

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="perceived_states",
        db_column="user_id",
    )
    learning_goal = models.OneToOneField(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="perceived_state",
        db_column="learning_goal_id",
    )
    source_assessment = models.ForeignKey(
        SelfAssessment,
        on_delete=models.PROTECT,
        related_name="perceived_state_projections",
        db_column="source_assessment_id",
    )
    evidence_decision = models.ForeignKey(
        SelfAssessmentEvidenceDecision,
        on_delete=models.PROTECT,
        related_name="perceived_state_projections",
        db_column="evidence_decision_id",
    )
    dimension_scores = models.JSONField(default=dict)
    reported_uncertainties = models.JSONField(default=dict, blank=True)
    diagnostic_summary = models.JSONField(default=dict, blank=True)
    taxonomy_sha256 = models.CharField(max_length=64)
    authority = models.CharField(max_length=32, default=AUTHORITY_GUIDANCE_ONLY, editable=False)
    projection_version = models.CharField(max_length=64, default=PROJECTION_VERSION)
    mastery_write_authorized = models.BooleanField(default=False, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learner_perceived_states"
        constraints = [
            models.CheckConstraint(
                check=models.Q(authority="guidance_only"),
                name="chk_lps_guidance_only",
            ),
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_lps_no_mastery",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "updated_at"], name="idx_lps_user_updated"),
        ]
