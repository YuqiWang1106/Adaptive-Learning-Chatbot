from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("adaptive_agent", "0011_learningagentrun_adaptive_context_manifest_and_more"),
    ]

    operations = [
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
                    ("calibration_offer_blocked", "calibration_offer_blocked"),
                    ("run_completed", "run_completed"),
                    ("run_failed", "run_failed"),
                ],
                max_length=40,
            ),
        ),
    ]
