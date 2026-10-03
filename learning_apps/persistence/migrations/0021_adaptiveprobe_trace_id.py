from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0020_adaptiveprobe_expires_at"),
    ]

    operations = [
        migrations.AddField(
            model_name="adaptiveprobe",
            name="trace_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=128),
        ),
    ]
