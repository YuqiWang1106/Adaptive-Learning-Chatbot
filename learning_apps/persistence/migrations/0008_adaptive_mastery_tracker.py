# Generated for Adaptive Mastery Tracker v1.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0007_learninggoal_vector_store_error_and_more"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdaptiveInteractionEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("concept_key", models.CharField(db_index=True, max_length=160)),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("self_assessment_baseline", "self_assessment_baseline"),
                            ("understanding_check", "understanding_check"),
                            ("probe_response", "probe_response"),
                        ],
                        max_length=40,
                    ),
                ),
                ("question_text", models.TextField(blank=True, default="")),
                ("student_answer", models.TextField(blank=True, default="")),
                ("accuracy_score", models.FloatField(default=0.0)),
                ("dimension_scores", models.JSONField(blank=True, default=dict)),
                ("grader_labels", models.JSONField(blank=True, default=dict)),
                ("evidence", models.TextField(blank=True, default="")),
                ("confidence", models.FloatField(default=0.0)),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adaptive_events",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adaptive_events",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "adaptive_interaction_events",
                "indexes": [
                    models.Index(fields=["user", "learning_goal", "created_at"], name="idx_aie_user_goal_time"),
                    models.Index(fields=["learning_goal", "concept_key", "created_at"], name="idx_aie_goal_concept_time"),
                    models.Index(fields=["source", "created_at"], name="idx_aie_source_time"),
                ],
            },
        ),
        migrations.CreateModel(
            name="AdaptiveProbe",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("concept_key", models.CharField(max_length=160)),
                ("target_dimension", models.CharField(max_length=32)),
                ("question_text", models.TextField()),
                ("expected_rubric", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "pending"),
                            ("answered", "answered"),
                            ("skipped", "skipped"),
                            ("expired", "expired"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("student_answer", models.TextField(blank=True, default="")),
                ("due_at", models.DateTimeField(blank=True, null=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adaptive_probes",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adaptive_probes",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "adaptive_probes",
                "indexes": [
                    models.Index(fields=["user", "learning_goal", "status"], name="idx_ap_user_goal_status"),
                    models.Index(fields=["learning_goal", "due_at"], name="idx_ap_goal_due"),
                ],
            },
        ),
        migrations.CreateModel(
            name="LearnerMasteryState",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("concept_key", models.CharField(max_length=160)),
                ("facts_mastery", models.FloatField(default=0.0)),
                ("procedures_mastery", models.FloatField(default=0.0)),
                ("strategies_mastery", models.FloatField(default=0.0)),
                ("rationales_mastery", models.FloatField(default=0.0)),
                ("dimension_mastery_score", models.FloatField(default=0.0)),
                ("quality_score", models.FloatField(default=0.0)),
                ("weakest_dimension", models.CharField(blank=True, default="", max_length=32)),
                ("curve_pattern", models.CharField(blank=True, default="insufficient_data", max_length=32)),
                ("feedback_tier", models.CharField(blank=True, default="CONSOLIDATE", max_length=32)),
                ("response_policy", models.JSONField(blank=True, default=dict)),
                ("event_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "last_event",
                    models.ForeignKey(
                        blank=True,
                        db_column="last_event_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="mastery_updates",
                        to="main.adaptiveinteractionevent",
                    ),
                ),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mastery_states",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="mastery_states",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "learner_mastery_states",
                "indexes": [
                    models.Index(fields=["user", "learning_goal", "updated_at"], name="idx_lms_user_goal_time"),
                    models.Index(fields=["learning_goal", "weakest_dimension"], name="idx_lms_goal_weak_dim"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="learnermasterystate",
            constraint=models.UniqueConstraint(
                fields=("user", "learning_goal", "concept_key"),
                name="uix_lms_user_goal_concept",
            ),
        ),
    ]
