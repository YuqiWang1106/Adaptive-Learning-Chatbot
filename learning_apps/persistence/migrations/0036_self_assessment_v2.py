from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0035_alter_userhistory_conversation_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="selfassessment",
            name="submission_snapshot",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="selfassessment",
            name="diagnostic_metadata",
            field=models.JSONField(blank=True, default=dict),
        ),
        migrations.AddField(
            model_name="selfassessmentevidencedecision",
            name="model_configuration",
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
