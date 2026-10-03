import learning_apps.persistence.models
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0024_knowledge_chunks_retrieval_decisions"),
    ]

    operations = [
        migrations.AddField(
            model_name="retrievaldecision",
            name="purpose",
            field=models.CharField(default="general", max_length=64),
        ),
        migrations.AddField(
            model_name="retrievaldecision",
            name="request_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="retrievaldecision",
            name="taxonomy_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="retrievaldecision",
            name="lifecycle_bundle_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="retrievaldecision",
            name="selected_bundle_sha256",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="retrievaldecision",
            name="idempotency_key",
            field=models.CharField(blank=True, default=None, max_length=64, null=True, unique=True),
        ),
        migrations.AddIndex(
            model_name="retrievaldecision",
            index=models.Index(
                fields=["user", "learning_goal", "purpose", "request_sha256"],
                name="idx_retrieval_scope_req",
            ),
        ),
        migrations.CreateModel(
            name="RetrievalDecisionEvidence",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("evidence_ref", models.CharField(max_length=40)),
                ("rank", models.PositiveIntegerField()),
                ("scores", models.JSONField(blank=True, default=dict)),
                ("snapshot_sha256", models.CharField(max_length=64)),
                ("mastery_write_authorized", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "knowledge_chunk",
                    models.ForeignKey(
                        blank=True,
                        db_column="knowledge_chunk_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="retrieval_evidence_rows",
                        to="main.knowledgechunk",
                    ),
                ),
                (
                    "retrieval_decision",
                    models.ForeignKey(
                        db_column="retrieval_decision_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="evidence_rows",
                        to="main.retrievaldecision",
                    ),
                ),
            ],
            options={"db_table": "retrieval_decision_evidence"},
        ),
        migrations.AddConstraint(
            model_name="retrievaldecisionevidence",
            constraint=models.UniqueConstraint(
                fields=("retrieval_decision", "evidence_ref"),
                name="uix_rde_decision_ref",
            ),
        ),
        migrations.AddConstraint(
            model_name="retrievaldecisionevidence",
            constraint=models.UniqueConstraint(
                fields=("retrieval_decision", "rank"),
                name="uix_rde_decision_rank",
            ),
        ),
        migrations.AddConstraint(
            model_name="retrievaldecisionevidence",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_rde_no_mastery",
            ),
        ),
        migrations.AddIndex(
            model_name="retrievaldecisionevidence",
            index=models.Index(fields=["retrieval_decision", "rank"], name="idx_rde_decision_rank"),
        ),
        migrations.AddIndex(
            model_name="retrievaldecisionevidence",
            index=models.Index(fields=["knowledge_chunk"], name="idx_rde_chunk"),
        ),
        migrations.CreateModel(
            name="SelfAssessmentEvidenceDecision",
            fields=[
                (
                    "decision_id",
                    models.CharField(
                        default=learning_apps.persistence.models.generate_learning_material_source_key,
                        editable=False,
                        max_length=64,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("status", models.CharField(choices=[("accepted", "accepted"), ("blocked", "blocked")], max_length=24)),
                ("reason_code", models.CharField(max_length=96)),
                ("policy_version", models.CharField(max_length=64)),
                ("assessment_sha256", models.CharField(max_length=64)),
                ("structured_report_sha256", models.CharField(max_length=64)),
                ("retrieval_bundle_sha256", models.CharField(max_length=64)),
                ("scope_sha256", models.CharField(max_length=64)),
                ("taxonomy_sha256", models.CharField(max_length=64)),
                ("lifecycle_bundle_sha256", models.CharField(max_length=64)),
                ("decision_sha256", models.CharField(max_length=64)),
                ("prompt_version", models.CharField(max_length=64)),
                ("model", models.CharField(blank=True, default="", max_length=96)),
                ("dimension_status", models.JSONField(blank=True, default=dict)),
                ("quote_validation_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("is_partial", models.BooleanField(default=False)),
                ("idempotency_key", models.CharField(max_length=64, unique=True)),
                ("invalidated_at", models.DateTimeField(blank=True, null=True)),
                ("invalidation_reason", models.CharField(blank=True, default="", max_length=96)),
                ("mastery_write_authorized", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="self_assessment_evidence_decisions",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "self_assessment",
                    models.OneToOneField(
                        db_column="self_assessment_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="evidence_decision",
                        to="main.selfassessment",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="self_assessment_evidence_decisions",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={"db_table": "self_assessment_evidence_decisions"},
        ),
        migrations.AddConstraint(
            model_name="selfassessmentevidencedecision",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_saed_no_mastery",
            ),
        ),
        migrations.AddIndex(
            model_name="selfassessmentevidencedecision",
            index=models.Index(fields=["user", "learning_goal", "status"], name="idx_saed_scope_status"),
        ),
        migrations.AddIndex(
            model_name="selfassessmentevidencedecision",
            index=models.Index(fields=["taxonomy_sha256"], name="idx_saed_taxonomy"),
        ),
        migrations.CreateModel(
            name="SelfAssessmentEvidenceRetrieval",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "dimension",
                    models.CharField(
                        choices=[("Facts", "Facts"), ("Strategies", "Strategies"), ("Procedures", "Procedures"), ("Rationales", "Rationales")],
                        max_length=24,
                    ),
                ),
                (
                    "evidence_decision",
                    models.ForeignKey(
                        db_column="evidence_decision_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="retrieval_links",
                        to="main.selfassessmentevidencedecision",
                    ),
                ),
                (
                    "retrieval_decision",
                    models.ForeignKey(
                        db_column="retrieval_decision_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="self_assessment_links",
                        to="main.retrievaldecision",
                    ),
                ),
            ],
            options={"db_table": "self_assessment_evidence_retrievals"},
        ),
        migrations.AddConstraint(
            model_name="selfassessmentevidenceretrieval",
            constraint=models.UniqueConstraint(
                fields=("evidence_decision", "dimension"),
                name="uix_saer_decision_dimension",
            ),
        ),
        migrations.AddConstraint(
            model_name="selfassessmentevidenceretrieval",
            constraint=models.UniqueConstraint(
                fields=("evidence_decision", "retrieval_decision"),
                name="uix_saer_decision_retrieval",
            ),
        ),
        migrations.CreateModel(
            name="AnswerGroundingDecision",
            fields=[
                (
                    "decision_id",
                    models.CharField(
                        default=learning_apps.persistence.models.generate_learning_material_source_key,
                        editable=False,
                        max_length=64,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                (
                    "status",
                    models.CharField(
                        choices=[("accepted", "accepted"), ("blocked", "blocked"), ("not_required", "not_required")],
                        max_length=24,
                    ),
                ),
                ("reason_code", models.CharField(max_length=96)),
                ("policy_version", models.CharField(max_length=64)),
                ("answer_sha256", models.CharField(max_length=64)),
                ("claim_set_sha256", models.CharField(max_length=64)),
                ("support_decision_sha256", models.CharField(max_length=64)),
                ("scope_sha256", models.CharField(max_length=64)),
                ("taxonomy_sha256", models.CharField(max_length=64)),
                ("lifecycle_bundle_sha256", models.CharField(max_length=64)),
                ("answer_model", models.CharField(blank=True, default="", max_length=96)),
                ("prompt_version", models.CharField(max_length=64)),
                ("verifier_model", models.CharField(blank=True, default="", max_length=96)),
                ("verifier_prompt_version", models.CharField(max_length=64)),
                ("idempotency_key", models.CharField(max_length=64, unique=True)),
                ("invalidated_at", models.DateTimeField(blank=True, null=True)),
                ("invalidation_reason", models.CharField(blank=True, default="", max_length=96)),
                ("mastery_write_authorized", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answer_grounding_decisions",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "retrieval_decision",
                    models.ForeignKey(
                        db_column="retrieval_decision_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="answer_grounding_decisions",
                        to="main.retrievaldecision",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answer_grounding_decisions",
                        to="main.userprofile",
                    ),
                ),
                (
                    "user_history",
                    models.OneToOneField(
                        blank=True,
                        db_column="user_history_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="grounding_decision",
                        to="main.userhistory",
                    ),
                ),
            ],
            options={"db_table": "answer_grounding_decisions"},
        ),
        migrations.AddConstraint(
            model_name="answergroundingdecision",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_agd_no_mastery",
            ),
        ),
        migrations.AddIndex(
            model_name="answergroundingdecision",
            index=models.Index(fields=["user", "learning_goal", "created_at"], name="idx_agd_scope_time"),
        ),
        migrations.AddIndex(
            model_name="answergroundingdecision",
            index=models.Index(fields=["status", "created_at"], name="idx_agd_status_time"),
        ),
        migrations.CreateModel(
            name="AnswerCitation",
            fields=[
                (
                    "citation_id",
                    models.CharField(
                        default=learning_apps.persistence.models.generate_learning_material_source_key,
                        editable=False,
                        max_length=64,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("claim_ordinal", models.PositiveIntegerField()),
                ("claim_sha256", models.CharField(max_length=64)),
                ("answer_start", models.PositiveIntegerField(blank=True, null=True)),
                ("answer_end", models.PositiveIntegerField(blank=True, null=True)),
                (
                    "relation",
                    models.CharField(
                        choices=[("supported", "supported"), ("contradicted", "contradicted"), ("insufficient", "insufficient")],
                        max_length=24,
                    ),
                ),
                ("confidence_band", models.CharField(max_length=16)),
                ("support_quote_sha256", models.CharField(max_length=64)),
                ("verifier_version", models.CharField(max_length=64)),
                ("mastery_write_authorized", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "grounding_decision",
                    models.ForeignKey(
                        db_column="grounding_decision_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="citations",
                        to="main.answergroundingdecision",
                    ),
                ),
                (
                    "retrieval_evidence",
                    models.ForeignKey(
                        db_column="retrieval_evidence_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="answer_citations",
                        to="main.retrievaldecisionevidence",
                    ),
                ),
            ],
            options={"db_table": "answer_citations"},
        ),
        migrations.AddConstraint(
            model_name="answercitation",
            constraint=models.UniqueConstraint(
                fields=("grounding_decision", "claim_ordinal", "retrieval_evidence"),
                name="uix_acite_decision_claim_evidence",
            ),
        ),
        migrations.AddConstraint(
            model_name="answercitation",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_acite_no_mastery",
            ),
        ),
        migrations.AddIndex(
            model_name="answercitation",
            index=models.Index(fields=["grounding_decision", "claim_ordinal"], name="idx_acite_decision_claim"),
        ),
    ]
