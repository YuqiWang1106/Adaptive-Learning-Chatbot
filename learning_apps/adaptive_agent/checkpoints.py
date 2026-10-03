from __future__ import annotations

from .crypto import seal_json, unseal_json
from .models import AgentRunCheckpoint, LearningAgentRun


def save_checkpoint(run: LearningAgentRun, state: dict, *, sdk_schema_version: str = "1") -> AgentRunCheckpoint:
    encrypted, digest = seal_json(state)
    checkpoint, _created = AgentRunCheckpoint.objects.update_or_create(
        run=run,
        defaults={
            "encrypted_state": encrypted,
            "state_sha256": digest,
            "sdk_schema_version": sdk_schema_version[:24],
            "expires_at": run.expires_at,
        },
    )
    return checkpoint


def load_checkpoint(run: LearningAgentRun) -> dict:
    checkpoint = AgentRunCheckpoint.objects.get(run=run)
    payload = unseal_json(checkpoint.encrypted_state, checkpoint.state_sha256)
    if not isinstance(payload, dict):
        raise ValueError("checkpoint_payload_invalid")
    return payload


def delete_checkpoint(run: LearningAgentRun) -> None:
    AgentRunCheckpoint.objects.filter(run=run).delete()
