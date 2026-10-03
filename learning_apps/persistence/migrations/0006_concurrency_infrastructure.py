from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0005_learninggoalconceptmap"),
    ]

    operations = [
        migrations.AddField(
            model_name="learninggoal",
            name="goal_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="selfassessment",
            name="assessment_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="selfassessment",
            name="operational_strategy",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="userhistory",
            name="processing_status",
            field=models.CharField(default="succeeded", max_length=32),
        ),
        migrations.CreateModel(
            name="AIJob",
            fields=[
                ("job_id", models.CharField(max_length=64, primary_key=True, serialize=False)),
                ("task_type", models.CharField(max_length=64)),
                ("username", models.CharField(db_index=True, max_length=50)),
                ("idempotency_key", models.CharField(db_index=True, max_length=160)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("queued", "queued"),
                            ("running", "running"),
                            ("succeeded", "succeeded"),
                            ("retrying", "retrying"),
                            ("failed", "failed"),
                            ("degraded", "degraded"),
                        ],
                        default="queued",
                        max_length=32,
                    ),
                ),
                ("stage", models.CharField(blank=True, default="queued", max_length=64)),
                ("percent", models.PositiveSmallIntegerField(default=0)),
                ("message", models.TextField(blank=True, default="")),
                ("result", models.JSONField(blank=True, default=dict)),
                ("error_code", models.CharField(blank=True, default="", max_length=96)),
                ("error_message", models.TextField(blank=True, default="")),
                ("retry_count", models.PositiveSmallIntegerField(default=0)),
                ("max_retries", models.PositiveSmallIntegerField(default=3)),
                ("started_at", models.DateTimeField(blank=True, null=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        blank=True,
                        db_column="learning_goal_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="ai_jobs",
                        to="main.learninggoal",
                    ),
                ),
            ],
            options={
                "db_table": "ai_jobs",
                "indexes": [
                    models.Index(fields=["task_type", "status"], name="idx_ai_jobs_type_status"),
                    models.Index(fields=["created_at"], name="idx_ai_jobs_created"),
                ],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("task_type", "username", "idempotency_key"),
                        name="uix_ai_jobs_task_user_idem",
                    ),
                ],
            },
        ),
        migrations.CreateModel(
            name="FeedbackEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("username", models.CharField(db_index=True, max_length=50)),
                ("question", models.TextField(blank=True, default="")),
                ("topic", models.CharField(blank=True, default="", max_length=160)),
                ("difficulty", models.CharField(blank=True, default="", max_length=32)),
                ("answer_style", models.CharField(blank=True, default="", max_length=50)),
                ("feedback", models.CharField(blank=True, default="", max_length=64)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "user",
                    models.ForeignKey(
                        blank=True,
                        db_column="user_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="feedback_events",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "feedback_events",
                "indexes": [
                    models.Index(fields=["username", "created_at"], name="idx_feedback_user_created"),
                    models.Index(fields=["topic", "created_at"], name="idx_feedback_topic_created"),
                ],
            },
        ),
        migrations.CreateModel(
            name="LLMRequestLog",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("route", models.CharField(db_index=True, max_length=96)),
                ("provider", models.CharField(default="openai", max_length=32)),
                ("model", models.CharField(blank=True, default="", max_length=96)),
                ("job_id", models.CharField(blank=True, db_index=True, default="", max_length=64)),
                ("status", models.CharField(db_index=True, max_length=32)),
                ("error_code", models.CharField(blank=True, default="", max_length=96)),
                ("error_message", models.TextField(blank=True, default="")),
                ("latency_ms", models.PositiveIntegerField(default=0)),
                ("prompt_tokens", models.PositiveIntegerField(default=0)),
                ("completion_tokens", models.PositiveIntegerField(default=0)),
                ("total_tokens", models.PositiveIntegerField(default=0)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "db_table": "llm_request_logs",
                "indexes": [
                    models.Index(fields=["route", "created_at"], name="idx_llm_route_created"),
                    models.Index(fields=["status", "created_at"], name="idx_llm_status_created"),
                ],
            },
        ),
    ]
