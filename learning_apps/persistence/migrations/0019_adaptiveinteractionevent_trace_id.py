from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0018_mastery_evidence_policy"),
    ]

    operations = [
        migrations.AddField(
            model_name="adaptiveinteractionevent",
            name="trace_id",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
    ]
