# P6.7 replaces the blocking understanding workflow with Agent Micro Checks and
# converts pre-existing pending formal probes into explicit user-facing offers.

from datetime import timedelta
import hashlib

import learning_apps.persistence.models
import django.db.models.deletion
from django.db import migrations, models
from django.utils import timezone


LEGACY_PREFERENCE_KEYS = {
    "skips_visual_count",
    "formal_notation_correct",
    "mode_scores",
    "struggles_abstract_solves_worked",
    "technical_term_accuracy",
}


def migrate_and_clean_legacy_state(apps, schema_editor):
    AdaptiveInteractionEvent = apps.get_model("main", "AdaptiveInteractionEvent")
    AdaptiveProbe = apps.get_model("main", "AdaptiveProbe")
    AdaptiveProbeOffer = apps.get_model("main", "AdaptiveProbeOffer")
    LearnerBehaviorEvidence = apps.get_model("main", "LearnerBehaviorEvidence")
    LearnerPreferenceState = apps.get_model("main", "LearnerPreferenceState")
    UserHistory = apps.get_model("main", "UserHistory")
    AgentConversationMemory = apps.get_model("adaptive_agent", "AgentConversationMemory")

    # Preserve every generated pending question behind an explicit Offer.  If
    # legacy data contains more than one pending probe for one goal, only the
    # newest remains active and older rows are retained as superseded offers.
    seen_scopes = set()
    pending_probes = AdaptiveProbe.objects.filter(status="pending").order_by(
        "user_id",
        "learning_goal_id",
        "-created_at",
        "-id",
    )
    for probe in pending_probes.iterator():
        scope = (probe.user_id, probe.learning_goal_id)
        is_active = scope not in seen_scopes
        seen_scopes.add(scope)
        fingerprint = hashlib.sha256(
            f"legacy:{probe.user_id}:{probe.learning_goal_id}:{probe.id}".encode("utf-8")
        ).hexdigest()
        due_at = probe.due_at or probe.created_at or timezone.now()
        AdaptiveProbeOffer.objects.create(
            offer_id=f"apo_legacy_{probe.id}",
            user_id=probe.user_id,
            learning_goal_id=probe.learning_goal_id,
            policy_version="probe-offer-migration-v1",
            due_trigger="legacy_pending",
            due_reason="Existing diagnostic review migrated to an explicit offer.",
            target_concept_key=probe.concept_key,
            target_concept_label=str(probe.concept_key or "").replace("_", " ").title()[:180],
            target_dimension=probe.target_dimension,
            candidate_score=1.0,
            candidate_components_snapshot={"migration": True, "legacy_probe_id": probe.id},
            mastery_state_fingerprint=fingerprint,
            status="ready" if is_active else "superseded",
            source="p6_7_migration",
            idempotency_key=f"legacy-probe:{probe.id}",
            open_scope_key=f"{probe.user_id}:{probe.learning_goal_id}" if is_active else None,
            resulting_probe_id=probe.id,
            expires_at=probe.expires_at or (due_at + timedelta(days=7)),
            mastery_write_authorized=False,
        )

    AdaptiveInteractionEvent.objects.filter(source="understanding_check").delete()
    LearnerBehaviorEvidence.objects.filter(event_type="understanding_signal").delete()
    UserHistory.objects.filter(answer_style="understanding").delete()

    for state in LearnerPreferenceState.objects.all().iterator():
        inferred = state.inferred_preferences if isinstance(state.inferred_preferences, dict) else {}
        cleaned = {key: value for key, value in inferred.items() if key not in LEGACY_PREFERENCE_KEYS}
        if cleaned != inferred:
            state.inferred_preferences = cleaned
            state.save(update_fields=["inferred_preferences", "updated_at"])

    # Memory summaries may contain removed understanding-history heuristics.
    # They are deterministic caches and will rebuild from retained valid turns.
    AgentConversationMemory.objects.all().delete()


def reverse_cleanup(apps, schema_editor):
    # Deleted low-quality inferred state is intentionally not reconstructed.
    return None


