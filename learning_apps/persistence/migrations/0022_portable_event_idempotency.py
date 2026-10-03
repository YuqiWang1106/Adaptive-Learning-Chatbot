from django.db import migrations, models


def blank_keys_to_null(apps, schema_editor):
    event_model = apps.get_model("main", "AdaptiveInteractionEvent")
    event_model.objects.filter(idempotency_key="").update(idempotency_key=None)


def null_keys_to_blank(apps, schema_editor):
    event_model = apps.get_model("main", "AdaptiveInteractionEvent")
    event_model.objects.filter(idempotency_key__isnull=True).update(idempotency_key="")


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0021_adaptiveprobe_trace_id"),
    ]

    operations = [
        migrations.RemoveConstraint(
            model_name="adaptiveinteractionevent",
            name="uix_aie_user_goal_idempotency",
        ),
        migrations.AlterField(
            model_name="adaptiveinteractionevent",
            name="idempotency_key",
            field=models.CharField(blank=True, default=None, max_length=160, null=True),
        ),
        migrations.RunPython(blank_keys_to_null, null_keys_to_blank),
        migrations.AddConstraint(
            model_name="adaptiveinteractionevent",
            constraint=models.UniqueConstraint(
                fields=("user", "learning_goal", "idempotency_key"),
                name="uix_aie_user_goal_idempotency",
            ),
        ),
    ]
