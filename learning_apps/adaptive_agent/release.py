from __future__ import annotations

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from learning_apps.adaptive_agent.tool_catalog import capability_registry
from learning_apps.application.contracts import stable_sha256
from .models import (
    AgentInterruption,
    AgentReleaseManifest,
    AgentRunCheckpoint,
    LearningAgentRun,
)
from .output import TutorTurnOutput
from .plugins import ensure_builtin_plugins
from .skills import BUILTIN_SKILLS, skill_summaries


PROMPT_VERSION = "adaptive-tutor-agent-v2.5.0"
RUNTIME_VERSION = "persistent-agent-runtime-v2.3.0"


def current_release_manifest() -> AgentReleaseManifest:
    from .adaptive_context import ADAPTIVE_CONTEXT_POLICY_VERSION
    from .conversation_memory import MEMORY_POLICY_VERSION, MEMORY_SCHEMA_VERSION
    from .sdk_adapter import AGENT_INSTRUCTIONS

    catalog = capability_registry()
    plugins = ensure_builtin_plugins()
    payload = {
        "release_name": settings.LEARNING_AGENT_RELEASE_NAME,
        "model": settings.LEARNING_AGENT_MODEL,
        "reasoning_effort": settings.LEARNING_AGENT_REASONING_EFFORT,
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": stable_sha256(AGENT_INSTRUCTIONS),
        "output_schema_sha256": stable_sha256(TutorTurnOutput.model_json_schema()),
        "runtime_version": RUNTIME_VERSION,
        "adaptive_context_policy_version": ADAPTIVE_CONTEXT_POLICY_VERSION,
        "conversation_memory_policy_version": MEMORY_POLICY_VERSION,
        "conversation_memory_schema_version": MEMORY_SCHEMA_VERSION,
        "capability_catalog_version": catalog.catalog_version,
        "capabilities": [
            {
                "name": spec.name,
                "version": spec.version,
                "authority": spec.authority.value,
                "requires_approval": spec.requires_approval,
            }
            for spec in catalog.all()
        ],
        "skills": skill_summaries(),
        "skill_instruction_sha256": {
            key: stable_sha256(BUILTIN_SKILLS[key].full_instructions())
            for key in sorted(BUILTIN_SKILLS)
        },
        "plugins": [
            {
                "plugin_id": row.plugin_id,
                "version": row.version,
                "manifest_sha256": row.manifest_sha256,
                "status": row.status,
            }
            for row in plugins
        ],
    }
    digest = stable_sha256(payload)
    with transaction.atomic():
        row, _created = AgentReleaseManifest.objects.get_or_create(
            manifest_sha256=digest,
            defaults={
                "release_name": settings.LEARNING_AGENT_RELEASE_NAME,
                "model": settings.LEARNING_AGENT_MODEL,
                "reasoning_effort": settings.LEARNING_AGENT_REASONING_EFFORT,
                "prompt_version": PROMPT_VERSION,
                "runtime_version": RUNTIME_VERSION,
                "capability_catalog_version": catalog.catalog_version,
                "manifest": payload,
                "active": True,
            },
        )
        if not row.active:
            row.active = True
            row.save(update_fields=["active"])
        active_release_ids = list(
            AgentReleaseManifest.objects.exclude(release_id=row.release_id)
            .filter(active=True)
            .values_list("release_id", flat=True)
        )
        AgentReleaseManifest.objects.filter(release_id__in=active_release_ids).update(active=False)
        now = timezone.now()
        unfinished = LearningAgentRun.objects.exclude(
            release_id=row.release_id,
        ).exclude(status__in=LearningAgentRun.TERMINAL_STATUSES)
        run_ids = list(unfinished.values_list("run_id", flat=True))
        if run_ids:
            AgentInterruption.objects.filter(
                run_id__in=run_ids,
                status=AgentInterruption.STATUS_PENDING,
            ).update(status=AgentInterruption.STATUS_EXPIRED, resolved_at=now)
            AgentRunCheckpoint.objects.filter(run_id__in=run_ids).delete()
            unfinished.update(
                status=LearningAgentRun.STATUS_EXPIRED,
                error_code="agent_release_retired",
                completed_at=now,
            )
    return row


def release_is_available(release: AgentReleaseManifest) -> bool:
    """Fail closed instead of silently resuming with drifting code artifacts."""

    from .adaptive_context import ADAPTIVE_CONTEXT_POLICY_VERSION
    from .conversation_memory import MEMORY_POLICY_VERSION, MEMORY_SCHEMA_VERSION
    from .sdk_adapter import AGENT_INSTRUCTIONS

    if not release.active:
        return False
    payload = release.manifest if isinstance(release.manifest, dict) else {}
    if stable_sha256(payload) != release.manifest_sha256:
        return False
    expected = {
        "prompt_version": PROMPT_VERSION,
        "prompt_sha256": stable_sha256(AGENT_INSTRUCTIONS),
        "output_schema_sha256": stable_sha256(TutorTurnOutput.model_json_schema()),
        "runtime_version": RUNTIME_VERSION,
        "adaptive_context_policy_version": ADAPTIVE_CONTEXT_POLICY_VERSION,
        "conversation_memory_policy_version": MEMORY_POLICY_VERSION,
        "conversation_memory_schema_version": MEMORY_SCHEMA_VERSION,
        "capability_catalog_version": capability_registry().catalog_version,
        "skill_instruction_sha256": {
            key: stable_sha256(BUILTIN_SKILLS[key].full_instructions())
            for key in sorted(BUILTIN_SKILLS)
        },
    }
    return all(payload.get(key) == value for key, value in expected.items())
