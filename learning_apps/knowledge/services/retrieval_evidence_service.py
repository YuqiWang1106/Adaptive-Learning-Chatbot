"""Deterministic retrieval contracts for grounded learning-material evidence.

This module deliberately has no Django/model/provider dependencies.  A caller must
first enforce owner and learning-goal scope, then pass only eligible chunks here.
Retrieval evidence is read-only context and never grants long-term mastery writes.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from enum import Enum
import hashlib
import math
import re
import unicodedata
from typing import Mapping, Optional, Protocol, Sequence, Tuple


RETRIEVAL_POLICY_VERSION = "p2.2-retrieval-evidence-v1"
_TOKEN_RE = re.compile(r"[^\W_]+(?:['\N{RIGHT SINGLE QUOTATION MARK}-][^\W_]+)*", re.UNICODE)
_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "but",
        "by",
        "can",
        "do",
        "does",
        "for",
        "from",
        "had",
        "has",
        "have",
        "how",
        "i",
        "if",
        "in",
        "into",
        "is",
        "it",
        "its",
        "of",
        "on",
        "or",
        "that",
        "the",
        "their",
        "them",
        "this",
        "to",
        "us",
        "was",
        "what",
        "when",
        "which",
        "while",
        "why",
        "with",
    }
)


class RetrievalStatus(str, Enum):
    """A retrieval call's explicit terminal state."""

    ACCEPTED = "accepted"
    ABSTAINED = "abstained"
    FAILED = "failed"