class Migration(migrations.Migration):

    dependencies = [
        ("adaptive_agent", "0011_learningagentrun_adaptive_context_manifest_and_more"),
        ("main", "0029_p5_external_capability_control"),
    ]

    operations = [
        migrations.CreateModel(
            name="AdaptiveProbeOffer",
            fields=[
                (
                    "offer_id",
                    models.CharField(
                        default=learning_apps.persistence.models.generate_probe_offer_id,
                        editable=False,
                        max_length=48,
                        primary_key=True,
                        serialize=False,
                    ),
                ),
                ("policy_version", models.CharField(max_length=64)),
                ("due_trigger", models.CharField(max_length=32)),
                ("due_reason", models.CharField(max_length=120)),
                ("target_concept_key", models.CharField(max_length=160)),
                ("target_concept_label", models.CharField(blank=True, default="", max_length=180)),
                ("target_dimension", models.CharField(max_length=32)),
                ("candidate_score", models.FloatField(default=0.0)),
                ("candidate_components_snapshot", models.JSONField(blank=True, default=dict)),
                ("mastery_state_fingerprint", models.CharField(max_length=64)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("pending", "pending"),
                            ("accepted", "accepted"),
                            ("generating", "generating"),
                            ("ready", "ready"),
                            ("consumed", "consumed"),
                            ("snoozed", "snoozed"),
                            ("dismissed", "dismissed"),
                            ("expired", "expired"),
                            ("superseded", "superseded"),
                        ],
                        default="pending",
                        max_length=32,
                    ),
                ),
                ("source", models.CharField(default="review_scheduler", max_length=64)),
                ("idempotency_key", models.CharField(max_length=96, unique=True)),
                ("open_scope_key", models.CharField(blank=True, max_length=160, null=True, unique=True)),
                ("offered_at", models.DateTimeField(auto_now_add=True)),
                ("last_presented_at", models.DateTimeField(blank=True, null=True)),
                ("presented_count", models.PositiveIntegerField(default=0)),
                ("accepted_at", models.DateTimeField(blank=True, null=True)),
                ("snoozed_at", models.DateTimeField(blank=True, null=True)),
                ("snoozed_until", models.DateTimeField(blank=True, null=True)),
                ("dismissed_at", models.DateTimeField(blank=True, null=True)),
                ("expired_at", models.DateTimeField(blank=True, null=True)),
                ("expires_at", models.DateTimeField()),
                ("last_error_code", models.CharField(blank=True, default="", max_length=64)),
                ("mastery_write_authorized", models.BooleanField(default=False, editable=False)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "learning_goal",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adaptive_probe_offers",
                        to="main.learninggoal",
                    ),
                ),
                (
                    "resulting_probe",
                    models.OneToOneField(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name="source_offer",
                        to="main.adaptiveprobe",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="adaptive_probe_offers",
                        to="main.userprofile",
                    ),
                ),
            ],
            options={"db_table": "adaptive_probe_offers"},
        ),
        migrations.AddIndex(
            model_name="adaptiveprobeoffer",
            index=models.Index(fields=["user", "learning_goal", "status"], name="idx_apo_scope_status"),
        ),
        migrations.AddIndex(
            model_name="adaptiveprobeoffer",
            index=models.Index(fields=["status", "expires_at"], name="idx_apo_status_expiry"),
        ),
        migrations.AddConstraint(
            model_name="adaptiveprobeoffer",
            constraint=models.CheckConstraint(
                check=models.Q(("mastery_write_authorized", False)),
                name="chk_apo_no_mastery",
            ),
        ),
        migrations.RunPython(migrate_and_clean_legacy_state, reverse_cleanup),
        migrations.RemoveField(model_name="learninggoal", name="pending_remediation"),
        migrations.AlterField(
            model_name="adaptiveinteractionevent",
            name="source",
            field=models.CharField(
                choices=[
                    ("self_assessment_baseline", "self_assessment_baseline"),
                    ("probe_response", "probe_response"),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="learnerbehaviorevidence",
            name="event_type",
            field=models.CharField(
                choices=[
                    ("preference_state_updated", "preference_state_updated"),
                    ("knowledge_map_snapshot", "knowledge_map_snapshot"),
                    ("explicit_preference", "explicit_preference"),
                    ("micro_check_response", "micro_check_response"),
                ],
                max_length=48,
            ),
        ),
        migrations.DeleteModel(name="AnswerTypeWeight"),
    ]
