from __future__ import annotations

from .accounts import UserProfile
from .base import generate_probe_offer_id, models
from .conversation import UserHistory
from .learning import LearningGoal

class AdaptiveInteractionEvent(models.Model):
    SOURCE_SELF_ASSESSMENT_BASELINE = "self_assessment_baseline"
    SOURCE_PROBE_RESPONSE = "probe_response"

    SOURCE_CHOICES = [
        (SOURCE_SELF_ASSESSMENT_BASELINE, "self_assessment_baseline"),
        (SOURCE_PROBE_RESPONSE, "probe_response"),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="adaptive_events",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="adaptive_events",
        db_column="learning_goal_id",
    )
    concept_key = models.CharField(max_length=160, db_index=True)
    source = models.CharField(max_length=40, choices=SOURCE_CHOICES)
    question_text = models.TextField(blank=True, default="")
    student_answer = models.TextField(blank=True, default="")
    accuracy_score = models.FloatField(default=0.0)
    dimension_scores = models.JSONField(default=dict, blank=True)
    grader_labels = models.JSONField(default=dict, blank=True)
    evidence = models.TextField(blank=True, default="")
    confidence = models.FloatField(default=0.0)
    latency_ms = models.PositiveIntegerField(default=0)
    # Optional caller-supplied replay key.  It is scoped by user + goal in the
    # service layer so duplicate delivery cannot create a second evidence row.
    # NULL means “no replay key”.  A regular multi-column unique constraint on
    # nullable values works on the production MySQL backend, whereas the
    # previous conditional unique constraint was silently unsupported there.
    idempotency_key = models.CharField(max_length=160, blank=True, null=True, default=None)
    trace_id = models.CharField(max_length=128, blank=True, default="")
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "adaptive_interaction_events"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "learning_goal", "idempotency_key"],
                name="uix_aie_user_goal_idempotency",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "created_at"], name="idx_aie_user_goal_time"),
            models.Index(fields=["learning_goal", "concept_key", "created_at"], name="idx_aie_goal_concept_time"),
            models.Index(fields=["source", "created_at"], name="idx_aie_source_time"),
            models.Index(fields=["user", "learning_goal", "idempotency_key"], name="idx_aie_idempotency"),
        ]

class LearnerBehaviorEvidence(models.Model):
    """Append-only evidence that must not be confused with mastery state."""

    EVENT_PREFERENCE_STATE_UPDATED = "preference_state_updated"
    EVENT_KNOWLEDGE_MAP_SNAPSHOT = "knowledge_map_snapshot"
    EVENT_EXPLICIT_PREFERENCE = "explicit_preference"
    EVENT_MICRO_CHECK_RESPONSE = "micro_check_response"

    EVENT_TYPE_CHOICES = [
        (EVENT_PREFERENCE_STATE_UPDATED, "preference_state_updated"),
        (EVENT_KNOWLEDGE_MAP_SNAPSHOT, "knowledge_map_snapshot"),
        (EVENT_EXPLICIT_PREFERENCE, "explicit_preference"),
        (EVENT_MICRO_CHECK_RESPONSE, "micro_check_response"),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="behavior_evidence",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="behavior_evidence",
        db_column="learning_goal_id",
    )
    concept_key = models.CharField(max_length=160, blank=True, default="")
    event_type = models.CharField(max_length=48, choices=EVENT_TYPE_CHOICES)
    source = models.CharField(max_length=80)
    payload = models.JSONField(default=dict, blank=True)
    confidence = models.FloatField(default=1.0)
    idempotency_key = models.CharField(max_length=160, blank=True, default="")
    trace_id = models.CharField(max_length=128, blank=True, default="")
    schema_version = models.CharField(max_length=40, default="learner_behavior_v1")
    occurred_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "learner_behavior_evidence"
        indexes = [
            models.Index(fields=["user", "occurred_at"], name="idx_lbe_user_time"),
            models.Index(fields=["learning_goal", "event_type", "occurred_at"], name="idx_lbe_goal_type_time"),
            models.Index(fields=["idempotency_key"], name="idx_lbe_idempotency"),
        ]

