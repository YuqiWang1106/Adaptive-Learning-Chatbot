from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0019_adaptiveinteractionevent_trace_id"),
    ]

    operations = [
        migrations.AddField(
            model_name="adaptiveprobe",
            name="expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]
