from django.db import migrations, models
import learning_apps.persistence.models


def backfill_legacy_material_sources(apps, schema_editor):
    material_model = apps.get_model("main", "UploadedLearningMaterial")
    for material in material_model.objects.filter(source_key="").iterator(chunk_size=500):
        material.source_key = f"legacy-{material.pk}"
        material.source_version = max(1, int(material.source_version or 1))
        material.content_signature = "legacy_unverified"
        material.save(
            update_fields=["source_key", "source_version", "content_signature"]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0022_portable_event_idempotency"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="cleanup_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="content_sha256",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="active_content_sha256",
            field=models.CharField(blank=True, max_length=64, null=True),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="content_signature",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="deleted_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="deletion_job_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="ingestion_attempts",
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="ingestion_job_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="processing_started_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="provider_cleanup_pending",
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="source_file",
            field=models.FileField(
                blank=True,
                max_length=500,
                upload_to=learning_apps.persistence.models.learning_material_upload_path,
            ),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="source_key",
            field=models.CharField(blank=True, db_index=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="source_version",
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.RunPython(backfill_legacy_material_sources, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="uploadedlearningmaterial",
            name="source_key",
            field=models.CharField(
                db_index=True,
                default=learning_apps.persistence.models.generate_learning_material_source_key,
                max_length=64,
            ),
        ),
        migrations.AlterField(
            model_name="uploadedlearningmaterial",
            name="status",
            field=models.CharField(
                choices=[
                    ("uploaded", "uploaded"),
                    ("queued", "queued"),
                    ("processing", "processing"),
                    ("ready", "ready"),
                    ("failed", "failed"),
                    ("deleting", "deleting"),
                    ("delete_failed", "delete_failed"),
                    ("deleted", "deleted"),
                ],
                default="uploaded",
                max_length=32,
            ),
        ),
        migrations.AddIndex(
            model_name="uploadedlearningmaterial",
            index=models.Index(fields=["learning_goal", "source_key"], name="idx_ulm_goal_source"),
        ),
        migrations.AddIndex(
            model_name="uploadedlearningmaterial",
            index=models.Index(fields=["status", "updated_at"], name="idx_ulm_status_updated"),
        ),
        migrations.AddConstraint(
            model_name="uploadedlearningmaterial",
            constraint=models.UniqueConstraint(
                fields=("user", "learning_goal", "active_content_sha256"),
                name="uix_ulm_user_goal_active_sha",
            ),
        ),
        migrations.AddConstraint(
            model_name="uploadedlearningmaterial",
            constraint=models.UniqueConstraint(
                fields=("learning_goal", "source_key", "source_version"),
                name="uix_ulm_goal_source_version",
            ),
        ),
        migrations.AddConstraint(
            model_name="uploadedlearningmaterial",
            constraint=models.CheckConstraint(
                check=models.Q(("source_version__gte", 1)),
                name="chk_ulm_source_version_gte_1",
            ),
        ),
    ]