class LearnerMisconceptionState(models.Model):
    STATUS_ACTIVE = "active"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [
        (STATUS_ACTIVE, "active"),
        (STATUS_RESOLVED, "resolved"),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="misconception_states",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="misconception_states",
        db_column="learning_goal_id",
    )
    concept_key = models.CharField(max_length=160)
    misconception_key = models.CharField(max_length=160)
    description = models.TextField(blank=True, default="")
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_ACTIVE)
    confidence = models.FloatField(default=0.0)
    occurrence_count = models.PositiveIntegerField(default=0)
    first_evidence = models.ForeignKey(
        AdaptiveInteractionEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="first_misconception_observations",
        db_column="first_evidence_id",
    )
    last_evidence = models.ForeignKey(
        AdaptiveInteractionEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_misconception_observations",
        db_column="last_evidence_id",
    )
    resolved_by_evidence = models.ForeignKey(
        AdaptiveInteractionEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="resolved_misconceptions",
        db_column="resolved_by_evidence_id",
    )
    source_summary = models.JSONField(default=dict, blank=True)
    schema_version = models.CharField(max_length=40, default="learner_misconception_v1")
    policy_version = models.CharField(max_length=40, default="misconception_policy_v1")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "learner_misconception_states"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "learning_goal", "concept_key", "misconception_key"],
                name="uix_lmc_user_goal_concept_mis",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "status"], name="idx_lmc_user_goal_status"),
            models.Index(fields=["learning_goal", "concept_key", "status"], name="idx_lmc_goal_concept_status"),
        ]

class ChatConceptSignal(models.Model):
    SOURCE_CONCEPT_MAP = "concept_map"
    SOURCE_RULE = "rule"
    SOURCE_VECTOR_MATCH = "vector_match"
    SOURCE_VECTOR_NEW = "vector_new"
    SOURCE_LLM = "llm"
    SOURCE_FALLBACK = "fallback"

    SOURCE_CHOICES = [
        (SOURCE_CONCEPT_MAP, "concept_map"),
        (SOURCE_RULE, "rule"),
        (SOURCE_VECTOR_MATCH, "vector_match"),
        (SOURCE_VECTOR_NEW, "vector_new"),
        (SOURCE_LLM, "llm"),
        (SOURCE_FALLBACK, "fallback"),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="chat_concept_signals",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="chat_concept_signals",
        db_column="learning_goal_id",
    )
    chat_history = models.ForeignKey(
        UserHistory,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="concept_signals",
        db_column="chat_history_id",
    )
    concept_key = models.CharField(max_length=160)
    concept_label = models.CharField(max_length=180, blank=True, default="")
    confidence = models.FloatField(default=0.0)
    evidence_snippet = models.TextField(blank=True, default="")
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default=SOURCE_FALLBACK)
    related_concepts = models.JSONField(default=list, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "chat_concept_signals"
        indexes = [
            models.Index(fields=["user", "learning_goal", "created_at"], name="idx_ccs_user_goal_time"),
            models.Index(fields=["learning_goal", "concept_key", "created_at"], name="idx_ccs_goal_concept_time"),
            models.Index(fields=["chat_history"], name="idx_ccs_history"),
        ]

class ConceptRegistryEntry(models.Model):
    SOURCE_BASELINE = "baseline"
    SOURCE_CONCEPT_MAP = "concept_map"
    SOURCE_MASTERY_STATE = "mastery_state"
    SOURCE_CHAT = "chat"
    SOURCE_PROBE = "probe"
    SOURCE_MANUAL = "manual"

    SOURCE_CHOICES = [
        (SOURCE_BASELINE, "baseline"),
        (SOURCE_CONCEPT_MAP, "concept_map"),
        (SOURCE_MASTERY_STATE, "mastery_state"),
        (SOURCE_CHAT, "chat"),
        (SOURCE_PROBE, "probe"),
        (SOURCE_MANUAL, "manual"),
    ]

    STATUS_PROVISIONAL = "provisional"
    STATUS_VERIFIED = "verified"
    STATUS_REJECTED = "rejected"
    STATUS_MERGED = "merged"
    STATUS_DEPRECATED = "deprecated"
    STATUS_CHOICES = [
        (STATUS_PROVISIONAL, "provisional"),
        (STATUS_VERIFIED, "verified"),
        (STATUS_REJECTED, "rejected"),
        (STATUS_MERGED, "merged"),
        (STATUS_DEPRECATED, "deprecated"),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="concept_registry_entries",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="concept_registry_entries",
        db_column="learning_goal_id",
    )
    concept_key = models.CharField(max_length=160)
    concept_label = models.CharField(max_length=180, blank=True, default="")
    description = models.TextField(blank=True, default="")
    aliases = models.JSONField(default=list, blank=True)
    embedding = models.JSONField(default=list, blank=True)
    embedding_model = models.CharField(max_length=128, blank=True, default="")
    embedding_dimensions = models.PositiveIntegerField(default=0)
    source = models.CharField(max_length=32, choices=SOURCE_CHOICES, default=SOURCE_CHAT)
    status = models.CharField(max_length=24, choices=STATUS_CHOICES, default=STATUS_PROVISIONAL)
    taxonomy_version = models.CharField(max_length=80, default="concept_taxonomy_v1")
    provenance = models.JSONField(default=list, blank=True)
    verification_method = models.CharField(max_length=64, blank=True, default="")
    verified_at = models.DateTimeField(null=True, blank=True)
    merged_into = models.ForeignKey(
        "self",
        on_delete=models.RESTRICT,
        null=True,
        blank=True,
        related_name="merged_candidates",
        db_column="merged_into_id",
    )
    usage_count = models.PositiveIntegerField(default=0)
    last_seen_at = models.DateTimeField(null=True, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "concept_registry_entries"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "learning_goal", "concept_key"],
                name="uix_cre_user_goal_concept",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "updated_at"], name="idx_cre_user_goal_time"),
            models.Index(fields=["learning_goal", "concept_key"], name="idx_cre_goal_concept"),
            models.Index(fields=["user", "learning_goal", "status"], name="idx_cre_user_goal_status"),
        ]

