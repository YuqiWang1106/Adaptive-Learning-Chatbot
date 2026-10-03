"""Fail-closed evidence admission for structured self-assessment reports.

This module is deliberately pure policy code.  It does not call a model, read or
write Django models, or grant mastery updates.  Callers supply the assessment,
the model-produced report, and (when used) an already scoped retrieval bundle.
The returned decision is an auditable gate for later persistence integration.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import hashlib
import json
import math
import re
import unicodedata
from typing import Any, Mapping, Optional, Sequence, Tuple


SELF_ASSESSMENT_EVIDENCE_POLICY_VERSION = "p2.3-self-assessment-evidence-v4-leddo-csa"

REQUIRED_DIMENSIONS = ("Facts", "Strategies", "Procedures", "Rationales")
ALLOWED_LABELS = frozenset(
    {
        "Know-Know",
        "Know-Don't Know",
        "False Knowledge",
        "Omission",
        "Irrelevant Knowledge",
    }
)
ALLOWED_BASES = frozenset(
    {"student_text", "material_evidence", "general_domain", "mixed"}
)
REQUIRED_MATERIAL_TRUST_SIGNALS = frozenset(
    {
        "retrieved_content_is_untrusted",
        "instruction_data_boundary_applied",
        "evidence_allow_list_enforced",
    }
)
BLOCKING_TRUST_SIGNALS = frozenset(
    {
        "instruction_injection_detected",
        "untrusted_instruction_execution",
        "unknown_evidence_reference",
        "scope_mismatch",
        "deleted_source",
        "superseded_source",
    }
)


class EvidenceAdmissionStatus(str, Enum):
    ACCEPTED = "accepted"
    BLOCKED = "blocked"


class GroundingMode(str, Enum):
    GENERAL_DOMAIN = "general_domain"
    RETRIEVED_MATERIAL = "retrieved_material"


@dataclass(frozen=True)
class DimensionValidation:
    dimension: str
    valid: bool
    aspect_count: int
    reason: str


@dataclass(frozen=True)
class SelfAssessmentEvidenceDecision:
    """A replayable policy decision; it is never a mastery capability."""

    status: EvidenceAdmissionStatus
    reason_code: str
    policy_version: str
    grounding_mode: GroundingMode
    assessment_hash: str
    structured_report_hash: str
    retrieval_bundle_hash: str
    lifecycle_snapshot_hash: str
    decision_hash: str
    evidence_ids: Tuple[str, ...]
    trust_signals: Tuple[str, ...]
    dimension_validation: Tuple[DimensionValidation, ...]
    mastery_write_authorized: bool = False

    def __post_init__(self) -> None:
        if self.mastery_write_authorized:
            raise ValueError("self-assessment admission never authorizes mastery writes")

    @property
    def accepted(self) -> bool:
        return self.status is EvidenceAdmissionStatus.ACCEPTED

    def to_metadata(self) -> Mapping[str, Any]:
        """Return storage-safe metadata without raw assessment or material text."""

        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "grounding_mode": self.grounding_mode.value,
            "assessment_hash": self.assessment_hash,
            "structured_report_hash": self.structured_report_hash,
            "retrieval_bundle_hash": self.retrieval_bundle_hash,
            "lifecycle_snapshot_hash": self.lifecycle_snapshot_hash,
            "decision_hash": self.decision_hash,
            "evidence_ids": list(self.evidence_ids),
            "trust_signals": list(self.trust_signals),
            "dimension_validation": [asdict(item) for item in self.dimension_validation],
            "mastery_write_authorized": False,
        }


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, Mapping):
        return {
            str(key): _json_safe(item)
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        }
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    if isinstance(value, (set, frozenset)):
        normalized = [_json_safe(item) for item in value]
        return sorted(
            normalized,
            key=lambda item: json.dumps(
                item, sort_keys=True, separators=(",", ":"), ensure_ascii=False
            ),
        )
    if isinstance(value, float) and not math.isfinite(value):
        return {"__non_finite_float__": repr(value)}
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def stable_sha256(value: Any) -> str:
    payload = json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "")


def evidence_reference(evidence: Any) -> str:
    """Return a provider-neutral opaque reference for one retrieval evidence row."""

    identity = {
        "source_id": str(_value(evidence, "source_id", "")).strip(),
        "source_version": str(_value(evidence, "source_version", "")).strip(),
        "chunk_id": str(_value(evidence, "chunk_id", "")).strip(),
    }
    if not all(identity.values()):
        raise ValueError("evidence requires source_id, source_version, and chunk_id")
    return f"ev_{stable_sha256(identity)[:32]}"


def _scope_fingerprint(expected_scope: Any) -> str:
    if expected_scope is None:
        return ""
    fingerprint = _value(expected_scope, "fingerprint", None)
    if callable(fingerprint):
        fingerprint = fingerprint()
    if fingerprint:
        return str(fingerprint).strip()
    return str(expected_scope).strip()


def _retrieval_evidence(retrieval_outcome: Any) -> Tuple[Any, ...]:
    if retrieval_outcome is None:
        return ()
    evidence = _value(retrieval_outcome, "evidence", ())
    if not isinstance(evidence, Sequence) or isinstance(evidence, (str, bytes)):
        return ()
    return tuple(evidence)


def retrieval_bundle_sha256(retrieval_outcome: Any) -> str:
    """Hash trace metadata and content hashes, never the retrieved raw text."""

    if retrieval_outcome is None:
        return stable_sha256(None)
    evidence_rows = []
    for item in _retrieval_evidence(retrieval_outcome):
        scores = _value(item, "scores", {})
        evidence_rows.append(
            {
                "evidence_id": evidence_reference(item),
                "source_id": _value(item, "source_id", ""),
                "source_version": _value(item, "source_version", ""),
                "chunk_id": _value(item, "chunk_id", ""),
                "locator": _value(item, "locator", ""),
                "rank": _value(item, "rank", 0),
                "scores": {
                    "lexical": _value(scores, "lexical", None),
                    "dense": _value(scores, "dense", None),
                    "hybrid": _value(scores, "hybrid", None),
                },
                "query_hash": _value(item, "query_hash", ""),
                "policy_version": _value(item, "policy_version", ""),
                "content_sha256": _value(item, "content_sha256", ""),
                "scope_hash": _value(item, "scope_hash", ""),
                "trust_level": _value(item, "trust_level", ""),
                "mastery_write_authorized": bool(
                    _value(item, "mastery_write_authorized", False)
                ),
            }
        )
    return stable_sha256(
        {
            "status": _enum_value(_value(retrieval_outcome, "status", "")),
            "query_hash": _value(retrieval_outcome, "query_hash", ""),
            "policy_version": _value(retrieval_outcome, "policy_version", ""),
            "reason": _value(retrieval_outcome, "reason", ""),
            "failure_code": _value(retrieval_outcome, "failure_code", ""),
            "evidence": evidence_rows,
        }
    )


def _number_in_range(value: Any, minimum: float, maximum: float) -> bool:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return math.isfinite(float(value)) and minimum <= float(value) <= maximum


_INSTRUCTION_INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\b(?:ignore|disregard|forget|discard)\s+(?:(?:all|every)\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|directions?|rules?|messages?|prompts?)\b",
        r"\b(?:ignore|disregard|forget|discard)\s+(?:(?:all|every)\s+)?(?:instructions?|directions?|rules?|messages?|prompts?)\s+(?:above|before|previously)\b",
        r"\b(?:do\s+not|don['’]t)\s+(?:follow|obey)\s+(?:the\s+)?(?:previous|prior|above|earlier)\s+(?:instructions?|directions?|rules?|messages?|prompts?)\b",
        r"\b(?:system|developer)\s+(?:prompt|message)\b",
        r"<\/?(?:system|developer|assistant|tool)(?:\s|>)",
        r"\b(?:call|invoke|execute)\s+(?:the\s+)?(?:tool|function|mcp)\b",
        r"\b(?:override|bypass)\s+(?:the\s+)?(?:policy|safety|instructions?)\b",
        r"\bmastery_write_authorized\b",
    )
)

_INVISIBLE_INSTRUCTION_CATEGORIES = frozenset({"Cf", "Cc"})
# Only an omission can be inferred from a reference blueprint plus the absence
# of a statement.  Every other label asserts something the learner actually
# expressed (correct, uncertain, false, or irrelevant) and therefore requires
# an exact quote from the authenticated submission.
_STUDENT_EXPRESSION_LABELS = ALLOWED_LABELS - {"Omission"}


def _normalized_text(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).casefold().split())


def _normalized_instruction_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    # Format/control characters include zero-width joiners and directionality
    # controls that otherwise split dangerous tokens while remaining invisible.
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) not in _INVISIBLE_INSTRUCTION_CATEGORIES
        or character in "\t\n\r"
    )
    return " ".join(normalized.casefold().split())


def contains_instruction_injection(value: Any) -> bool:
    text = _normalized_instruction_text(value)
    return any(pattern.search(text) for pattern in _INSTRUCTION_INJECTION_PATTERNS)


def _learner_response_values(value: Any) -> list[str]:
    if isinstance(value, str):
        cleaned = value.strip()
        return [cleaned] if cleaned else []
    if isinstance(value, Mapping):
        return [
            text
            for nested in value.values()
            for text in _learner_response_values(nested)
        ]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return [
            text
            for nested in value
            for text in _learner_response_values(nested)
        ]
    return []


def _student_submission_text(assessment_payload: Mapping[str, Any]) -> str:
    # Only fields that represent the student's submitted answer are eligible.
    # Prompt/question/template text must never make a fabricated quote valid.
    for key in ("answer", "response", "student_text", "submission"):
        value = assessment_payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    submitted = assessment_payload.get("self_assessment")
    if isinstance(submitted, Mapping):
        parts: list[str] = []
        parts.extend(_learner_response_values(submitted.get("context")))
        knowledge_types = submitted.get("knowledge_types")
        if isinstance(knowledge_types, Sequence) and not isinstance(
            knowledge_types, (str, bytes)
        ):
            for item in knowledge_types:
                if not isinstance(item, Mapping):
                    continue
                for key in ("statement", "uncertainties"):
                    parts.extend(_learner_response_values(item.get(key)))
    else:
        parts = []
    self_evaluation = assessment_payload.get("self_evaluation")
    parts.extend(_learner_response_values(self_evaluation))
    return "\n".join(parts)


def _dimension_map(report: Any) -> Tuple[Optional[Mapping[str, Any]], str]:
    if not isinstance(report, Mapping):
        return None, "structured_report_not_mapping"
    by_name: dict[str, Any] = {}
    expected = {name.casefold(): name for name in REQUIRED_DIMENSIONS}
    for raw_name, content in report.items():
        normalized = str(raw_name).strip().casefold()
        canonical = expected.get(normalized)
        if canonical is None:
            return None, "unexpected_dimension"
        if canonical in by_name:
            return None, "duplicate_dimension"
        by_name[canonical] = content
    if set(by_name) != set(REQUIRED_DIMENSIONS):
        return None, "missing_dimension"
    return by_name, "ok"


def _validate_report(
    report: Any,
    *,
    grounding_mode: GroundingMode,
    allowed_evidence_ids: frozenset[str],
    allowed_evidence_ids_by_dimension: Optional[Mapping[str, frozenset[str]]],
    evidence_text_by_id: Mapping[str, str],
    student_submission_text: str,
    self_report_states: Optional[Mapping[str, str]],
    require_csa_contract: bool,
) -> Tuple[Tuple[DimensionValidation, ...], str, frozenset[str]]:
    dimensions, shape_reason = _dimension_map(report)
    if dimensions is None:
        return tuple(
            DimensionValidation(name, False, 0, shape_reason)
            for name in REQUIRED_DIMENSIONS
        ), shape_reason, frozenset()

    validations = []
    used_evidence: set[str] = set()
    first_error = ""
    for name in REQUIRED_DIMENSIONS:
        dimension = dimensions[name]
        reason = "ok"
        aspects = _value(dimension, "aspects", None)
        if not isinstance(dimension, Mapping):
            aspects = None
            reason = "dimension_not_mapping"
        elif not isinstance(aspects, Sequence) or isinstance(aspects, (str, bytes)):
            reason = "aspects_not_list"
        elif not aspects:
            reason = "aspects_empty"

        if reason == "ok":
            for aspect in aspects:
                if not isinstance(aspect, Mapping):
                    reason = "aspect_not_mapping"
                    break
                if not str(aspect.get("aspect", "")).strip():
                    reason = "aspect_name_missing"
                    break
                labels = aspect.get("labels")
                if not isinstance(labels, (list, tuple)) or not labels:
                    reason = "labels_not_list"
                    break
                if any(str(label) not in ALLOWED_LABELS for label in labels):
                    reason = "label_not_allowed"
                    break
                if require_csa_contract and len(labels) != 1:
                    reason = "csa_category_not_mutually_exclusive"
                    break
                reported_state = (
                    str(self_report_states.get(name, ""))
                    if isinstance(self_report_states, Mapping)
                    else ""
                )
                if (
                    require_csa_contract
                    and reported_state == "not_started"
                    and list(labels) != ["Know-Don't Know"]
                ):
                    reason = "explicit_not_started_category_invalid"
                    break
                if reported_state == "not_started" and "Omission" in labels:
                    reason = "explicit_not_started_as_omission"
                    break
                if not _number_in_range(aspect.get("severity"), 1.0, 5.0):
                    reason = "severity_invalid"
                    break
                if not _number_in_range(aspect.get("confidence"), 0.0, 1.0):
                    reason = "confidence_invalid"
                    break
                if not str(aspect.get("explanation", "")).strip():
                    reason = "explanation_missing"
                    break
                basis = str(aspect.get("basis", "")).strip()
                if basis not in ALLOWED_BASES:
                    reason = "basis_invalid"
                    break
                raw_ids = aspect.get("evidence_ids", [])
                if not isinstance(raw_ids, (list, tuple)) or any(
                    not isinstance(item, str) or not item.strip() for item in raw_ids
                ):
                    reason = "evidence_ids_invalid"
                    break
                evidence_ids = tuple(item.strip() for item in raw_ids)
                if len(evidence_ids) != len(set(evidence_ids)):
                    reason = "duplicate_evidence_id"
                    break
                dimension_allowed = (
                    allowed_evidence_ids_by_dimension.get(name, frozenset())
                    if isinstance(allowed_evidence_ids_by_dimension, Mapping)
                    else allowed_evidence_ids
                )
                if not set(evidence_ids).issubset(dimension_allowed):
                    reason = "unknown_evidence_reference"
                    break
                uses_material = basis in {"material_evidence", "mixed"}
                student_expression_labels = (
                    set(str(label) for label in labels) & _STUDENT_EXPRESSION_LABELS
                )
                if student_expression_labels and basis not in {"student_text", "mixed"}:
                    reason = "learner_label_requires_student_basis"
                    break
                if uses_material and not evidence_ids:
                    reason = "material_basis_without_evidence"
                    break
                if not uses_material and evidence_ids:
                    reason = "evidence_without_material_basis"
                    break
                if grounding_mode is GroundingMode.GENERAL_DOMAIN and uses_material:
                    reason = "material_basis_in_general_mode"
                    break
                if require_csa_contract and list(labels) == ["Omission"]:
                    if basis not in {"material_evidence", "general_domain"}:
                        reason = "csa_omission_basis_invalid"
                        break
                    if str(aspect.get("student_quote", "")).strip():
                        reason = "csa_omission_has_student_quote"
                        break
                if uses_material:
                    quotes = aspect.get("evidence_quotes")
                    if isinstance(quotes, Mapping):
                        normalized_quotes = {
                            str(key).strip(): str(value or "").strip()
                            for key, value in quotes.items()
                            if str(key).strip()
                        }
                    elif len(evidence_ids) == 1 and str(
                        aspect.get("evidence_quote", "")
                    ).strip():
                        normalized_quotes = {
                            evidence_ids[0]: str(aspect.get("evidence_quote") or "").strip()
                        }
                    else:
                        normalized_quotes = {}
                    if set(normalized_quotes) != set(evidence_ids):
                        reason = "evidence_quote_missing"
                        break
                    for evidence_id, quote in normalized_quotes.items():
                        normalized_quote = _normalized_text(quote)
                        normalized_source = _normalized_text(
                            evidence_text_by_id.get(evidence_id, "")
                        )
                        if (
                            len(re.sub(r"\W+", "", normalized_quote)) < 8
                            or not normalized_source
                            or normalized_quote not in normalized_source
                        ):
                            reason = "evidence_quote_not_in_source"
                            break
                    if reason != "ok":
                        break
                if basis in {"student_text", "mixed"} and not str(
                    aspect.get("student_quote", "")
                ).strip():
                    reason = "student_quote_missing"
                    break
                if basis in {"student_text", "mixed"}:
                    quote = _normalized_text(aspect.get("student_quote", ""))
                    submission = _normalized_text(student_submission_text)
                    if not submission or quote not in submission:
                        reason = "student_quote_not_in_submission"
                        break
                used_evidence.update(evidence_ids)

        aspect_count = len(aspects) if isinstance(aspects, (list, tuple)) else 0
        validations.append(DimensionValidation(name, reason == "ok", aspect_count, reason))
        if reason != "ok" and not first_error:
            first_error = reason

    return tuple(validations), first_error or "ok", frozenset(used_evidence)


def evaluate_self_assessment_evidence(
    *,
    assessment_payload: Mapping[str, Any],
    structured_report: Mapping[str, Any],
    grounding_mode: GroundingMode | str = GroundingMode.GENERAL_DOMAIN,
    retrieval_outcome: Any = None,
    expected_scope: Any = None,
    lifecycle_by_evidence_id: Optional[Mapping[str, Any]] = None,
    allowed_evidence_ids_by_dimension: Optional[Mapping[str, Sequence[str]]] = None,
    is_partial: bool = False,
    dimension_errors: Optional[Mapping[str, Any]] = None,
    provider_error: str = "",
    evaluation_error: str = "",
    trust_signals: Sequence[str] = (),
    policy_version: str = SELF_ASSESSMENT_EVIDENCE_POLICY_VERSION,
    require_csa_contract: bool = False,
) -> SelfAssessmentEvidenceDecision:
    """Validate whether a report may be treated as admitted assessment evidence.

    Accepted means only that the structured evidence passed this boundary.  It
    never means the learner's mastery may be updated.
    """

    try:
        mode = GroundingMode(grounding_mode)
    except ValueError:
        mode = GroundingMode.GENERAL_DOMAIN
        invalid_mode = True
    else:
        invalid_mode = False

    assessment_hash = stable_sha256(assessment_payload)
    report_hash = stable_sha256(structured_report)
    bundle_hash = retrieval_bundle_sha256(retrieval_outcome)
    if isinstance(trust_signals, Sequence) and not isinstance(
        trust_signals, (str, bytes)
    ):
        normalized_signals = tuple(
            sorted({str(item).strip() for item in trust_signals if str(item).strip()})
        )
    else:
        normalized_signals = ("invalid_trust_signals",)
    evidence = _retrieval_evidence(retrieval_outcome)
    try:
        evidence_ids = tuple(evidence_reference(item) for item in evidence)
    except ValueError:
        evidence_ids = ()
    evidence_text_by_id = {
        evidence_reference(item): str(_value(item, "text", "") or "")
        for item in evidence
    } if evidence_ids else {}

    blank_validation = tuple(
        DimensionValidation(name, False, 0, "not_evaluated")
        for name in REQUIRED_DIMENSIONS
    )

    lifecycle_snapshot_hash = stable_sha256(
        {
            str(key): _enum_value(value)
            for key, value in sorted(
                (lifecycle_by_evidence_id or {}).items(), key=lambda item: str(item[0])
            )
        }
        if isinstance(lifecycle_by_evidence_id, Mapping)
        else None
    )

    def decision(
        status: EvidenceAdmissionStatus,
        reason: str,
        validations: Tuple[DimensionValidation, ...] = blank_validation,
    ) -> SelfAssessmentEvidenceDecision:
        decision_hash = stable_sha256(
            {
                "status": status.value,
                "reason": reason,
                "policy_version": SELF_ASSESSMENT_EVIDENCE_POLICY_VERSION,
                "grounding_mode": mode.value,
                "assessment_hash": assessment_hash,
                "structured_report_hash": report_hash,
                "retrieval_bundle_hash": bundle_hash,
                "lifecycle_snapshot_hash": lifecycle_snapshot_hash,
                "trust_signals": normalized_signals,
            }
        )
        return SelfAssessmentEvidenceDecision(
            status=status,
            reason_code=reason,
            policy_version=SELF_ASSESSMENT_EVIDENCE_POLICY_VERSION,
            grounding_mode=mode,
            assessment_hash=assessment_hash,
            structured_report_hash=report_hash,
            retrieval_bundle_hash=bundle_hash,
            lifecycle_snapshot_hash=lifecycle_snapshot_hash,
            decision_hash=decision_hash,
            evidence_ids=evidence_ids,
            trust_signals=normalized_signals,
            dimension_validation=validations,
        )

    if str(policy_version).strip() != SELF_ASSESSMENT_EVIDENCE_POLICY_VERSION:
        return decision(EvidenceAdmissionStatus.BLOCKED, "policy_version_invalid")
    if invalid_mode:
        return decision(EvidenceAdmissionStatus.BLOCKED, "grounding_mode_invalid")
    if not isinstance(assessment_payload, Mapping) or not assessment_payload:
        return decision(EvidenceAdmissionStatus.BLOCKED, "assessment_payload_invalid")
    if str(provider_error).strip():
        return decision(EvidenceAdmissionStatus.BLOCKED, "provider_error")
    if str(evaluation_error).strip():
        return decision(EvidenceAdmissionStatus.BLOCKED, "evaluation_error")
    if is_partial:
        return decision(EvidenceAdmissionStatus.BLOCKED, "partial_report")
    if dimension_errors:
        return decision(EvidenceAdmissionStatus.BLOCKED, "dimension_errors_present")
    student_submission = _student_submission_text(assessment_payload)
    if contains_instruction_injection(student_submission):
        return decision(EvidenceAdmissionStatus.BLOCKED, "instruction_injection_detected")

    retrieval_status = _enum_value(_value(retrieval_outcome, "status", ""))
    if retrieval_status == "failed":
        return decision(EvidenceAdmissionStatus.BLOCKED, "retrieval_provider_error")
    signal_set = frozenset(normalized_signals)
    if signal_set & BLOCKING_TRUST_SIGNALS:
        return decision(EvidenceAdmissionStatus.BLOCKED, "trust_violation")
    if not signal_set.issubset(
        REQUIRED_MATERIAL_TRUST_SIGNALS | BLOCKING_TRUST_SIGNALS
    ):
        return decision(EvidenceAdmissionStatus.BLOCKED, "trust_signal_unknown")

    expected_scope_hash = _scope_fingerprint(expected_scope)
    allowed_ids = frozenset(evidence_ids)
    if mode is GroundingMode.GENERAL_DOMAIN:
        if evidence or retrieval_status == "accepted":
            return decision(
                EvidenceAdmissionStatus.BLOCKED, "retrieval_present_in_general_mode"
            )
    else:
        if retrieval_outcome is None:
            return decision(EvidenceAdmissionStatus.BLOCKED, "retrieval_bundle_missing")
        if retrieval_status != "accepted" or not evidence:
            return decision(EvidenceAdmissionStatus.BLOCKED, "retrieval_not_accepted")
        if not expected_scope_hash:
            return decision(EvidenceAdmissionStatus.BLOCKED, "expected_scope_missing")
        if len(evidence_ids) != len(evidence) or len(set(evidence_ids)) != len(evidence_ids):
            return decision(EvidenceAdmissionStatus.BLOCKED, "retrieval_identity_invalid")
        for item in evidence:
            if _value(item, "scope_hash", "") != expected_scope_hash:
                return decision(EvidenceAdmissionStatus.BLOCKED, "scope_mismatch")
            if bool(_value(item, "mastery_write_authorized", False)):
                return decision(
                    EvidenceAdmissionStatus.BLOCKED,
                    "retrieval_claims_mastery_authority",
                )
            if _value(item, "trust_level", "") != "untrusted_retrieved_content":
                return decision(EvidenceAdmissionStatus.BLOCKED, "retrieval_trust_invalid")
            if contains_instruction_injection(_value(item, "text", "")):
                return decision(
                    EvidenceAdmissionStatus.BLOCKED,
                    "instruction_injection_detected",
                )
        if not isinstance(lifecycle_by_evidence_id, Mapping):
            return decision(EvidenceAdmissionStatus.BLOCKED, "lifecycle_snapshot_missing")
        for evidence_id in evidence_ids:
            lifecycle = _enum_value(lifecycle_by_evidence_id.get(evidence_id, ""))
            if not lifecycle:
                return decision(EvidenceAdmissionStatus.BLOCKED, "lifecycle_unknown")
            if lifecycle == "deleted":
                return decision(EvidenceAdmissionStatus.BLOCKED, "source_deleted")
            if lifecycle == "superseded":
                return decision(EvidenceAdmissionStatus.BLOCKED, "source_superseded")
            if lifecycle != "active":
                return decision(EvidenceAdmissionStatus.BLOCKED, "lifecycle_invalid")
        if not REQUIRED_MATERIAL_TRUST_SIGNALS.issubset(signal_set):
            return decision(
                EvidenceAdmissionStatus.BLOCKED, "trust_boundary_incomplete"
            )

    validations, validation_reason, used_evidence = _validate_report(
        structured_report,
        grounding_mode=mode,
        allowed_evidence_ids=allowed_ids,
        allowed_evidence_ids_by_dimension={
            str(dimension): frozenset(str(item) for item in ids)
            for dimension, ids in allowed_evidence_ids_by_dimension.items()
        }
        if isinstance(allowed_evidence_ids_by_dimension, Mapping)
        else None,
        evidence_text_by_id=evidence_text_by_id,
        student_submission_text=student_submission,
        self_report_states={
            str(item.get("type") or "").strip().capitalize(): str(
                item.get("state") or ""
            ).strip()
            for item in (
                (
                    assessment_payload.get("self_assessment", {}).get(
                        "knowledge_types",
                        [],
                    )
                    if isinstance(
                        assessment_payload.get("self_assessment"),
                        Mapping,
                    )
                    else []
                )
            )
            if isinstance(item, Mapping)
        },
        require_csa_contract=bool(require_csa_contract),
    )
    if validation_reason != "ok":
        return decision(EvidenceAdmissionStatus.BLOCKED, validation_reason, validations)
    if mode is GroundingMode.RETRIEVED_MATERIAL and not used_evidence:
        return decision(
            EvidenceAdmissionStatus.BLOCKED,
            "retrieved_evidence_not_used",
            validations,
        )
    return decision(EvidenceAdmissionStatus.ACCEPTED, "accepted", validations)


__all__ = [
    "ALLOWED_BASES",
    "contains_instruction_injection",
    "ALLOWED_LABELS",
    "BLOCKING_TRUST_SIGNALS",
    "DimensionValidation",
    "EvidenceAdmissionStatus",
    "GroundingMode",
    "REQUIRED_DIMENSIONS",
    "REQUIRED_MATERIAL_TRUST_SIGNALS",
    "SELF_ASSESSMENT_EVIDENCE_POLICY_VERSION",
    "SelfAssessmentEvidenceDecision",
    "evaluate_self_assessment_evidence",
    "evidence_reference",
    "retrieval_bundle_sha256",
    "stable_sha256",
]
