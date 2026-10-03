# Generated for Probe Concept & Priority Scheduler v2.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0008_adaptive_mastery_tracker"),
    ]

    operations = [
        migrations.CreateModel(
            name="ChatConceptSignal",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("concept_key", models.CharField(max_length=160)),
                ("concept_label", models.CharField(blank=True, default="", max_length=180)),
                ("confidence", models.FloatField(default=0.0)),
                ("evidence_snippet", models.TextField(blank=True, default="")),
                (
                    "source",
                    models.CharField(
                        choices=[
                            ("concept_map", "concept_map"),
                            ("rule", "rule"),
                            ("llm", "llm"),
                            ("fallback", "fallback"),
                        ],
                        default="fallback",
                        max_length=32,
                    ),
                ),
                ("related_concepts", models.JSONField(blank=True, default=list)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "chat_history",
                    models.ForeignKey(
                        blank=True,
                        db_column="chat_history_id",
                        null=True,
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="concept_signals",
                        to="main.userhistory",
                    ),
                ),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_concept_signals",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="chat_concept_signals",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "chat_concept_signals",
                "indexes": [
                    models.Index(fields=["user", "learning_goal", "created_at"], name="idx_ccs_user_goal_time"),
                    models.Index(fields=["learning_goal", "concept_key", "created_at"], name="idx_ccs_goal_concept_time"),
                    models.Index(fields=["chat_history"], name="idx_ccs_history"),
                ],
            },
        ),
    ]