class ConceptIdentityDecision(models.Model):
    """Auditable, goal-scoped identity decision without retaining raw learner text."""

    DECISION_ACCEPTED = "accepted"
    DECISION_ABSTAINED = "abstained"
    DECISION_PROVISIONAL = "provisional"
    DECISION_STATUS_CHOICES = [
        (DECISION_ACCEPTED, "accepted"),
        (DECISION_ABSTAINED, "abstained"),
        (DECISION_PROVISIONAL, "provisional"),
    ]

    RELATION_SAME = "same"
    RELATION_RELATED = "related"
    RELATION_PREREQUISITE = "prerequisite"
    RELATION_BROADER_OR_NARROWER = "broader_or_narrower"
    RELATION_NONE_OF_ABOVE = "none_of_above"
    RELATION_INSUFFICIENT = "insufficient"
    RELATION_CHOICES = [
        (RELATION_SAME, "same"),
        (RELATION_RELATED, "related"),
        (RELATION_PREREQUISITE, "prerequisite"),
        (RELATION_BROADER_OR_NARROWER, "broader_or_narrower"),
        (RELATION_NONE_OF_ABOVE, "none_of_above"),
        (RELATION_INSUFFICIENT, "insufficient"),
    ]

    ADMISSION_ACCEPTED = "accepted"
    ADMISSION_BLOCKED = "blocked"
    ADMISSION_CHOICES = [
        (ADMISSION_ACCEPTED, "accepted"),
        (ADMISSION_BLOCKED, "blocked"),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="concept_identity_decisions",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="concept_identity_decisions",
        db_column="learning_goal_id",
    )
    selected_concept = models.ForeignKey(
        ConceptRegistryEntry,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="identity_decisions",
        db_column="selected_concept_id",
    )
    decision_key = models.CharField(max_length=64, unique=True)
    request_hash = models.CharField(max_length=64, db_index=True)
    input_hash = models.CharField(max_length=64)
    candidate_hash = models.CharField(max_length=64)
    taxonomy_fingerprint = models.CharField(max_length=64)
    resolver_version = models.CharField(max_length=48)
    prompt_version = models.CharField(max_length=48)
    model = models.CharField(max_length=96, blank=True, default="")
    provider = models.CharField(max_length=32, blank=True, default="")
    relation = models.CharField(
        max_length=32,
        choices=RELATION_CHOICES,
        default=RELATION_INSUFFICIENT,
    )
    decision_status = models.CharField(
        max_length=24,
        choices=DECISION_STATUS_CHOICES,
        default=DECISION_ABSTAINED,
    )
    admission_status = models.CharField(
        max_length=24,
        choices=ADMISSION_CHOICES,
        default=ADMISSION_BLOCKED,
    )
    admission_reason = models.CharField(max_length=96, blank=True, default="")
    confidence_band = models.CharField(max_length=16, blank=True, default="")
    candidate_trace = models.JSONField(default=list, blank=True)
    raw_output = models.JSONField(default=dict, blank=True)
    cache_hit_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "concept_identity_decisions"
        constraints = [
            # One replayable identity request may have exactly one decision;
            # ``decision_key`` is already unique globally, while this scoped
            # constraint documents and protects the user/goal boundary for
            # database backends that inspect model metadata.
            models.UniqueConstraint(
                fields=["user", "learning_goal", "request_hash"],
                name="uix_cid_user_goal_request",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "created_at"], name="idx_cid_user_goal_time"),
            models.Index(fields=["learning_goal", "decision_status"], name="idx_cid_goal_status"),
            models.Index(fields=["taxonomy_fingerprint"], name="idx_cid_taxonomy_fp"),
        ]

