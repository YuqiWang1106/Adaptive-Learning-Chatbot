"""Provider-neutral, fail-closed citation grounding policy.

The parser understands the generic shape of Responses output annotations and
file-search results but emits no raw provider identifiers.  A citation is valid
only when it resolves to exactly one item in the caller-supplied retrieval
evidence bundle and that item is still active in the expected owner/goal scope.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, is_dataclass
from enum import Enum
import hashlib
import json
import math
from typing import Any, Mapping, Optional, Sequence, Tuple


CITATION_GROUNDING_POLICY_VERSION = "p2.3-citation-grounding-v2"


class CitationGroundingStatus(str, Enum):
    ACCEPTED = "accepted"
    BLOCKED = "blocked"


@dataclass(frozen=True)
class CitationCandidate:
    """Sanitized candidate extracted from an annotation or search result."""

    candidate_type: str
    ordinal: int
    provider_reference_hash: str
    filename: str
    evidence_hint: str
    answer_start: Optional[int]
    answer_end: Optional[int]
    quoted_text: str
    result_content_sha256: str
    result_score: Optional[float]


@dataclass(frozen=True)
class ValidatedCitation:
    citation_id: str
    evidence_id: str
    source_id: str
    source_version: str
    chunk_id: str
    locator: str
    content_sha256: str
    scope_hash: str
    retrieval_rank: int
    answer_start: Optional[int]
    answer_end: Optional[int]
    filename: str
    quoted_text: str


@dataclass(frozen=True)
class CitationGroundingDecision:
    status: CitationGroundingStatus
    reason_code: str
    policy_version: str
    response_hash: str
    retrieval_bundle_hash: str
    candidates: Tuple[CitationCandidate, ...]
    citations: Tuple[ValidatedCitation, ...]
    mastery_write_authorized: bool = False

    def __post_init__(self) -> None:
        if self.mastery_write_authorized:
            raise ValueError("citation grounding never authorizes mastery writes")

    @property
    def accepted(self) -> bool:
        return self.status is CitationGroundingStatus.ACCEPTED

    def to_metadata(self) -> Mapping[str, Any]:
        return {
            "status": self.status.value,
            "reason_code": self.reason_code,
            "policy_version": self.policy_version,
            "response_hash": self.response_hash,
            "retrieval_bundle_hash": self.retrieval_bundle_hash,
            "candidates": [asdict(candidate) for candidate in self.candidates],
            "citations": [asdict(citation) for citation in self.citations],
            "mastery_write_authorized": False,
        }


@dataclass(frozen=True)
class _ParsedCandidate:
    public: CitationCandidate
    provider_reference: str
    explicit_evidence_hints: Tuple[str, ...]


def _json_safe(value: Any) -> Any:
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if hasattr(value, "model_dump") and callable(value.model_dump):
        return _json_safe(value.model_dump())
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


def _stable_sha256(value: Any) -> str:
    encoded = json.dumps(
        _json_safe(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _value(item: Any, name: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(name, default)
    return getattr(item, name, default)


def _enum_value(value: Any) -> str:
    if isinstance(value, Enum):
        return str(value.value)
    return str(value or "")


def _as_items(value: Any) -> Tuple[Any, ...]:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
        return tuple(value)
    return ()


def _scope_fingerprint(expected_scope: Any) -> str:
    if expected_scope is None:
        return ""
    fingerprint = _value(expected_scope, "fingerprint", None)
    if callable(fingerprint):
        fingerprint = fingerprint()
    return str(fingerprint or expected_scope).strip()


def _evidence_reference(evidence: Any) -> str:
    identity = {
        "source_id": str(_value(evidence, "source_id", "")).strip(),
        "source_version": str(_value(evidence, "source_version", "")).strip(),
        "chunk_id": str(_value(evidence, "chunk_id", "")).strip(),
    }
    if not all(identity.values()):
        return ""
    return f"ev_{_stable_sha256(identity)[:32]}"


def _explicit_evidence_hint(item: Any) -> str:
    direct = str(_value(item, "evidence_id", "") or "").strip()
    if direct:
        return direct
    metadata = _value(item, "metadata", {})
    if not isinstance(metadata, Mapping):
        metadata = {}
    nested = str(metadata.get("evidence_id", "") or "").strip()
    if nested:
        return nested
    source_id = str(
        _value(item, "source_id", "") or metadata.get("source_id", "") or ""
    ).strip()
    source_version = str(
        _value(item, "source_version", "")
        or metadata.get("source_version", "")
        or ""
    ).strip()
    chunk_id = str(
        _value(item, "chunk_id", "") or metadata.get("chunk_id", "") or ""
    ).strip()
    if source_id and source_version and chunk_id:
        return _evidence_reference(
            {
                "source_id": source_id,
                "source_version": source_version,
                "chunk_id": chunk_id,
            }
        )
    return ""


def _provider_reference(item: Any) -> str:
    for field in ("file_id", "document_id", "source_file_id", "provider_reference"):
        value = str(_value(item, field, "") or "").strip()
        if value:
            return value
    return ""


def _content_text(item: Any) -> str:
    direct = _value(item, "text", "")
    if isinstance(direct, str):
        return direct
    content = _value(item, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, Sequence) and not isinstance(content, (str, bytes)):
        pieces = []
        for part in content:
            text = _value(part, "text", "")
            if isinstance(text, str):
                pieces.append(text)
        return "".join(pieces)
    return ""


def _content_sha256(item: Any) -> str:
    # Provider/result metadata is not an integrity boundary: retrieved content
    # may itself inject arbitrary metadata. Always hash the returned bytes here.
    text = _content_text(item)
    return hashlib.sha256(text.encode("utf-8")).hexdigest() if text else ""


def _optional_int(value: Any) -> Optional[int]:
    if value is None or value == "" or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _optional_score(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        score = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(score) or score < 0.0 or score > 1.0:
        return None
    return score


def _candidate(
    *,
    candidate_type: str,
    ordinal: int,
    item: Any,
    evidence_hints: Sequence[str] = (),
) -> _ParsedCandidate:
    provider_reference = _provider_reference(item)
    explicit = _explicit_evidence_hint(item)
    hints = tuple(dict.fromkeys(hint for hint in (explicit, *evidence_hints) if hint))
    start = _optional_int(_value(item, "start_index", None))
    end = _optional_int(_value(item, "end_index", None))
    quoted = str(
        _value(item, "quote", "") or _value(item, "quoted_text", "") or ""
    )
    filename = str(_value(item, "filename", "") or "").strip()
    public = CitationCandidate(
        candidate_type=candidate_type,
        ordinal=ordinal,
        provider_reference_hash=(
            _stable_sha256(provider_reference) if provider_reference else ""
        ),
        filename=filename,
        evidence_hint=hints[0] if len(hints) == 1 else "",
        answer_start=start,
        answer_end=end,
        quoted_text=quoted,
        result_content_sha256=(
            _content_sha256(item) if candidate_type == "file_search_result" else ""
        ),
        result_score=(
            _optional_score(_value(item, "score", None))
            if candidate_type == "file_search_result"
            else None
        ),
    )
    return _ParsedCandidate(public, provider_reference, hints)


def _parse_response(response_body: Any) -> Tuple[str, Tuple[_ParsedCandidate, ...]]:
    output = _as_items(_value(response_body, "output", ()))
    answer_parts: list[str] = []
    parsed: list[_ParsedCandidate] = []
    annotation_ordinal = 0
    result_ordinal = 0

    for output_item in output:
        output_type = str(_value(output_item, "type", "") or "")
        if output_type == "file_search_call" or _value(output_item, "results", None) is not None:
            for result in _as_items(_value(output_item, "results", ())):
                parsed.append(
                    _candidate(
                        candidate_type="file_search_result",
                        ordinal=result_ordinal,
                        item=result,
                    )
                )
                result_ordinal += 1

        for content in _as_items(_value(output_item, "content", ())):
            content_type = str(_value(content, "type", "") or "")
            if content_type in {"output_text", "text"}:
                answer_parts.append(str(_value(content, "text", "") or ""))
            for annotation in _as_items(_value(content, "annotations", ())):
                annotation_type = str(_value(annotation, "type", "") or "")
                if annotation_type not in {"file_citation", "file_path", "citation"}:
                    continue
                parsed.append(
                    _candidate(
                        candidate_type="annotation",
                        ordinal=annotation_ordinal,
                        item=annotation,
                    )
                )
                annotation_ordinal += 1

        # Some SDK/dict fixtures expose message annotations at the item level.
        for annotation in _as_items(_value(output_item, "annotations", ())):
            annotation_type = str(_value(annotation, "type", "") or "")
            if annotation_type not in {"file_citation", "file_path", "citation"}:
                continue
            parsed.append(
                _candidate(
                    candidate_type="annotation",
                    ordinal=annotation_ordinal,
                    item=annotation,
                )
            )
            annotation_ordinal += 1

    fallback_text = _value(response_body, "output_text", "")
    if not answer_parts and isinstance(fallback_text, str):
        answer_parts.append(fallback_text)
    return "".join(answer_parts), tuple(parsed)


def extract_citation_candidates(response_body: Any) -> Tuple[CitationCandidate, ...]:
    """Extract sanitized candidates without claiming that they are valid."""

    _, parsed = _parse_response(response_body)
    return tuple(item.public for item in parsed)


def extract_response_answer_text(response_body: Any) -> str:
    """Return the canonical plain/Markdown answer covered by citation spans.

    Citation offsets always address this exact string.  Rendered HTML is a
    presentation artifact and must never be used as the coordinate space.
    """

    answer_text, _ = _parse_response(response_body)
    return answer_text


def _bundle_evidence(retrieval_bundle: Any) -> Tuple[Any, ...]:
    return _as_items(_value(retrieval_bundle, "evidence", ()))


def _bundle_hash(retrieval_bundle: Any) -> str:
    evidence = []
    for item in _bundle_evidence(retrieval_bundle):
        evidence.append(
            {
                "evidence_id": _evidence_reference(item),
                "source_id": _value(item, "source_id", ""),
                "source_version": _value(item, "source_version", ""),
                "chunk_id": _value(item, "chunk_id", ""),
                "locator": _value(item, "locator", ""),
                "rank": _value(item, "rank", 0),
                "content_sha256": _value(item, "content_sha256", ""),
                "scope_hash": _value(item, "scope_hash", ""),
                "trust_level": _value(item, "trust_level", ""),
                "mastery_write_authorized": bool(
                    _value(item, "mastery_write_authorized", False)
                ),
            }
        )
    return _stable_sha256(
        {
            "status": _enum_value(_value(retrieval_bundle, "status", "")),
            "query_hash": _value(retrieval_bundle, "query_hash", ""),
            "policy_version": _value(retrieval_bundle, "policy_version", ""),
            "evidence": evidence,
        }
    )


def _mapped_hints(mapping: Mapping[str, Any], provider_reference: str) -> Tuple[str, ...]:
    if not provider_reference or provider_reference not in mapping:
        return ()
    raw = mapping[provider_reference]
    if isinstance(raw, str):
        return (raw.strip(),) if raw.strip() else ()
    if isinstance(raw, Sequence) and not isinstance(raw, (str, bytes)):
        return tuple(str(item).strip() for item in raw if str(item).strip())
    return ()


def validate_response_citations(
    response_body: Any,
    *,
    retrieval_bundle: Any,
    expected_scope: Any,
    lifecycle_by_evidence_id: Optional[Mapping[str, Any]],
    provider_reference_map: Optional[Mapping[str, Any]] = None,
    policy_version: str = CITATION_GROUNDING_POLICY_VERSION,
) -> CitationGroundingDecision:
    """Validate every citation/search candidate against one frozen evidence bundle."""

    answer_text, parsed = _parse_response(response_body)
    public_candidates = tuple(item.public for item in parsed)
    response_hash = _stable_sha256(response_body)
    bundle_hash = _bundle_hash(retrieval_bundle)

    def blocked(reason: str) -> CitationGroundingDecision:
        return CitationGroundingDecision(
            status=CitationGroundingStatus.BLOCKED,
            reason_code=reason,
            policy_version=str(policy_version).strip(),
            response_hash=response_hash,
            retrieval_bundle_hash=bundle_hash,
            candidates=public_candidates,
            citations=(),
        )

    if str(policy_version).strip() != CITATION_GROUNDING_POLICY_VERSION:
        return blocked("policy_version_invalid")
    scope_hash = _scope_fingerprint(expected_scope)
    if not scope_hash:
        return blocked("expected_scope_missing")
    status = _enum_value(_value(retrieval_bundle, "status", ""))
    if status != "accepted":
        return blocked("retrieval_not_accepted")
    evidence_rows = _bundle_evidence(retrieval_bundle)
    if not evidence_rows:
        return blocked("retrieval_bundle_missing")

    evidence_by_id: dict[str, Any] = {}
    evidence_by_content_hash: dict[str, list[str]] = {}
    for evidence in evidence_rows:
        evidence_id = _evidence_reference(evidence)
        if not evidence_id or evidence_id in evidence_by_id:
            return blocked("retrieval_identity_invalid")
        if str(_value(evidence, "scope_hash", "")) != scope_hash:
            return blocked("scope_mismatch")
        if bool(_value(evidence, "mastery_write_authorized", False)):
            return blocked("retrieval_claims_mastery_authority")
        if str(_value(evidence, "trust_level", "")) != "untrusted_retrieved_content":
            return blocked("retrieval_trust_invalid")
        evidence_by_id[evidence_id] = evidence
        content_hash = str(_value(evidence, "content_sha256", "") or "")
        if content_hash:
            evidence_by_content_hash.setdefault(content_hash, []).append(evidence_id)

    if not isinstance(lifecycle_by_evidence_id, Mapping):
        return blocked("lifecycle_snapshot_missing")
    for evidence_id in evidence_by_id:
        lifecycle = _enum_value(lifecycle_by_evidence_id.get(evidence_id, ""))
        if not lifecycle:
            return blocked("lifecycle_unknown")
        if lifecycle == "deleted":
            return blocked("source_deleted")
        if lifecycle == "superseded":
            return blocked("source_superseded")
        if lifecycle != "active":
            return blocked("lifecycle_invalid")

    annotations = tuple(
        item for item in parsed if item.public.candidate_type == "annotation"
    )
    if not annotations:
        return blocked("citations_missing")
    if provider_reference_map is None:
        provider_reference_map = {}
    elif not isinstance(provider_reference_map, Mapping):
        return blocked("provider_reference_map_invalid")

    # First resolve search results.  An annotation may safely inherit a search
    # result's unique evidence id only when both share the same provider reference.
    result_hints_by_provider: dict[str, set[str]] = {}
    for item in parsed:
        if item.public.candidate_type != "file_search_result":
            continue
        # Provider-returned metadata is untrusted and may contain an injected
        # evidence_id. Only a server-built map or an exact returned-content hash
        # can bind a result to the frozen retrieval bundle.
        hints = set(_mapped_hints(provider_reference_map, item.provider_reference))
        if item.public.result_content_sha256:
            hints.update(
                evidence_by_content_hash.get(item.public.result_content_sha256, ())
            )
        if not hints:
            return blocked("candidate_unknown")
        if not hints.issubset(evidence_by_id):
            return blocked("candidate_outside_bundle")
        if len(hints) != 1:
            return blocked("candidate_ambiguous")
        resolved_id = next(iter(hints))
        if item.provider_reference:
            result_hints_by_provider.setdefault(item.provider_reference, set()).add(
                resolved_id
            )

    validated = []
    for item in annotations:
        # Never trust an evidence_id copied into a response annotation.
        hints = set(_mapped_hints(provider_reference_map, item.provider_reference))
        hints.update(result_hints_by_provider.get(item.provider_reference, ()))
        if not hints:
            return blocked("citation_unknown")
        if not hints.issubset(evidence_by_id):
            return blocked("citation_outside_bundle")
        if len(hints) != 1:
            return blocked("citation_ambiguous")
        evidence_id = next(iter(hints))
        evidence = evidence_by_id[evidence_id]
        start = item.public.answer_start
        end = item.public.answer_end
        if start is None or end is None:
            return blocked("citation_span_required")
        if start < 0 or end <= start or end > len(answer_text):
            return blocked("citation_span_invalid")
        quoted = item.public.quoted_text
        evidence_text = str(_value(evidence, "text", ""))
        if not quoted:
            return blocked("citation_quote_required")
        if quoted not in evidence_text:
            return blocked("citation_quote_mismatch")
        claim = " ".join(answer_text[start:end].casefold().split())
        normalized_quote = " ".join(quoted.casefold().split())
        if not claim or not normalized_quote or not (
            claim in normalized_quote or normalized_quote in claim
        ):
            # P2 uses a conservative extractive grounding contract. A later
            # calibrated entailment model may admit paraphrases, but an
            # unrelated generated claim must never pass merely because a valid
            # source exists elsewhere in the response.
            return blocked("citation_claim_not_supported")
        citation_id = f"cit_{_stable_sha256({'response_hash': response_hash, 'evidence_id': evidence_id, 'start': start, 'end': end, 'ordinal': item.public.ordinal})[:32]}"
        validated.append(
            ValidatedCitation(
                citation_id=citation_id,
                evidence_id=evidence_id,
                source_id=str(_value(evidence, "source_id", "")),
                source_version=str(_value(evidence, "source_version", "")),
                chunk_id=str(_value(evidence, "chunk_id", "")),
                locator=str(_value(evidence, "locator", "")),
                content_sha256=str(_value(evidence, "content_sha256", "")),
                scope_hash=str(_value(evidence, "scope_hash", "")),
                retrieval_rank=int(_value(evidence, "rank", 0) or 0),
                answer_start=start,
                answer_end=end,
                filename=item.public.filename,
                quoted_text=quoted,
            )
        )

    covered = [False] * len(answer_text)
    for citation in validated:
        if citation.answer_start is None or citation.answer_end is None:
            continue
        for index in range(citation.answer_start, citation.answer_end):
            covered[index] = True
    for index, character in enumerate(answer_text):
        if character.isalnum() and not covered[index]:
            return blocked("answer_claim_without_citation")

    return CitationGroundingDecision(
        status=CitationGroundingStatus.ACCEPTED,
        reason_code="accepted",
        policy_version=str(policy_version).strip(),
        response_hash=response_hash,
        retrieval_bundle_hash=bundle_hash,
        candidates=public_candidates,
        citations=tuple(validated),
    )


__all__ = [
    "CITATION_GROUNDING_POLICY_VERSION",
    "CitationCandidate",
    "CitationGroundingDecision",
    "CitationGroundingStatus",
    "ValidatedCitation",
    "extract_citation_candidates",
    "extract_response_answer_text",
    "validate_response_citations",
]
