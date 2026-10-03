from django.db import migrations, models
from django.db.models import Q
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0017_conceptidentitydecision_request_scope"),
    ]

    operations = [
        migrations.AddField(
            model_name="adaptiveinteractionevent",
            name="idempotency_key",
            field=models.CharField(blank=True, default="", max_length=160),
        ),
        migrations.AddField(
            model_name="learnermasterystate",
            name="mastery_confidence",
            field=models.FloatField(default=0.0),
        ),
        migrations.AddField(
            model_name="learnermasterystate",
            name="dimension_confidences",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="learnermasterystate",
            name="eligible_evidence_count",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="learnermasterystate",
            name="evidence_source_summary",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="learnermasterystate",
            name="policy_version",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="learnermasterystate",
            name="last_eligible_evidence",
            field=models.ForeignKey(
                blank=True,
                db_column="last_eligible_evidence_id",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="last_eligible_mastery_states",
                to="main.adaptiveinteractionevent",
            ),
        ),
        migrations.AddIndex(
            model_name="adaptiveinteractionevent",
            index=models.Index(
                fields=["user", "learning_goal", "idempotency_key"],
                name="idx_aie_idempotency",
            ),
        ),
        migrations.AddConstraint(
            model_name="adaptiveinteractionevent",
            constraint=models.UniqueConstraint(
                condition=~Q(idempotency_key=""),
                fields=("user", "learning_goal", "idempotency_key"),
                name="uix_aie_user_goal_idempotency",
            ),
        ),
    ]
