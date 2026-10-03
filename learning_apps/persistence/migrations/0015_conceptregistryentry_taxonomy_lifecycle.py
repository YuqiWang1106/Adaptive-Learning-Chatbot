from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("main", "0014_learnerbehaviorevidence_learnerpreferencestate_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="conceptregistryentry",
            name="embedding_dimensions",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="conceptregistryentry",
            name="embedding_model",
            field=models.CharField(blank=True, default="", max_length=128),
        ),
        migrations.AddField(
            model_name="conceptregistryentry",
            name="merged_into",
            field=models.ForeignKey(
                blank=True,
                db_column="merged_into_id",
                null=True,
                on_delete=django.db.models.deletion.RESTRICT,
                related_name="merged_candidates",
                to="main.conceptregistryentry",
            ),
        ),
        migrations.AddField(
            model_name="conceptregistryentry",
            name="provenance",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="conceptregistryentry",
            name="status",
            field=models.CharField(
                choices=[
                    ("provisional", "provisional"),
                    ("verified", "verified"),
                    ("rejected", "rejected"),
                    ("merged", "merged"),
                    ("deprecated", "deprecated"),
                ],
                default="verified",
                max_length=24,
            ),
        ),
        migrations.AddField(
            model_name="conceptregistryentry",
            name="taxonomy_version",
            field=models.CharField(default="concept_taxonomy_v1", max_length=80),
        ),
        migrations.AddField(
            model_name="conceptregistryentry",
            name="verification_method",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="conceptregistryentry",
            name="verified_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name="conceptregistryentry",
            name="status",
            field=models.CharField(
                choices=[
                    ("provisional", "provisional"),
                    ("verified", "verified"),
                    ("rejected", "rejected"),
                    ("merged", "merged"),
                    ("deprecated", "deprecated"),
                ],
                default="provisional",
                max_length=24,
            ),
        ),
        migrations.AddIndex(
            model_name="conceptregistryentry",
            index=models.Index(
                fields=["user", "learning_goal", "status"],
                name="idx_cre_user_goal_status",
            ),
        ),
    ]
