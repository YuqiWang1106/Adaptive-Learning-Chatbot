"""Remove superseded P3-P5 orchestration tables and unused audit fields."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0031_p6_8_perceived_state_probe_only_mastery"),
    ]

    operations = [
        # Drop complete legacy aggregates in dependency order. Removing their
        # foreign keys one-by-one forces SQLite to rebuild tables while old
        # composite constraints still reference those fields.
        migrations.DeleteModel(name="AgentToolInvocation"),
        migrations.DeleteModel(name="AgentRun"),
        migrations.DeleteModel(name="AgentShadowRun"),
        migrations.DeleteModel(name="ExternalActionExecution"),
        migrations.DeleteModel(name="ExternalActionApproval"),
        migrations.DeleteModel(name="ExternalConnectorGrant"),
        migrations.DeleteModel(name="FeedbackEvent"),
        migrations.RemoveField(
            model_name="answergroundingdecision",
            name="invalidated_at",
        ),
        migrations.RemoveField(
            model_name="answergroundingdecision",
            name="invalidation_reason",
        ),
        migrations.RemoveField(
            model_name="learnerpreferencestate",
            name="explicit_preferences",
        ),
        migrations.RemoveField(
            model_name="selfassessmentevidencedecision",
            name="invalidation_reason",
        ),
    ]
