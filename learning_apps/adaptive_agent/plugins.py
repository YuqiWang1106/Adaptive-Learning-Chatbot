from __future__ import annotations

from dataclasses import dataclass

from django.db import transaction
from django.utils import timezone

from learning_apps.application.contracts import stable_sha256
from .models import MCPAccessGrant, PluginRegistration


BUILTIN_PLUGIN_MANIFESTS = (
    {
        "plugin_id": "core_adaptive_learning",
        "version": "1.2.0",
        "skills": ["socratic_coaching", "worked_example", "misconception_repair", "retrieval_practice", "assessment_reflection", "adaptive_quiz_session"],
        "tools": ["learning.get_goal_context", "learning.resolve_current_concept", "adaptive.get_teaching_state", "adaptive.list_learning_priorities", "assessment.get_recent_quizzes", "assessment.get_latest", "probe.get_recent_diagnostics", "learning.get_concept_map", "conversation.get_recent_learning_events", "quiz.propose", "memory.propose"],
        "mcp_servers": [],
        "oauth_scopes": [],
        "approval_policy": "all_proposals_require_student_approval",
        "runtime_compatibility": ">=2.0,<3.0",
        "ui_cards": ["plan", "tool", "evidence", "clarification", "approval"],
        "trust_root": "builtin_developer_review",
    },
    {
        "plugin_id": "curriculum_materials",
        "version": "1.0.0",
        "skills": ["source_grounded_explanation"],
        "tools": ["knowledge.retrieve_evidence", "learning.get_concept_map"],
        "mcp_servers": ["adaptive-learning"],
        "oauth_scopes": [],
        "approval_policy": "read_only",
        "runtime_compatibility": ">=2.0,<3.0",
        "ui_cards": ["evidence", "citation"],
        "trust_root": "builtin_developer_review",
    },
    {
        "plugin_id": "review_scheduler",
        "version": "1.0.0",
        "skills": ["spaced_review"],
        "tools": ["review.get_due_reviews", "review_plan.propose"],
        "mcp_servers": [],
        "oauth_scopes": [],
        "approval_policy": "writes_require_student_approval",
        "runtime_compatibility": ">=2.0,<3.0",
        "ui_cards": ["review", "approval"],
        "trust_root": "builtin_developer_review",
    },
)


def _sealed_manifest(manifest: dict) -> tuple[dict, str]:
    payload = dict(manifest)
    digest = stable_sha256(payload)
    payload["integrity_sha256"] = digest
    payload["signature_scheme"] = "builtin_trust_root_sha256"
    return payload, digest


def ensure_builtin_plugins() -> list[PluginRegistration]:
    rows = []
    for manifest in BUILTIN_PLUGIN_MANIFESTS:
        sealed, digest = _sealed_manifest(manifest)
        row, _created = PluginRegistration.objects.get_or_create(
            plugin_id=manifest["plugin_id"],
            version=manifest["version"],
            defaults={"manifest": sealed, "manifest_sha256": digest, "status": PluginRegistration.STATUS_ACTIVE},
        )
        if row.manifest_sha256 != digest or row.manifest != sealed:
            raise ValueError("builtin_plugin_integrity_mismatch")
        older_ids = list(
            PluginRegistration.objects.filter(
                plugin_id=manifest["plugin_id"],
                status=PluginRegistration.STATUS_ACTIVE,
            )
            .exclude(id=row.id)
            .values_list("id", flat=True)
        )
        if older_ids:
            now = timezone.now()
            PluginRegistration.objects.filter(id__in=older_ids).update(
                status=PluginRegistration.STATUS_DISABLED,
                disabled_at=now,
            )
            MCPAccessGrant.objects.filter(plugin_id__in=older_ids, revoked_at__isnull=True).update(
                revoked_at=now
            )
        rows.append(row)
    return rows


def disable_plugin(plugin_id: str) -> int:
    with transaction.atomic():
        rows = list(PluginRegistration.objects.select_for_update().filter(plugin_id=plugin_id, status=PluginRegistration.STATUS_ACTIVE))
        if not rows:
            return 0
        now = timezone.now()
        PluginRegistration.objects.filter(id__in=[row.id for row in rows]).update(
            status=PluginRegistration.STATUS_DISABLED,
            disabled_at=now,
        )
        MCPAccessGrant.objects.filter(plugin_id__in=[row.id for row in rows], revoked_at__isnull=True).update(revoked_at=now)
    return len(rows)


@dataclass(frozen=True)
class ActivePluginPolicy:
    plugin_versions: tuple[tuple[str, str], ...]
    skill_ids: frozenset[str]
    tool_names: frozenset[str]


def active_plugin_policy() -> ActivePluginPolicy:
    ensure_builtin_plugins()
    rows = PluginRegistration.objects.filter(status=PluginRegistration.STATUS_ACTIVE).order_by(
        "plugin_id", "version"
    )
    skills: set[str] = set()
    tools: set[str] = set()
    versions = []
    for row in rows:
        manifest = row.manifest if isinstance(row.manifest, dict) else {}
        skills.update(str(value) for value in manifest.get("skills", []) if value)
        tools.update(str(value) for value in manifest.get("tools", []) if value)
        versions.append((row.plugin_id, row.version))
    return ActivePluginPolicy(
        plugin_versions=tuple(versions),
        skill_ids=frozenset(skills),
        tool_names=frozenset(tools),
    )
