from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0015_conceptregistryentry_taxonomy_lifecycle"),
    ]

    operations = [
        migrations.CreateModel(
            name="ConceptIdentityDecision",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("decision_key", models.CharField(max_length=64, unique=True)),
                ("request_hash", models.CharField(db_index=True, max_length=64)),
                ("input_hash", models.CharField(max_length=64)),
                ("candidate_hash", models.CharField(max_length=64)),
                ("taxonomy_fingerprint", models.CharField(max_length=64)),
                ("resolver_version", models.CharField(max_length=48)),
                ("prompt_version", models.CharField(max_length=48)),
                ("model", models.CharField(blank=True, default="", max_length=96)),
                ("provider", models.CharField(blank=True, default="", max_length=32)),
                (
                    "relation",
                    models.CharField(
                        choices=[
                            ("same", "same"),
                            ("related", "related"),
                            ("prerequisite", "prerequisite"),
                            ("broader_or_narrower", "broader_or_narrower"),
                            ("none_of_above", "none_of_above"),
                            ("insufficient", "insufficient"),
                        ],
                        default="insufficient",
                        max_length=32,
                    ),
                ),
                (
                    "decision_status",
                    models.CharField(
                        choices=[
                            ("accepted", "accepted"),
                            ("abstained", "abstained"),
                            ("provisional", "provisional"),
                        ],
                        default="abstained",
                        max_length=24,
                    ),
                ),
                (
                    "admission_status",
                    models.CharField(
                        choices=[("accepted", "accepted"), ("blocked", "blocked")],
                        default="blocked",
                        max_length=24,
                    ),
                ),
                ("admission_reason", models.CharField(blank=True, default="", max_length=96)),
                ("confidence_band", models.CharField(blank=True, default="", max_length=16)),
                ("candidate_trace", models.JSONField(blank=True, default=list)),
                ("raw_output", models.JSONField(blank=True, default=dict)),
                ("cache_hit_count", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("last_used_at", models.DateTimeField(auto_now=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="concept_identity_decisions",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "selected_concept",
                    models.ForeignKey(
                        blank=True,
                        db_column="selected_concept_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="identity_decisions",
                        to="main.conceptregistryentry",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="concept_identity_decisions",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "concept_identity_decisions",
            },
        ),
        migrations.AddIndex(
            model_name="conceptidentitydecision",
            index=models.Index(fields=["user", "learning_goal", "created_at"], name="idx_cid_user_goal_time"),
        ),
        migrations.AddIndex(
            model_name="conceptidentitydecision",
            index=models.Index(fields=["learning_goal", "decision_status"], name="idx_cid_goal_status"),
        ),
        migrations.AddIndex(
            model_name="conceptidentitydecision",
            index=models.Index(fields=["taxonomy_fingerprint"], name="idx_cid_taxonomy_fp"),
        ),
    ]