class RetrievalLifecycle(str, Enum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    DELETED = "deleted"


class DenseScoreProvider(Protocol):
    """Provider-neutral dense scoring boundary.

    Implementations return a normalized score in ``[0, 1]`` for each chunk they
    can score.  Missing chunk ids receive a dense score of zero.  Raising an
    exception fails closed; retrieval does not silently fall back to lexical.
    """

    def score(self, query: str, chunks: Sequence["RetrievalChunk"]) -> Mapping[str, float]:
        ...


def normalize_query(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value or "").casefold()
    return " ".join(normalized.split())


def query_sha256(value: str) -> str:
    return hashlib.sha256(normalize_query(value).encode("utf-8")).hexdigest()


def _required_text(name: str, value: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise ValueError(f"{name} is required")
    return cleaned


def _bounded_score(name: str, value: float) -> float:
    score = float(value)
    if not math.isfinite(score) or score < 0.0 or score > 1.0:
        raise ValueError(f"{name} must be a finite score in [0, 1]")
    return score


@dataclass(frozen=True)
class RetrievalScope:
    """The authorization scope resolved by the caller before retrieval."""

    owner_id: str
    learning_goal_id: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "owner_id", _required_text("owner_id", self.owner_id))
        object.__setattr__(
            self,
            "learning_goal_id",
            _required_text("learning_goal_id", self.learning_goal_id),
        )

    @property
    def fingerprint(self) -> str:
        value = f"{self.owner_id}\0{self.learning_goal_id}"
        return hashlib.sha256(value.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class RetrievalChunk:
    """An immutable, already-authorized retrieval candidate."""

    source_id: str
    source_version: str
    chunk_id: str
    locator: str
    text: str
    owner_id: str
    learning_goal_id: str
    lifecycle: RetrievalLifecycle = RetrievalLifecycle.ACTIVE
    content_sha256: str = ""

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "source_version",
            "chunk_id",
            "locator",
            "text",
            "owner_id",
            "learning_goal_id",
        ):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        try:
            lifecycle = RetrievalLifecycle(self.lifecycle)
        except ValueError as exc:
            raise ValueError("lifecycle must be active, superseded, or deleted") from exc
        object.__setattr__(self, "lifecycle", lifecycle)
        digest = str(self.content_sha256 or "").strip().lower()
        if digest and not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        if not digest:
            digest = hashlib.sha256(self.text.encode("utf-8")).hexdigest()
        object.__setattr__(self, "content_sha256", digest)


@dataclass(frozen=True)
class RetrievalScores:
    lexical: float
    dense: Optional[float]
    hybrid: float

    def __post_init__(self) -> None:
        object.__setattr__(self, "lexical", _bounded_score("lexical", self.lexical))
        if self.dense is not None:
            object.__setattr__(self, "dense", _bounded_score("dense", self.dense))
        object.__setattr__(self, "hybrid", _bounded_score("hybrid", self.hybrid))


@dataclass(frozen=True)
class RetrievalEvidence:
    """Traceable evidence passed to an answer generator.

    ``mastery_write_authorized`` is a constant false capability marker.  Reading
    or retrieving content is not evidence that a learner knows the content.
    """

    source_id: str
    source_version: str
    chunk_id: str
    locator: str
    rank: int
    scores: RetrievalScores
    query_hash: str
    policy_version: str
    text: str
    content_sha256: str
    scope_hash: str
    mastery_write_authorized: bool = False
    trust_level: str = "untrusted_retrieved_content"

    def __post_init__(self) -> None:
        for name in (
            "source_id",
            "source_version",
            "chunk_id",
            "locator",
            "query_hash",
            "policy_version",
            "text",
            "content_sha256",
            "scope_hash",
        ):
            object.__setattr__(self, name, _required_text(name, getattr(self, name)))
        if self.rank < 1:
            raise ValueError("rank must be at least one")
        if not re.fullmatch(r"[0-9a-f]{64}", self.query_hash):
            raise ValueError("query_hash must be a lowercase SHA-256 digest")
        if not re.fullmatch(r"[0-9a-f]{64}", self.content_sha256):
            raise ValueError("content_sha256 must be a lowercase SHA-256 digest")
        if not re.fullmatch(r"[0-9a-f]{64}", self.scope_hash):
            raise ValueError("scope_hash must be a lowercase SHA-256 digest")
        if self.mastery_write_authorized:
            raise ValueError("retrieval evidence can never authorize mastery writes")
        if self.trust_level != "untrusted_retrieved_content":
            raise ValueError("retrieved evidence must remain explicitly untrusted")


@dataclass(frozen=True)
class RetrievalPolicy:
    version: str = RETRIEVAL_POLICY_VERSION
    max_results: int = 5
    min_score: float = 0.12
    lexical_weight: float = 0.55
    dense_weight: float = 0.45
    abstain_on_ambiguity: bool = False
    min_top_margin: float = 0.02

    def __post_init__(self) -> None:
        object.__setattr__(self, "version", _required_text("version", self.version))
        if self.max_results < 1 or self.max_results > 100:
            raise ValueError("max_results must be between 1 and 100")
        object.__setattr__(self, "min_score", _bounded_score("min_score", self.min_score))
        object.__setattr__(
            self,
            "min_top_margin",
            _bounded_score("min_top_margin", self.min_top_margin),
        )
        lexical_weight = _bounded_score("lexical_weight", self.lexical_weight)
        dense_weight = _bounded_score("dense_weight", self.dense_weight)
        if lexical_weight + dense_weight <= 0.0:
            raise ValueError("at least one retrieval weight must be positive")
        object.__setattr__(self, "lexical_weight", lexical_weight)
        object.__setattr__(self, "dense_weight", dense_weight)


@dataclass(frozen=True)
class RetrievalOutcome:
    status: RetrievalStatus
    evidence: Tuple[RetrievalEvidence, ...]
    query_hash: str
    policy_version: str
    reason: str
    failure_code: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[0-9a-f]{64}", self.query_hash):
            raise ValueError("query_hash must be a lowercase SHA-256 digest")
        _required_text("policy_version", self.policy_version)
        _required_text("reason", self.reason)
        if self.status is RetrievalStatus.ACCEPTED and not self.evidence:
            raise ValueError("accepted retrieval must contain evidence")
        if self.status is not RetrievalStatus.ACCEPTED and self.evidence:
            raise ValueError("abstained or failed retrieval cannot contain evidence")
        if self.status is RetrievalStatus.FAILED and not self.failure_code:
            raise ValueError("failed retrieval must carry a stable failure_code")
        if self.status is not RetrievalStatus.FAILED and self.failure_code:
            raise ValueError("only failed retrieval may carry failure_code")


def _tokens(value: str) -> Tuple[str, ...]:
    return tuple(
        token
        for token in _TOKEN_RE.findall(normalize_query(value))
        if token not in _STOPWORDS
    )


def _lexical_scores(query: str, chunks: Sequence[RetrievalChunk]) -> Mapping[str, float]:
    """Return bounded BM25-derived scores without external state or randomness."""

    query_tokens = _tokens(query)
    if not query_tokens or not chunks:
        return {chunk.chunk_id: 0.0 for chunk in chunks}

    tokenized = [_tokens(chunk.text) for chunk in chunks]
    document_frequency: Counter[str] = Counter()
    for tokens in tokenized:
        document_frequency.update(set(tokens))
    average_length = sum(len(tokens) for tokens in tokenized) / max(1, len(tokenized))
    unique_query_tokens = tuple(dict.fromkeys(query_tokens))
    document_count = len(chunks)
    normalized_query = normalize_query(query)
    scores: dict[str, float] = {}

    for chunk, tokens in zip(chunks, tokenized):
        frequencies = Counter(tokens)
        raw_score = 0.0
        matched = 0
        for token in unique_query_tokens:
            frequency = frequencies[token]
            if not frequency:
                continue
            matched += 1
            df = document_frequency[token]
            inverse_document_frequency = math.log(
                1.0 + (document_count - df + 0.5) / (df + 0.5)
            )
            length_normalizer = 1.2 * (
                1.0 - 0.75 + 0.75 * len(tokens) / max(1.0, average_length)
            )
            raw_score += inverse_document_frequency * (
                frequency * (1.2 + 1.0) / (frequency + length_normalizer)
            )

        coverage = matched / len(unique_query_tokens)
        bounded_bm25 = 1.0 - math.exp(-raw_score / 4.0)
        phrase_bonus = 0.08 if normalized_query in normalize_query(chunk.text) else 0.0
        score = bounded_bm25 * (0.6 + 0.4 * coverage) + phrase_bonus
        scores[chunk.chunk_id] = min(1.0, max(0.0, score))
    return scores


def _outcome(
    *,
    status: RetrievalStatus,
    query_hash: str,
    policy: RetrievalPolicy,
    reason: str,
    evidence: Tuple[RetrievalEvidence, ...] = (),
    failure_code: str = "",
) -> RetrievalOutcome:
    return RetrievalOutcome(
        status=status,
        evidence=evidence,
        query_hash=query_hash,
        policy_version=policy.version,
        reason=reason,
        failure_code=failure_code,
    )


def retrieve_evidence(
    query: str,
    chunks: Sequence[RetrievalChunk],
    *,
    scope: Optional[RetrievalScope] = None,
    dense_provider: Optional[DenseScoreProvider] = None,
    policy: Optional[RetrievalPolicy] = None,
) -> RetrievalOutcome:
    """Rank already-scoped chunks and return explicit evidence or a safe terminal state."""

    effective_policy = policy or RetrievalPolicy()
    digest = query_sha256(query)
    if scope is None:
        return _outcome(
            status=RetrievalStatus.FAILED,
            query_hash=digest,
            policy=effective_policy,
            reason="authorization_scope_required",
            failure_code="scope_required",
        )
    if not normalize_query(query):
        return _outcome(
            status=RetrievalStatus.ABSTAINED,
            query_hash=digest,
            policy=effective_policy,
            reason="empty_query",
        )
    if not chunks:
        return _outcome(
            status=RetrievalStatus.ABSTAINED,
            query_hash=digest,
            policy=effective_policy,
            reason="empty_corpus",
        )
    eligible_chunks = tuple(
        chunk
        for chunk in chunks
        if chunk.owner_id == scope.owner_id
        and chunk.learning_goal_id == scope.learning_goal_id
        and chunk.lifecycle is RetrievalLifecycle.ACTIVE
    )
    if not eligible_chunks:
        return _outcome(
            status=RetrievalStatus.ABSTAINED,
            query_hash=digest,
            policy=effective_policy,
            reason="no_eligible_chunks",
        )
    chunk_ids = [chunk.chunk_id for chunk in eligible_chunks]
    if len(set(chunk_ids)) != len(chunk_ids):
        return _outcome(
            status=RetrievalStatus.FAILED,
            query_hash=digest,
            policy=effective_policy,
            reason="candidate_validation_failed",
            failure_code="duplicate_chunk_id",
        )

    lexical_scores = _lexical_scores(query, eligible_chunks)
    dense_scores: Optional[Mapping[str, float]] = None
    if dense_provider is not None:
        try:
            dense_scores = dense_provider.score(query, eligible_chunks)
            unknown_ids = set(dense_scores) - set(chunk_ids)
            if unknown_ids:
                raise ValueError("dense provider returned an unknown chunk id")
            dense_scores = {
                chunk_id: _bounded_score("dense", value)
                for chunk_id, value in dense_scores.items()
            }
        except Exception:
            return _outcome(
                status=RetrievalStatus.FAILED,
                query_hash=digest,
                policy=effective_policy,
                reason="dense_scoring_failed",
                failure_code="dense_provider_error",
            )

    ranked: list[tuple[float, str, RetrievalChunk, RetrievalScores]] = []
    for chunk in eligible_chunks:
        lexical = lexical_scores[chunk.chunk_id]
        dense = dense_scores.get(chunk.chunk_id, 0.0) if dense_scores is not None else None
        if dense is None:
            hybrid = lexical
        else:
            total_weight = effective_policy.lexical_weight + effective_policy.dense_weight
            hybrid = (
                effective_policy.lexical_weight * lexical
                + effective_policy.dense_weight * dense
            ) / total_weight
        scores = RetrievalScores(lexical=lexical, dense=dense, hybrid=hybrid)
        ranked.append((scores.hybrid, chunk.chunk_id, chunk, scores))

    ranked.sort(key=lambda item: (-item[0], item[1]))
    eligible = [item for item in ranked if item[0] >= effective_policy.min_score]
    if not eligible:
        return _outcome(
            status=RetrievalStatus.ABSTAINED,
            query_hash=digest,
            policy=effective_policy,
            reason="no_relevant_evidence",
        )
    if (
        effective_policy.abstain_on_ambiguity
        and len(eligible) > 1
        and eligible[0][0] - eligible[1][0] < effective_policy.min_top_margin
    ):
        return _outcome(
            status=RetrievalStatus.ABSTAINED,
            query_hash=digest,
            policy=effective_policy,
            reason="ambiguous_top_candidates",
        )

    evidence = tuple(
        RetrievalEvidence(
            source_id=chunk.source_id,
            source_version=chunk.source_version,
            chunk_id=chunk.chunk_id,
            locator=chunk.locator,
            rank=rank,
            scores=scores,
            query_hash=digest,
            policy_version=effective_policy.version,
            text=chunk.text,
            content_sha256=chunk.content_sha256,
            scope_hash=scope.fingerprint,
        )
        for rank, (_, _, chunk, scores) in enumerate(
            eligible[: effective_policy.max_results],
            start=1,
        )
    )
    return _outcome(
        status=RetrievalStatus.ACCEPTED,
        query_hash=digest,
        policy=effective_policy,
        reason="evidence_retrieved",
        evidence=evidence,
    )


__all__ = [
    "DenseScoreProvider",
    "RETRIEVAL_POLICY_VERSION",
    "RetrievalChunk",
    "RetrievalEvidence",
    "RetrievalLifecycle",
    "RetrievalOutcome",
    "RetrievalPolicy",
    "RetrievalScope",
    "RetrievalScores",
    "RetrievalStatus",
    "normalize_query",
    "query_sha256",
    "retrieve_evidence",
]
