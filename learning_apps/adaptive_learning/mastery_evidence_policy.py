"""Pure, fail-closed admission policy for long-term mastery evidence.

The policy deliberately sits below concept identity and above mastery fusion.  A
caller may provide a candidate event, but it cannot opt an event into long-term
mastery by setting a metadata flag.  The database service records the decision
and only admitted evidence is read by the mastery calculator.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from .constants import DIMENSIONS


MASTERY_EVIDENCE_POLICY_VERSION = "mastery_evidence_policy_v2"


@dataclass(frozen=True)
class MasteryEvidenceDecision:
    accepted: bool
    reason_code: str
    policy_version: str = MASTERY_EVIDENCE_POLICY_VERSION
    source: str = ""
    confidence: float = 0.0

    @property
    def admission_status(self) -> str:
        return "accepted" if self.accepted else "blocked"


def _clamp01(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return round(max(0.0, min(1.0, number)), 4)


def _valid_dimension_scores(value: Any) -> bool:
    if not isinstance(value, Mapping):
        return False
    for dimension in DIMENSIONS:
        if dimension not in value:
            return False
        raw = value[dimension]
        if isinstance(raw, bool):
            return False
        try:
            number = float(raw)
        except (TypeError, ValueError):
            return False
        if number < 0.0 or number > 100.0:
            return False
    return True


def evaluate_mastery_evidence(
    *,
    source: str,
    confidence: Any,
    dimension_scores: Any,
    metadata: Mapping[str, Any] | None = None,
    probe_source: str = "probe_response",
) -> MasteryEvidenceDecision:
    """Return a deterministic admission decision.

    Long-term observed mastery is Probe-only. Self-assessment, chat, Micro
    Checks, navigation and model-generated suggestions belong to separate
    learner-state projections and cannot update mastery.
    """

    clean_source = str(source or "")
    clean_confidence = _clamp01(confidence)
    if clean_source != probe_source:
        return MasteryEvidenceDecision(False, "source_not_admitted", source=clean_source, confidence=clean_confidence)

    minimum = 0.65
    if clean_confidence < minimum:
        return MasteryEvidenceDecision(
            False,
            "confidence_below_threshold",
            source=clean_source,
            confidence=clean_confidence,
        )
    if not _valid_dimension_scores(dimension_scores):
        return MasteryEvidenceDecision(
            False,
            "dimension_scores_incomplete_or_invalid",
            source=clean_source,
            confidence=clean_confidence,
        )

    # Metadata is evidence provenance, not an authorization switch.  These
    # optional checks catch malformed structured events without requiring old
    # direct service callers to manufacture UI-only probe fields.
    details = metadata if isinstance(metadata, Mapping) else {}
    if clean_source == probe_source and details.get("target_dimension"):
        if str(details.get("target_dimension")) not in DIMENSIONS:
            return MasteryEvidenceDecision(
                False,
                "probe_target_dimension_invalid",
                source=clean_source,
                confidence=clean_confidence,
            )

    return MasteryEvidenceDecision(True, "admitted", source=clean_source, confidence=clean_confidence)


def evidence_is_admitted(metadata: Any) -> bool:
    """Strictly recognize the persisted policy decision on an event."""

    if not isinstance(metadata, dict):
        return False
    return (
        metadata.get("mastery_evidence_admission") == "accepted"
        and metadata.get("mastery_policy_version") == MASTERY_EVIDENCE_POLICY_VERSION
        and metadata.get("mastery_update_accepted") is True
    )
