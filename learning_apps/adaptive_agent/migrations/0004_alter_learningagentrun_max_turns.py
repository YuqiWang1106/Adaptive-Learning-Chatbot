from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('adaptive_agent', '0003_outboxevent_idempotency_key'),
    ]

    operations = [
        migrations.AlterField(
            model_name='learningagentrun',
            name='max_turns',
            field=models.PositiveSmallIntegerField(default=5),
        ),
    ]
