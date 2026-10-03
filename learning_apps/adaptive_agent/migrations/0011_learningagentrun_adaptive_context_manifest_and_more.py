# Generated for P6.7 Adaptive Context and non-blocking Micro Checks.

import learning_apps.adaptive_agent.models
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("adaptive_agent", "0010_agentconversationmemory_run_memory_manifest"),
        ("main", "0029_p5_external_capability_control"),
    ]

    operations = [
        migrations.AddField(
            model_name="learningagentrun",
            name="adaptive_context_manifest",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="learningagentrun",
            name="origin",
            field=models.CharField(
                choices=[("normal", "normal"), ("micro_check_response", "micro_check_response")],
                default="normal",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="learningagentrun",
            name="origin_reference",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AlterField(
            model_name="agentrunevent",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("run_started", "run_started"),
                    ("adaptive_context_loaded", "adaptive_context_loaded"),
                    ("memory_loaded", "memory_loaded"),
                    ("plan_updated", "plan_updated"),
                    ("skill_selected", "skill_selected"),
                    ("tool_requested", "tool_requested"),
                    ("tool_started", "tool_started"),
                    ("tool_completed", "tool_completed"),
                    ("evidence_attached", "evidence_attached"),
                    ("clarification_requested", "clarification_requested"),
                    ("approval_requested", "approval_requested"),
                    ("approval_resolved", "approval_resolved"),
                    ("answer_streaming", "answer_streaming"),
                    ("micro_check_offered", "micro_check_offered"),
                    ("micro_check_blocked", "micro_check_blocked"),
                    ("micro_check_answered", "micro_check_answered"),
                    ("micro_check_skipped", "micro_check_skipped"),
                    ("micro_check_superseded", "micro_check_superseded"),
                    ("run_completed", "run_completed"),
                    ("run_failed", "run_failed"),
                ],
                max_length=40,
            ),
        ),
        migrations.CreateModel(
            name="AgentMicroCheck",
            fields=[
                (
                    "check_id",
                    models.CharField(
                        default=learning_apps.adaptive_agent.models.generate_micro_check_id,
                        editable=False,
                        max_length=48,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("conversation_generation", models.PositiveIntegerField()),
                ("originating_skill_id", models.CharField(max_length=64)),
                ("originating_skill_version", models.CharField(max_length=32)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("near_transfer", "near_transfer"),
                            ("one_step", "one_step"),
                            ("choice_with_reason", "choice_with_reason"),
                            ("error_diagnosis", "error_diagnosis"),
                            ("short_recall", "short_recall"),
                        ],
                        max_length=32,
                    ),
                ),
                ("prompt", models.TextField()),
                ("options", models.JSONField(blank=True, default=list)),
                (
                    "response_format",
                    models.CharField(
                        choices=[("short_text", "short_text"), ("single_choice", "single_choice")],
                        max_length=24,
                    ),
                ),
                ("concept_key", models.CharField(max_length=160)),
                ("target_dimension", models.CharField(max_length=32)),
                ("encrypted_rubric", models.TextField()),
                ("rubric_sha256", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("offered", "offered"),
                            ("answer_submitted", "answer_submitted"),
                            ("evaluating", "evaluating"),
                            ("answered", "answered"),
                            ("skipped", "skipped"),
                            ("superseded", "superseded"),
                            ("expired", "expired"),
                        ],
                        default="offered",
                        max_length=32,
                    ),
                ),
                ("open_scope_key", models.CharField(blank=True, max_length=160, null=True, unique=True)),
                ("encrypted_submitted_response", models.TextField(blank=True, default="")),
                ("submitted_response_sha256", models.CharField(blank=True, default="", max_length=64)),
                ("evaluation_outcome", models.CharField(blank=True, default="", max_length=24)),
                ("evaluation_confidence", models.FloatField(default=0.0)),
                ("evaluation_version", models.CharField(blank=True, default="", max_length=64)),
                ("evaluation_payload", models.JSONField(blank=True, default=dict)),
                ("offered_at", models.DateTimeField(auto_now_add=True)),
                ("answered_at", models.DateTimeField(blank=True, null=True)),
                ("skipped_at", models.DateTimeField(blank=True, null=True)),
                ("expired_at", models.DateTimeField(blank=True, null=True)),
                ("superseded_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField()),
                ("mastery_write_authorized", models.BooleanField(default=False, editable=False)),
                (
                    "conversation",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_micro_checks",
                        to="main.learningconversation",
                    ),
                ),
                (
                    "learning_goal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_micro_checks",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "originating_run",
                    models.OneToOneField(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="micro_check",
                        to="adaptive_agent.learningagentrun",
                    ),
                ),
                (
                    "response_run",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="micro_check_response_for",
                        to="adaptive_agent.learningagentrun",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="agent_micro_checks",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "agent_micro_checks_v2",
                "indexes": [
                    models.Index(fields=["user", "learning_goal", "status"], name="idx_amc_scope_status"),
                    models.Index(
                        fields=["conversation", "conversation_generation", "offered_at"],
                        name="idx_amc_conv_generation",
                    ),
                ],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(("mastery_write_authorized", False)),
                        name="chk_amc_no_mastery",
                    ),
                ],
            },
        ),
    ]
