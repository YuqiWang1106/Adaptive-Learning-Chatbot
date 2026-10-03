from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0012_remove_userconversationstate_user_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="userprofile",
            name="role",
            field=models.CharField(
                choices=[("student", "Student"), ("teacher", "Teacher")],
                default="student",
                max_length=20,
            ),
        ),
        migrations.AddIndex(
            model_name="userprofile",
            index=models.Index(fields=["role"], name="idx_users_role"),
        ),
    ]
