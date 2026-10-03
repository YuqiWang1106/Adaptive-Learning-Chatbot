from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


DIMENSIONS = ("facts", "strategies", "procedures", "rationales")
ALLOWED_STATES = frozenset({"can_explain", "uncertain", "not_started"})
MIN_RESPONSE_CHARACTERS = 12
USABLE_RESPONSE_CHARACTERS = 40
MAX_CONTEXT_CHARACTERS = 1000
MAX_DIMENSION_TEXT_CHARACTERS = 4000
MAX_TARGET_TASK_CHARACTERS = 1000
MIN_TARGET_TASK_CHARACTERS = 12


def _text(value: Any) -> str:
    return str(value or "").replace("\x00", "").strip()


def _meaningful_length(value: str) -> int:
    return sum(1 for character in value if not character.isspace())


def _sufficiency(state: str, statement: str, uncertainties: str) -> str:
    if state == "not_started":
        return "explicit_not_started"
    length = _meaningful_length(f"{statement}{uncertainties}")
    return "usable" if length >= USABLE_RESPONSE_CHARACTERS else "thin"


@dataclass(frozen=True)
class SubmissionValidation:
    valid: bool
    snapshot: dict[str, Any]
    errors: dict[str, str]


def validate_self_assessment_submission(post_data: Mapping[str, Any]) -> SubmissionValidation:
    """Normalize and validate the V2 learner-authored submission.

    The same function is called by the HTTP boundary and the worker so a
    forged or stale queued payload cannot bypass the field contract.
    """

    target_task = _text(
        post_data.get("target_task")
        or post_data.get("context")
        or post_data.get("problem")
    )
    context = target_task
    errors: dict[str, str] = {}
    if _meaningful_length(target_task) < MIN_TARGET_TASK_CHARACTERS:
        errors["target_task"] = (
            "Describe a specific task or problem type using at least "
            f"{MIN_TARGET_TASK_CHARACTERS} non-space characters."
        )
    elif len(target_task) > MAX_TARGET_TASK_CHARACTERS:
        errors["target_task"] = (
            f"Keep the target task to {MAX_TARGET_TASK_CHARACTERS} characters or fewer."
        )

    blueprint_id = _text(post_data.get("reference_blueprint_id"))
    blueprint_sha256 = _text(post_data.get("reference_blueprint_sha256"))
    target_task_sha256 = _text(post_data.get("target_task_sha256"))
    scope_confirmed = _text(post_data.get("scope_confirmed"))
    if not blueprint_id.isdigit():
        errors["reference_blueprint_id"] = "Confirm the target scope before continuing."
    if len(blueprint_sha256) != 64 or len(target_task_sha256) != 64:
        errors["reference_blueprint_id"] = "The confirmed scope is incomplete. Prepare it again."
    if scope_confirmed != "1":
        errors["reference_blueprint_id"] = "Confirm the target scope before continuing."

    dimensions: dict[str, dict[str, str]] = {}
    evidence_sufficiency: dict[str, str] = {}
    for dimension in DIMENSIONS:
        state = _text(post_data.get(f"{dimension}_state"))
        statement = _text(post_data.get(f"{dimension}_statement"))
        uncertainties = _text(post_data.get(f"{dimension}_uncertainties"))
        if state not in ALLOWED_STATES:
            errors[f"{dimension}_state"] = "Choose one starting point."
            state = ""
        if len(statement) > MAX_DIMENSION_TEXT_CHARACTERS:
            errors[f"{dimension}_statement"] = (
                f"Keep this response to {MAX_DIMENSION_TEXT_CHARACTERS} characters or fewer."
            )
        if len(uncertainties) > MAX_DIMENSION_TEXT_CHARACTERS:
            errors[f"{dimension}_uncertainties"] = (
                f"Keep this response to {MAX_DIMENSION_TEXT_CHARACTERS} characters or fewer."
            )
        response_length = _meaningful_length(f"{statement}{uncertainties}")
        if state == "can_explain" and _meaningful_length(statement) < MIN_RESPONSE_CHARACTERS:
            errors[f"{dimension}_statement"] = (
                "Describe what you can explain using at least 12 non-space characters."
            )
        elif state == "uncertain" and response_length < MIN_RESPONSE_CHARACTERS:
            errors[f"{dimension}_statement"] = (
                "Describe what you know and what is uncertain using at least 12 non-space characters."
            )

        if state == "not_started":
            statement = "I have not learned this yet."
            uncertainties = ""
        dimensions[dimension] = {
            "state": state,
            "statement": statement,
            "uncertainties": uncertainties,
        }
        evidence_sufficiency[dimension] = _sufficiency(
            state,
            statement,
            uncertainties,
        )

    snapshot = {
        "version": "self_assessment_v2_statement_contract",
        "target_task": target_task,
        "target_task_sha256": target_task_sha256,
        "reference_blueprint_id": int(blueprint_id) if blueprint_id.isdigit() else None,
        "reference_blueprint_sha256": blueprint_sha256,
        "scope_confirmed": scope_confirmed == "1",
        "context": context,
        "dimensions": dimensions,
        "evidence_sufficiency": evidence_sufficiency,
    }
    return SubmissionValidation(valid=not errors, snapshot=snapshot, errors=errors)


def snapshot_to_post_data(snapshot: Mapping[str, Any], *, learning_goal_id: int | None = None) -> dict[str, str]:
    dimensions = snapshot.get("dimensions") if isinstance(snapshot.get("dimensions"), Mapping) else {}
    target_task = _text(snapshot.get("target_task") or snapshot.get("context"))
    payload = {
        "target_task": target_task,
        "context": target_task,
        "target_task_sha256": _text(snapshot.get("target_task_sha256")),
        "reference_blueprint_id": _text(snapshot.get("reference_blueprint_id")),
        "reference_blueprint_sha256": _text(snapshot.get("reference_blueprint_sha256")),
        "scope_confirmed": "1" if snapshot.get("scope_confirmed") else "",
    }
    if learning_goal_id:
        payload["learning_goal_id"] = str(learning_goal_id)
    for dimension in DIMENSIONS:
        item = dimensions.get(dimension) if isinstance(dimensions, Mapping) else {}
        item = item if isinstance(item, Mapping) else {}
        payload[f"{dimension}_state"] = _text(item.get("state"))
        payload[f"{dimension}_statement"] = _text(item.get("statement"))
        payload[f"{dimension}_uncertainties"] = _text(item.get("uncertainties"))
    return payload


__all__ = [
    "ALLOWED_STATES",
    "DIMENSIONS",
    "MAX_TARGET_TASK_CHARACTERS",
    "MIN_TARGET_TASK_CHARACTERS",
    "SubmissionValidation",
    "snapshot_to_post_data",
    "validate_self_assessment_submission",
]