class LearnerMasteryState(models.Model):
    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="mastery_states",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="mastery_states",
        db_column="learning_goal_id",
    )
    concept_key = models.CharField(max_length=160)
    facts_mastery = models.FloatField(default=0.0)
    procedures_mastery = models.FloatField(default=0.0)
    strategies_mastery = models.FloatField(default=0.0)
    rationales_mastery = models.FloatField(default=0.0)
    dimension_mastery_score = models.FloatField(default=0.0)
    quality_score = models.FloatField(default=0.0)
    weakest_dimension = models.CharField(max_length=32, blank=True, default="")
    curve_pattern = models.CharField(max_length=32, blank=True, default="insufficient_data")
    curve_confidence = models.FloatField(default=0.0)
    curve_evidence_count = models.PositiveIntegerField(default=0)
    dimension_curve_patterns = models.JSONField(default=dict, blank=True)
    dimension_curve_confidences = models.JSONField(default=dict, blank=True)
    curve_reason = models.JSONField(default=dict, blank=True)
    feedback_tier = models.CharField(max_length=32, blank=True, default="CONSOLIDATE")
    response_policy = models.JSONField(default=dict, blank=True)
    # Evidence-backed confidence/provenance for the current concept-scoped
    # mastery state.  These fields are intentionally separate from curve
    # confidence, which describes a temporal pattern rather than mastery
    # certainty.
    mastery_confidence = models.FloatField(default=0.0)
    dimension_confidences = models.JSONField(default=dict, blank=True)
    eligible_evidence_count = models.PositiveIntegerField(default=0)
    evidence_source_summary = models.JSONField(default=dict, blank=True)
    policy_version = models.CharField(max_length=64, blank=True, default="")
    last_event = models.ForeignKey(
        AdaptiveInteractionEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="mastery_updates",
        db_column="last_event_id",
    )
    last_eligible_evidence = models.ForeignKey(
        AdaptiveInteractionEvent,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="last_eligible_mastery_states",
        db_column="last_eligible_evidence_id",
    )
    event_count = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "learner_mastery_states"
        constraints = [
            models.UniqueConstraint(
                fields=["user", "learning_goal", "concept_key"],
                name="uix_lms_user_goal_concept",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "updated_at"], name="idx_lms_user_goal_time"),
            models.Index(fields=["learning_goal", "weakest_dimension"], name="idx_lms_goal_weak_dim"),
        ]

