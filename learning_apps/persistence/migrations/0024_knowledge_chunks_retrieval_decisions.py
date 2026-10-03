import learning_apps.persistence.models
from django.db import migrations, models
import django.db.models.deletion


def mark_legacy_materials_unverified(apps, schema_editor):
    material_model = apps.get_model("main", "UploadedLearningMaterial")
    material_model.objects.filter(content_signature="legacy_unverified").update(
        chunk_status="legacy_unverified",
        chunk_error_code="legacy_source_unverified",
    )


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0023_learning_material_source_lifecycle"),
    ]

    operations = [
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="chunk_error_code",
            field=models.CharField(blank=True, default="", max_length=96),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="chunk_status",
            field=models.CharField(
                choices=[
                    ("not_requested", "not_requested"),
                    ("queued", "queued"),
                    ("processing", "processing"),
                    ("ready", "ready"),
                    ("failed", "failed"),
                    ("legacy_unverified", "legacy_unverified"),
                ],
                default="not_requested",
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="chunk_transform_version",
            field=models.CharField(blank=True, default="", max_length=64),
        ),
        migrations.AddField(
            model_name="uploadedlearningmaterial",
            name="chunks_built_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(mark_legacy_materials_unverified, migrations.RunPython.noop),
        migrations.CreateModel(
            name="KnowledgeChunk",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("source_key", models.CharField(max_length=64)),
                ("source_version", models.PositiveIntegerField()),
                ("source_content_sha256", models.CharField(max_length=64)),
                ("chunk_id", models.CharField(max_length=64, unique=True)),
                ("chunk_index", models.PositiveIntegerField()),
                ("content", models.TextField()),
                ("content_sha256", models.CharField(max_length=64)),
                ("transform_version", models.CharField(max_length=64)),
                ("locator", models.CharField(max_length=128)),
                (
                    "locator_type",
                    models.CharField(
                        choices=[
                            ("line", "line"),
                            ("paragraph", "paragraph"),
                            ("slide", "slide"),
                            ("page", "page"),
                        ],
                        max_length=24,
                    ),
                ),
                ("locator_start", models.PositiveIntegerField()),
                ("locator_end", models.PositiveIntegerField()),
                ("locator_part", models.PositiveIntegerField(default=1)),
                (
                    "lifecycle",
                    models.CharField(
                        choices=[
                            ("active", "active"),
                            ("superseded", "superseded"),
                            ("deleted", "deleted"),
                        ],
                        default="active",
                        max_length=24,
                    ),
                ),
                ("trust_level", models.CharField(default="untrusted_retrieved_content", max_length=48)),
                ("superseded_at", models.DateTimeField(blank=True, null=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="knowledge_chunks",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "material",
                    models.ForeignKey(
                        db_column="material_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="knowledge_chunks",
                        to="main.uploadedlearningmaterial",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="knowledge_chunks",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={"db_table": "knowledge_chunks"},
        ),
        migrations.CreateModel(
            name="RetrievalDecision",
            fields=[
                (
                    "decision_id",
                    models.CharField(
                        default=learning_apps.persistence.models.generate_learning_material_source_key,
                        editable=False,
                        max_length=64,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("query_sha256", models.CharField(max_length=64)),
                ("scope_sha256", models.CharField(max_length=64)),
                ("policy_version", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("accepted", "accepted"),
                            ("abstained", "abstained"),
                            ("failed", "failed"),
                        ],
                        max_length=24,
                    ),
                ),
                ("reason_code", models.CharField(max_length=96)),
                ("failure_code", models.CharField(blank=True, default="", max_length=96)),
                ("candidate_count", models.PositiveIntegerField(default=0)),
                ("candidate_set_sha256", models.CharField(max_length=64)),
                ("selected_chunk_hashes", models.JSONField(blank=True, default=list)),
                ("evidence_count", models.PositiveIntegerField(default=0)),
                ("mastery_write_authorized", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="retrieval_decisions",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="retrieval_decisions",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={
                "db_table": "retrieval_decisions",
                "indexes": [
                    models.Index(fields=["user", "learning_goal", "created_at"], name="idx_retrieval_scope_created"),
                    models.Index(fields=["query_sha256", "created_at"], name="idx_retrieval_query_created"),
                    models.Index(fields=["status", "created_at"], name="idx_retrieval_status"),
                ],
            },
        ),
        migrations.AddConstraint(
            model_name="retrievaldecision",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_retrieval_no_mastery",
            ),
        ),
        migrations.AddIndex(
            model_name="knowledgechunk",
            index=models.Index(fields=["user", "learning_goal", "lifecycle"], name="idx_kchunk_scope_lifecycle"),
        ),
        migrations.AddIndex(
            model_name="knowledgechunk",
            index=models.Index(fields=["material", "lifecycle"], name="idx_kchunk_material_life"),
        ),
        migrations.AddIndex(
            model_name="knowledgechunk",
            index=models.Index(fields=["source_key", "source_version"], name="idx_kchunk_source_version"),
        ),
        migrations.AddConstraint(
            model_name="knowledgechunk",
            constraint=models.UniqueConstraint(
                fields=("material", "transform_version", "chunk_index"),
                name="uix_kchunk_material_tx_idx",
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgechunk",
            constraint=models.CheckConstraint(
                check=models.Q(("locator_start__gte", 1)),
                name="chk_kchunk_locator_start",
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgechunk",
            constraint=models.CheckConstraint(
                check=models.Q(("locator_end__gte", 1)),
                name="chk_kchunk_locator_end",
            ),
        ),
        migrations.AddConstraint(
            model_name="knowledgechunk",
            constraint=models.CheckConstraint(
                check=models.Q(("locator_part__gte", 1)),
                name="chk_kchunk_locator_part",
            ),
        ),
    ]
