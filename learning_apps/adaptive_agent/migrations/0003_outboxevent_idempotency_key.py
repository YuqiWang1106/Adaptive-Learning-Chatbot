from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('adaptive_agent', '0002_mcpaccessgrant_pluginregistration_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='outboxevent',
            name='idempotency_key',
            field=models.CharField(blank=True, max_length=96, null=True, unique=True),
        ),
    ]
