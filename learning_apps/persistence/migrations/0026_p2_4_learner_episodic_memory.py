import learning_apps.persistence.models
from django.db import migrations, models
import django.db.models.deletion
import hashlib
from datetime import timedelta
import re
import uuid
from django.utils import timezone


def migrate_legacy_conversation_scopes(apps, schema_editor):
    """Scope attributable legacy rows; leave ambiguous rows permanently hidden."""

    LearningConversation = apps.get_model("main", "LearningConversation")
    LearningGoal = apps.get_model("main", "LearningGoal")
    UserHistory = apps.get_model("main", "UserHistory")
    AIJob = apps.get_model("main", "AIJob")

    legacy_ids = [
        value
        for value in (
        LearningGoal.objects.exclude(conversation_id__isnull=True)
        .exclude(conversation_id="")
        .values_list("conversation_id", flat=True)
        )
        if re.fullmatch(r"conv_[A-Za-z0-9_-]{6,}", str(value or ""))
    ]
    if len(legacy_ids) != len(set(legacy_ids)):
        raise RuntimeError("duplicate legacy provider conversation id; refusing ambiguous P2.4 migration")

    UserHistory.objects.update(
        scope_status="legacy_unscoped",
        event_key=None,
        conversation=None,
    )
    grace_expires_at = timezone.now() + timedelta(days=90)

    for goal in LearningGoal.objects.select_related("user").order_by("id").iterator():
        attributable_history = UserHistory.objects.filter(
            learning_goal_id=goal.id,
            user_id=goal.user_id,
        )
        attributable_jobs = AIJob.objects.filter(
            learning_goal_id=goal.id,
            username=goal.user.username,
            task_type="chat_answer",
        )
        if not goal.conversation_id and not attributable_history.exists() and not attributable_jobs.exists():
            continue
        scope_digest = hashlib.sha256(
            f"conversation\0{goal.user_id}\0{goal.id}".encode()
        ).hexdigest()
        legacy_provider_id = (
            goal.conversation_id
            if re.fullmatch(r"conv_[A-Za-z0-9_-]{6,}", str(goal.conversation_id or ""))
            else None
        )
        conversation = LearningConversation.objects.create(
            conversation_key=f"lc_{uuid.uuid4().hex}",
            user_id=goal.user_id,
            learning_goal_id=goal.id,
            generation=1,
            active_scope_key=f"scope_{scope_digest}",
            lifecycle="active",
            provider_conversation_id=legacy_provider_id,
            provider_cleanup_status="not_required",
            provider_cleanup_error=(
                "legacy_provider_id_quarantined"
                if goal.conversation_id and not legacy_provider_id
                else ""
            ),
            retention_class="standard",
            policy_version="p2.4-conversation-v1",
            retention_expires_at=grace_expires_at,
            mastery_write_authorized=False,
        )
        for history_id in attributable_history.values_list("id", flat=True).iterator():
            UserHistory.objects.filter(id=history_id).update(
                conversation_id=conversation.conversation_key,
                event_key=f"evt_legacy_history_{history_id}",
                scope_status="active",
            )
        attributable_jobs.update(conversation_id=conversation.conversation_key)

    # Provider identifiers now live only on internal conversation rows.
    LearningGoal.objects.exclude(conversation_id__isnull=True).update(conversation_id=None)


def reverse_history_scope_marker(apps, schema_editor):
    LearningConversation = apps.get_model("main", "LearningConversation")
    LearningGoal = apps.get_model("main", "LearningGoal")
    UserHistory = apps.get_model("main", "UserHistory")
    AIJob = apps.get_model("main", "AIJob")
    for conversation in LearningConversation.objects.filter(generation=1).order_by("created_at"):
        if conversation.provider_conversation_id:
            LearningGoal.objects.filter(id=conversation.learning_goal_id).update(
                conversation_id=conversation.provider_conversation_id
            )
    UserHistory.objects.update(
        conversation=None,
        event_key=None,
        scope_status="legacy_unscoped",
    )
    AIJob.objects.update(conversation=None)
    LearningConversation.objects.all().delete()