class AdaptiveProbe(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ANSWERED = "answered"
    STATUS_SKIPPED = "skipped"
    STATUS_EXPIRED = "expired"

    STATUS_CHOICES = [
        (STATUS_PENDING, "pending"),
        (STATUS_ANSWERED, "answered"),
        (STATUS_SKIPPED, "skipped"),
        (STATUS_EXPIRED, "expired"),
    ]

    user = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="adaptive_probes",
        db_column="user_id",
    )
    learning_goal = models.ForeignKey(
        LearningGoal,
        on_delete=models.CASCADE,
        related_name="adaptive_probes",
        db_column="learning_goal_id",
    )
    concept_key = models.CharField(max_length=160)
    target_dimension = models.CharField(max_length=32)
    question_text = models.TextField()
    expected_rubric = models.JSONField(default=dict, blank=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    student_answer = models.TextField(blank=True, default="")
    due_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    trace_id = models.CharField(max_length=128, blank=True, default="", db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "adaptive_probes"
        indexes = [
            models.Index(fields=["user", "learning_goal", "status"], name="idx_ap_user_goal_status"),
            models.Index(fields=["learning_goal", "due_at"], name="idx_ap_goal_due"),
        ]

class AdaptiveProbeOffer(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_GENERATING = "generating"
    STATUS_READY = "ready"
    STATUS_CONSUMED = "consumed"
    STATUS_SNOOZED = "snoozed"
    STATUS_DISMISSED = "dismissed"
    STATUS_EXPIRED = "expired"
    STATUS_SUPERSEDED = "superseded"
    STATUS_CHOICES = [(value, value) for value in (
        STATUS_PENDING,
        STATUS_ACCEPTED,
        STATUS_GENERATING,
        STATUS_READY,
        STATUS_CONSUMED,
        STATUS_SNOOZED,
        STATUS_DISMISSED,
        STATUS_EXPIRED,
        STATUS_SUPERSEDED,
    )]
    ACTIVE_STATUSES = frozenset({STATUS_PENDING, STATUS_ACCEPTED, STATUS_GENERATING, STATUS_READY})

    offer_id = models.CharField(max_length=48, primary_key=True, default=generate_probe_offer_id, editable=False)
    user = models.ForeignKey(UserProfile, on_delete=models.CASCADE, related_name="adaptive_probe_offers")
    learning_goal = models.ForeignKey(LearningGoal, on_delete=models.CASCADE, related_name="adaptive_probe_offers")
    policy_version = models.CharField(max_length=64)
    due_trigger = models.CharField(max_length=32)
    due_reason = models.CharField(max_length=120)
    target_concept_key = models.CharField(max_length=160)
    target_concept_label = models.CharField(max_length=180, blank=True, default="")
    target_dimension = models.CharField(max_length=32)
    candidate_score = models.FloatField(default=0.0)
    candidate_components_snapshot = models.JSONField(default=dict, blank=True)
    mastery_state_fingerprint = models.CharField(max_length=64)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    source = models.CharField(max_length=64, default="review_scheduler")
    idempotency_key = models.CharField(max_length=96, unique=True)
    open_scope_key = models.CharField(max_length=160, unique=True, null=True, blank=True)
    offered_at = models.DateTimeField(auto_now_add=True)
    last_presented_at = models.DateTimeField(null=True, blank=True)
    presented_count = models.PositiveIntegerField(default=0)
    accepted_at = models.DateTimeField(null=True, blank=True)
    snoozed_at = models.DateTimeField(null=True, blank=True)
    snoozed_until = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)
    expired_at = models.DateTimeField(null=True, blank=True)
    resulting_probe = models.OneToOneField(
        AdaptiveProbe,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="source_offer",
    )
    expires_at = models.DateTimeField()
    last_error_code = models.CharField(max_length=64, blank=True, default="")
    mastery_write_authorized = models.BooleanField(default=False, editable=False)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "adaptive_probe_offers"
        constraints = [
            models.CheckConstraint(
                check=models.Q(mastery_write_authorized=False),
                name="chk_apo_no_mastery",
            ),
        ]
        indexes = [
            models.Index(fields=["user", "learning_goal", "status"], name="idx_apo_scope_status"),
            models.Index(fields=["status", "expires_at"], name="idx_apo_status_expiry"),
        ]
