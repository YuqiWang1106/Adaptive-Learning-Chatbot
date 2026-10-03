from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0016_conceptidentitydecision"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="conceptidentitydecision",
            constraint=models.UniqueConstraint(
                fields=("user", "learning_goal", "request_hash"),
                name="uix_cid_user_goal_request",
            ),
        ),
    ]