class Migration(migrations.Migration):
    dependencies = [
        ("main", "0025_p2_3_evidence_grounding"),
    ]

    operations = [
        migrations.CreateModel(
            name="LearningConversation",
            fields=[
                (
                    "conversation_key",
                    models.CharField(
                        default=learning_apps.persistence.models.generate_learning_material_source_key,
                        editable=False,
                        max_length=64,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("generation", models.PositiveIntegerField()),
                ("active_scope_key", models.CharField(blank=True, max_length=96, null=True, unique=True)),
                (
                    "lifecycle",
                    models.CharField(
                        choices=[
                            ("active", "active"),
                            ("tombstoned", "tombstoned"),
                            ("purged", "purged"),
                            ("deleted", "deleted"),
                        ],
                        default="active",
                        max_length=24,
                    ),
                ),
                ("provider_conversation_id", models.CharField(blank=True, max_length=128, null=True, unique=True)),
                (
                    "provider_cleanup_status",
                    models.CharField(
                        choices=[
                            ("not_required", "not_required"),
                            ("pending", "pending"),
                            ("retrying", "retrying"),
                            ("succeeded", "succeeded"),
                            ("abandoned", "abandoned"),
                        ],
                        default="not_required",
                        max_length=24,
                    ),
                ),
                ("provider_cleanup_attempts", models.PositiveSmallIntegerField(default=0)),
                ("provider_cleanup_error", models.CharField(blank=True, default="", max_length=160)),
                ("cleanup_lease_token", models.CharField(blank=True, default="", max_length=64)),
                ("cleanup_lease_expires_at", models.DateTimeField(blank=True, null=True)),
                ("cleanup_next_attempt_at", models.DateTimeField(blank=True, null=True)),
                ("retention_class", models.CharField(default="standard", max_length=32)),
                ("policy_version", models.CharField(default="p2.4-conversation-v1", max_length=64)),
                ("retention_expires_at", models.DateTimeField(blank=True, null=True)),
                ("tombstoned_at", models.DateTimeField(blank=True, null=True)),
                ("deleted_at", models.DateTimeField(blank=True, null=True)),
                ("last_activity_at", models.DateTimeField(auto_now_add=True)),
                ("mastery_write_authorized", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="conversation_generations",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.PROTECT,
                        related_name="learning_conversations",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={"db_table": "learning_conversations"},
        ),
        migrations.AddConstraint(
            model_name="learningconversation",
            constraint=models.UniqueConstraint(
                fields=("user", "learning_goal", "generation"),
                name="uix_conv_scope_generation",
            ),
        ),
        migrations.AddConstraint(
            model_name="learningconversation",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_conv_no_mastery",
            ),
        ),
        migrations.AddConstraint(
            model_name="learningconversation",
            constraint=models.CheckConstraint(
                check=(
                    (
                        models.Q(("active_scope_key__isnull", False), ("lifecycle", "active"))
                        & models.Q(("active_scope_key", ""), _negated=True)
                    )
                    | (models.Q(("lifecycle", "active"), _negated=True) & models.Q(("active_scope_key__isnull", True)))
                ),
                name="chk_conv_active_scope_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="learningconversation",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(("lifecycle__in", ["purged", "deleted"]), _negated=True)
                    | models.Q(("provider_conversation_id__isnull", True))
                ),
                name="chk_conv_purged_provider",
            ),
        ),
        migrations.AddConstraint(
            model_name="learningconversation",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(("lifecycle__in", ["active", "tombstoned"]), _negated=True)
                    | models.Q(("retention_expires_at__isnull", False))
                ),
                name="chk_conv_retention_required",
            ),
        ),
        migrations.AddIndex(
            model_name="learningconversation",
            index=models.Index(fields=["user", "learning_goal", "lifecycle"], name="idx_conv_scope_lifecycle"),
        ),
        migrations.AddIndex(
            model_name="learningconversation",
            index=models.Index(fields=["provider_cleanup_status", "cleanup_next_attempt_at"], name="idx_conv_cleanup_due"),
        ),
        migrations.AddIndex(
            model_name="learningconversation",
            index=models.Index(fields=["retention_expires_at"], name="idx_conv_retention"),
        ),
        migrations.AddField(
            model_name="userhistory",
            name="conversation",
            field=models.ForeignKey(
                blank=True,
                db_column="conversation_key",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="history_entries",
                to="main.learningconversation",
            ),
        ),
        migrations.AddField(
            model_name="userhistory",
            name="event_key",
            field=models.CharField(blank=True, max_length=96, null=True, unique=True),
        ),
        migrations.AddField(
            model_name="userhistory",
            name="scope_status",
            field=models.CharField(
                choices=[
                    ("active", "active"),
                    ("tombstoned", "tombstoned"),
                    ("legacy_unscoped", "legacy_unscoped"),
                ],
                default="legacy_unscoped",
                max_length=24,
            ),
        ),
        migrations.AddIndex(
            model_name="userhistory",
            index=models.Index(fields=["conversation", "scope_status", "timestamp"], name="idx_hist_conv_scope_time"),
        ),
        migrations.AddField(
            model_name="aijob",
            name="conversation",
            field=models.ForeignKey(
                blank=True,
                db_column="conversation_key",
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name="ai_jobs",
                to="main.learningconversation",
            ),
        ),
        migrations.RunPython(migrate_legacy_conversation_scopes, reverse_history_scope_marker),
        migrations.AddConstraint(
            model_name="userhistory",
            constraint=models.CheckConstraint(
                check=(
                    models.Q(("scope_status", "active"), _negated=True)
                    | (
                        models.Q(("conversation__isnull", False))
                        & models.Q(("learning_goal__isnull", False))
                        & models.Q(("event_key__isnull", False))
                        & models.Q(("event_key", ""), _negated=True)
                    )
                ),
                name="chk_hist_active_scope",
            ),
        ),
        migrations.CreateModel(
            name="LearnerMemoryRecord",
            fields=[
                ("memory_id", models.CharField(max_length=40, primary_key=True, serialize=False)),
                (
                    "kind",
                    models.CharField(
                        choices=[
                            ("explicit_preference", "explicit_preference"),
                            ("goal_constraint", "goal_constraint"),
                            ("learning_strategy_preference", "learning_strategy_preference"),
                        ],
                        max_length=48,
                    ),
                ),
                ("memory_key", models.CharField(max_length=64)),
                ("value_payload", models.JSONField()),
                ("value_hash", models.CharField(max_length=64)),
                ("scope_fingerprint", models.CharField(max_length=64)),
                ("active_scope_key", models.CharField(blank=True, max_length=96, null=True, unique=True)),
                (
                    "lifecycle",
                    models.CharField(
                        choices=[
                            ("proposed", "proposed"),
                            ("active", "active"),
                            ("conflicted", "conflicted"),
                            ("revoked", "revoked"),
                            ("expired", "expired"),
                        ],
                        max_length=24,
                    ),
                ),
                ("conflict_memory_ids", models.JSONField(blank=True, default=list)),
                ("source", models.CharField(max_length=48)),
                ("policy_version", models.CharField(max_length=64)),
                ("expires_at", models.DateTimeField()),
                ("confirmed_at", models.DateTimeField(blank=True, null=True)),
                ("revoked_at", models.DateTimeField(blank=True, null=True)),
                ("mastery_write_authorized", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="learner_memories",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="learner_memories",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={"db_table": "learner_memory_records"},
        ),
        migrations.AddConstraint(
            model_name="learnermemoryrecord",
            constraint=models.UniqueConstraint(
                fields=("user", "learning_goal", "kind", "memory_key", "value_hash"),
                name="uix_lmr_scope_identity",
            ),
        ),
        migrations.AddConstraint(
            model_name="learnermemoryrecord",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_lmr_no_mastery",
            ),
        ),
        migrations.AddConstraint(
            model_name="learnermemoryrecord",
            constraint=models.CheckConstraint(
                check=(
                    (
                        models.Q(("active_scope_key__isnull", False), ("lifecycle", "active"))
                        & models.Q(("active_scope_key", ""), _negated=True)
                    )
                    | (models.Q(("lifecycle", "active"), _negated=True) & models.Q(("active_scope_key__isnull", True)))
                ),
                name="chk_lmr_active_scope_key",
            ),
        ),
        migrations.AddIndex(
            model_name="learnermemoryrecord",
            index=models.Index(fields=["user", "learning_goal", "lifecycle"], name="idx_lmr_scope_lifecycle"),
        ),
        migrations.AddIndex(
            model_name="learnermemoryrecord",
            index=models.Index(fields=["lifecycle", "expires_at"], name="idx_lmr_expiry"),
        ),
        migrations.AddIndex(
            model_name="learnermemoryrecord",
            index=models.Index(fields=["user", "learning_goal", "memory_key"], name="idx_lmr_scope_key"),
        ),
        migrations.CreateModel(
            name="LearnerMemoryDecisionRecord",
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
                ("event_key", models.CharField(blank=True, max_length=96, null=True, unique=True)),
                ("idempotency_key", models.CharField(max_length=64, unique=True)),
                ("status", models.CharField(choices=[("accepted", "accepted"), ("blocked", "blocked")], max_length=24)),
                ("reason_code", models.CharField(max_length=96)),
                ("requested_lifecycle", models.CharField(max_length=24)),
                ("source", models.CharField(max_length=48)),
                ("policy_version", models.CharField(max_length=64)),
                ("request_hash", models.CharField(max_length=64)),
                ("decision_hash", models.CharField(max_length=64)),
                ("decision_metadata", models.JSONField(blank=True, default=dict)),
                ("mastery_write_authorized", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        db_column="learning_goal_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="learner_memory_decisions",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "memory",
                    models.ForeignKey(
                        blank=True,
                        db_column="memory_id",
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="decision_records",
                        to="main.learnermemoryrecord",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        db_column="user_id",
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="learner_memory_decisions",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={"db_table": "learner_memory_decision_records"},
        ),
        migrations.AddConstraint(
            model_name="learnermemorydecisionrecord",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_lmdr_no_mastery",
            ),
        ),
        migrations.AddIndex(
            model_name="learnermemorydecisionrecord",
            index=models.Index(fields=["user", "learning_goal", "created_at"], name="idx_lmdr_scope_time"),
        ),
        migrations.AddIndex(
            model_name="learnermemorydecisionrecord",
            index=models.Index(fields=["status", "created_at"], name="idx_lmdr_status_time"),
        ),
    ]
