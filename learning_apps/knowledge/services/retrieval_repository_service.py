"""Owner/goal-scoped persistence adapter for provider-independent retrieval."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Optional

from django.conf import settings
from django.db import IntegrityError, transaction
from django.db.models import F

from learning_apps.adaptive_learning.concept_identity_service import taxonomy_fingerprint_for_goal
from learning_apps.persistence.models import (
    KnowledgeChunk,
    LearningGoal,
    RetrievalDecision,
    RetrievalDecisionEvidence,
    UploadedLearningMaterial,
    UserProfile,
)

from .retrieval_evidence_service import (
    DenseScoreProvider,
    RetrievalChunk,
    RetrievalLifecycle,
    RetrievalOutcome,
    RetrievalPolicy,
    RetrievalScope,
    RetrievalStatus,
    RetrievalEvidence,
    RetrievalScores,
    RETRIEVAL_POLICY_VERSION,
    query_sha256,
    retrieve_evidence,
)

DEFAULT_MAX_RETRIEVAL_CANDIDATES = 5000


class RetrievalScopeError(ValueError):
    """Raised when the caller does not own the requested learning goal."""


class TrustedRetrievalError(ValueError):
    """Stable fail-closed error raised when a persisted bundle is no longer valid."""

    def __init__(self, code: str):
        self.code = str(code or "trusted_retrieval_invalid")
        super().__init__(self.code)


@dataclass(frozen=True)
class PersistedRetrievalResult:
    decision_id: str
    outcome: RetrievalOutcome

    @property
    def evidence(self):
        return self.outcome.evidence


@dataclass(frozen=True)
class TrustedRetrievalBundle:
    decision_id: str
    outcome: RetrievalOutcome
    evidence_rows: tuple[RetrievalDecisionEvidence, ...]
    scope: RetrievalScope
    taxonomy_sha256: str
    lifecycle_bundle_sha256: str
    selected_bundle_sha256: str


def _hash_identifier(value: str) -> str:
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def _set_fingerprint(hashes: list[str]) -> str:
    return hashlib.sha256("\n".join(sorted(hashes)).encode("ascii")).hexdigest()


def _stable_sha256(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def _evidence_ref(*, source_id: str, source_version: str, chunk_id: str) -> str:
    return f"ev_{_stable_sha256({'source_id': source_id, 'source_version': source_version, 'chunk_id': chunk_id})[:32]}"


def _chunk_snapshot_payload(chunk: KnowledgeChunk, scope_sha256: str) -> dict:
    material = chunk.material
    return {
        "scope_sha256": scope_sha256,
        "chunk_id": chunk.chunk_id,
        "content_sha256": chunk.content_sha256,
        "source_key": chunk.source_key,
        "source_version": int(chunk.source_version),
        "source_content_sha256": chunk.source_content_sha256,
        "transform_version": chunk.transform_version,
        "lifecycle": chunk.lifecycle,
        "trust_level": chunk.trust_level,
        "material_id": int(material.id),
        "material_chunk_status": material.chunk_status,
        "material_content_sha256": material.content_sha256 or "",
        "material_active_content_sha256": material.active_content_sha256 or "",
        "material_deleted": bool(material.deleted_at),
    }


def _chunk_snapshot_sha256(chunk: KnowledgeChunk, scope_sha256: str) -> str:
    return _stable_sha256(_chunk_snapshot_payload(chunk, scope_sha256))


def _bundle_hashes(rows: list[dict]) -> tuple[str, str]:
    lifecycle = _stable_sha256(
        [
            {
                "evidence_ref": row["evidence_ref"],
                "snapshot_sha256": row["snapshot_sha256"],
            }
            for row in sorted(rows, key=lambda item: int(item["rank"]))
        ]
    )
    selected = _stable_sha256(
        [
            {
                "evidence_ref": row["evidence_ref"],
                "rank": int(row["rank"]),
                "scores": row["scores"],
                "snapshot_sha256": row["snapshot_sha256"],
            }
            for row in sorted(rows, key=lambda item: int(item["rank"]))
        ]
    )
    return lifecycle, selected


def _chunk_is_current(chunk: KnowledgeChunk, user: UserProfile, goal: LearningGoal) -> bool:
    material = chunk.material
    return bool(
        chunk.user_id == user.user_id
        and chunk.learning_goal_id == goal.id
        and material.user_id == user.user_id
        and material.learning_goal_id == goal.id
        and chunk.lifecycle == KnowledgeChunk.LIFECYCLE_ACTIVE
        and chunk.trust_level == "untrusted_retrieved_content"
        and material.status
        not in {
            UploadedLearningMaterial.STATUS_DELETING,
            UploadedLearningMaterial.STATUS_DELETED,
        }
        and material.chunk_status == UploadedLearningMaterial.CHUNK_STATUS_READY
        and material.deleted_at is None
        and material.active_content_sha256
        and material.content_sha256
        and material.active_content_sha256 == material.content_sha256
        and chunk.source_content_sha256 == material.content_sha256
    )


def _failed_candidate_limit(query: str, policy: RetrievalPolicy) -> RetrievalOutcome:
    return RetrievalOutcome(
        status=RetrievalStatus.FAILED,
        evidence=(),
        query_hash=query_sha256(query),
        policy_version=policy.version,
        reason="candidate_limit_exceeded",
        failure_code="candidate_limit_exceeded",
    )


def _result_from_persisted_decision(
    decision: RetrievalDecision,
) -> PersistedRetrievalResult:
    """Rehydrate an idempotent replay from the committed database truth.

    A replay must never pair a previously committed decision id with the
    outcome calculated by the losing/replaying request.  Accepted decisions
    are reloaded through the full trust/lifecycle verifier so stale evidence is
    not reintroduced into a generation prompt.  Non-accepted decisions have no
    evidence rows and can be reconstructed directly from their immutable trace.
    """

    try:
        status = RetrievalStatus(decision.status)
    except ValueError as exc:
        raise TrustedRetrievalError("retrieval_status_invalid") from exc
    if status is RetrievalStatus.ACCEPTED:
        bundle = load_trusted_retrieval_bundle(
            decision.user.username,
            decision.learning_goal_id,
            decision.decision_id,
            expected_purpose=decision.purpose,
        )
        return PersistedRetrievalResult(
            decision_id=decision.decision_id,
            outcome=bundle.outcome,
        )
    return PersistedRetrievalResult(
        decision_id=decision.decision_id,
        outcome=RetrievalOutcome(
            status=status,
            evidence=(),
            query_hash=decision.query_sha256,
            policy_version=decision.policy_version,
            reason=decision.reason_code,
            failure_code=(
                decision.failure_code if status is RetrievalStatus.FAILED else ""
            ),
        ),
    )


def retrieve_material_evidence(
    username: str,
    learning_goal_id: int,
    query: str,
    *,
    dense_provider: Optional[DenseScoreProvider] = None,
    policy: Optional[RetrievalPolicy] = None,
    purpose: str = "general",
) -> PersistedRetrievalResult:
    """Retrieve active evidence and persist only a hash-only decision trace.

    Retrieved material remains untrusted input. This adapter deliberately has no
    mastery-state dependency and cannot authorize or perform a mastery write.
    """
    user = UserProfile.objects.filter(username=username).first()
    goal = (
        LearningGoal.objects.filter(id=int(learning_goal_id), user=user).first()
        if user
        else None
    )
    if not user or not goal:
        raise RetrievalScopeError("Learning goal not found in the caller's scope.")

    scope = RetrievalScope(owner_id=str(user.user_id), learning_goal_id=str(goal.id))
    effective_policy = policy or RetrievalPolicy()
    clean_purpose = str(purpose or "general").strip()[:64] or "general"
    taxonomy_sha256 = taxonomy_fingerprint_for_goal(user, goal)
    max_candidates = max(
        1,
        int(
            getattr(
                settings,
                "LEARNING_MATERIAL_MAX_RETRIEVAL_CANDIDATES",
                DEFAULT_MAX_RETRIEVAL_CANDIDATES,
            )
        ),
    )
    rows = list(
        KnowledgeChunk.objects.filter(
            user=user,
            learning_goal=goal,
            lifecycle=KnowledgeChunk.LIFECYCLE_ACTIVE,
            trust_level="untrusted_retrieved_content",
            material__user=user,
            material__learning_goal=goal,
            material__chunk_status=UploadedLearningMaterial.CHUNK_STATUS_READY,
            material__active_content_sha256__isnull=False,
            material__deleted_at__isnull=True,
            source_content_sha256=F("material__content_sha256"),
        )
        .select_related("material")
        .order_by("chunk_id")[: max_candidates + 1]
    )
    candidate_limit_exceeded = len(rows) > max_candidates
    if candidate_limit_exceeded:
        rows = rows[:max_candidates]

    candidates = tuple(
        RetrievalChunk(
            source_id=row.source_key,
            source_version=str(row.source_version),
            chunk_id=row.chunk_id,
            locator=row.locator,
            text=row.content,
            owner_id=str(row.user_id),
            learning_goal_id=str(row.learning_goal_id),
            lifecycle=RetrievalLifecycle.ACTIVE,
            content_sha256=row.content_sha256,
        )
        for row in rows
    )
    outcome = (
        _failed_candidate_limit(query, effective_policy)
        if candidate_limit_exceeded
        else retrieve_evidence(
            query,
            candidates,
            scope=scope,
            dense_provider=dense_provider,
            policy=effective_policy,
        )
    )

    candidate_hashes = [_hash_identifier(chunk.chunk_id) for chunk in candidates]
    selected_hashes = [_hash_identifier(item.chunk_id) for item in outcome.evidence]
    row_by_chunk_id = {row.chunk_id: row for row in rows}
    evidence_snapshots = []
    for item in outcome.evidence:
        row = row_by_chunk_id.get(item.chunk_id)
        if row is None:
            continue
        evidence_snapshots.append(
            {
                "chunk": row,
                "evidence_ref": _evidence_ref(
                    source_id=item.source_id,
                    source_version=item.source_version,
                    chunk_id=item.chunk_id,
                ),
                "rank": item.rank,
                "scores": {
                    "lexical": item.scores.lexical,
                    "dense": item.scores.dense,
                    "hybrid": item.scores.hybrid,
                },
                "snapshot_sha256": _chunk_snapshot_sha256(row, scope.fingerprint),
            }
        )
    lifecycle_hash, selected_bundle_hash = _bundle_hashes(evidence_snapshots)
    candidate_set_hash = _set_fingerprint(candidate_hashes)
    request_hash = _stable_sha256(
        {
            "purpose": clean_purpose,
            "query_sha256": outcome.query_hash,
            "scope_sha256": scope.fingerprint,
            "taxonomy_sha256": taxonomy_sha256,
            "candidate_set_sha256": candidate_set_hash,
            "policy": {
                "version": effective_policy.version,
                "max_results": effective_policy.max_results,
                "min_score": effective_policy.min_score,
                "lexical_weight": effective_policy.lexical_weight,
                "dense_weight": effective_policy.dense_weight,
                "abstain_on_ambiguity": effective_policy.abstain_on_ambiguity,
                "min_top_margin": effective_policy.min_top_margin,
            },
        }
    )
    idempotency_key = _stable_sha256(
        {"scope": scope.fingerprint, "purpose": clean_purpose, "request": request_hash}
    )

    with transaction.atomic():
        locked_rows = {
            row.chunk_id: row
            for row in KnowledgeChunk.objects.select_for_update()
            .select_related("material")
            .filter(chunk_id__in=[item["chunk"].chunk_id for item in evidence_snapshots])
        }
        stale = any(
            item["chunk"].chunk_id not in locked_rows
            or not _chunk_is_current(locked_rows[item["chunk"].chunk_id], user, goal)
            or _chunk_snapshot_sha256(
                locked_rows[item["chunk"].chunk_id], scope.fingerprint
            )
            != item["snapshot_sha256"]
            for item in evidence_snapshots
        )
        if stale:
            outcome = RetrievalOutcome(
                status=RetrievalStatus.FAILED,
                evidence=(),
                query_hash=outcome.query_hash,
                policy_version=effective_policy.version,
                reason="retrieval_snapshot_changed",
                failure_code="retrieval_snapshot_changed",
            )
            evidence_snapshots = []
            selected_hashes = []
            lifecycle_hash, selected_bundle_hash = _bundle_hashes([])

        existing = RetrievalDecision.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            return _result_from_persisted_decision(existing)
        try:
            with transaction.atomic():
                decision = RetrievalDecision.objects.create(
                user=user,
                learning_goal=goal,
                query_sha256=outcome.query_hash,
                scope_sha256=scope.fingerprint,
                purpose=clean_purpose,
                request_sha256=request_hash,
                taxonomy_sha256=taxonomy_sha256,
                lifecycle_bundle_sha256=lifecycle_hash,
                selected_bundle_sha256=selected_bundle_hash,
                idempotency_key=idempotency_key,
                policy_version=outcome.policy_version,
                status=outcome.status.value,
                reason_code=outcome.reason[:96],
                failure_code=outcome.failure_code[:96],
                candidate_count=len(candidates),
                candidate_set_sha256=candidate_set_hash,
                selected_chunk_hashes=selected_hashes,
                evidence_count=len(outcome.evidence),
                    mastery_write_authorized=False,
                )
        except IntegrityError:
            decision = RetrievalDecision.objects.get(idempotency_key=idempotency_key)
            return _result_from_persisted_decision(decision)
        RetrievalDecisionEvidence.objects.bulk_create(
            [
                RetrievalDecisionEvidence(
                    retrieval_decision=decision,
                    knowledge_chunk=locked_rows[item["chunk"].chunk_id],
                    evidence_ref=item["evidence_ref"],
                    rank=item["rank"],
                    scores=item["scores"],
                    snapshot_sha256=item["snapshot_sha256"],
                    mastery_write_authorized=False,
                )
                for item in evidence_snapshots
            ]
        )
    return PersistedRetrievalResult(decision_id=decision.decision_id, outcome=outcome)


def load_scoped_retrieval_decision(
    username: str,
    learning_goal_id: int,
    decision_id: str,
    *,
    expected_purpose: str = "",
    lock: bool = False,
) -> RetrievalDecision:
    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(id=int(learning_goal_id), user=user).first() if user else None
    if not user or not goal:
        raise TrustedRetrievalError("scope_not_found")
    queryset = RetrievalDecision.objects
    if lock:
        queryset = queryset.select_for_update()
    decision = queryset.filter(
        decision_id=str(decision_id), user=user, learning_goal=goal
    ).first()
    if not decision:
        raise TrustedRetrievalError("decision_not_found_in_scope")
    scope = RetrievalScope(owner_id=str(user.user_id), learning_goal_id=str(goal.id))
    if decision.scope_sha256 != scope.fingerprint:
        raise TrustedRetrievalError("scope_hash_mismatch")
    if expected_purpose and decision.purpose != str(expected_purpose):
        raise TrustedRetrievalError("purpose_mismatch")
    if decision.policy_version != RETRIEVAL_POLICY_VERSION:
        raise TrustedRetrievalError("retrieval_policy_stale")
    current_taxonomy = taxonomy_fingerprint_for_goal(user, goal)
    if not decision.taxonomy_sha256 or decision.taxonomy_sha256 != current_taxonomy:
        raise TrustedRetrievalError("taxonomy_hash_mismatch")
    if decision.mastery_write_authorized:
        raise TrustedRetrievalError("retrieval_claims_mastery_authority")
    return decision


def load_trusted_retrieval_bundle(
    username: str,
    learning_goal_id: int,
    decision_id: str,
    *,
    expected_purpose: str = "",
    lock: bool = False,
) -> TrustedRetrievalBundle:
    decision = load_scoped_retrieval_decision(
        username,
        learning_goal_id,
        decision_id,
        expected_purpose=expected_purpose,
        lock=lock,
    )
    if decision.status != RetrievalDecision.STATUS_ACCEPTED:
        raise TrustedRetrievalError("retrieval_not_accepted")
    rows_qs = RetrievalDecisionEvidence.objects.filter(
        retrieval_decision=decision
    ).select_related("knowledge_chunk__material")
    if lock:
        rows_qs = rows_qs.select_for_update()
    evidence_rows = tuple(rows_qs.order_by("rank", "id"))
    if not evidence_rows or len(evidence_rows) != int(decision.evidence_count):
        raise TrustedRetrievalError("retrieval_evidence_incomplete")

    user = decision.user
    goal = decision.learning_goal
    scope = RetrievalScope(owner_id=str(user.user_id), learning_goal_id=str(goal.id))
    snapshot_rows = []
    evidence = []
    for row in evidence_rows:
        chunk = row.knowledge_chunk
        if not chunk or not _chunk_is_current(chunk, user, goal):
            raise TrustedRetrievalError("retrieval_chunk_inactive")
        current_snapshot = _chunk_snapshot_sha256(chunk, scope.fingerprint)
        if current_snapshot != row.snapshot_sha256:
            raise TrustedRetrievalError("retrieval_snapshot_mismatch")
        expected_ref = _evidence_ref(
            source_id=chunk.source_key,
            source_version=str(chunk.source_version),
            chunk_id=chunk.chunk_id,
        )
        if expected_ref != row.evidence_ref:
            raise TrustedRetrievalError("retrieval_evidence_ref_mismatch")
        scores = row.scores if isinstance(row.scores, dict) else {}
        typed_scores = RetrievalScores(
            lexical=scores.get("lexical", 0.0),
            dense=scores.get("dense"),
            hybrid=scores.get("hybrid", 0.0),
        )
        evidence.append(
            RetrievalEvidence(
                source_id=chunk.source_key,
                source_version=str(chunk.source_version),
                chunk_id=chunk.chunk_id,
                locator=chunk.locator,
                rank=row.rank,
                scores=typed_scores,
                query_hash=decision.query_sha256,
                policy_version=decision.policy_version,
                text=chunk.content,
                content_sha256=chunk.content_sha256,
                scope_hash=scope.fingerprint,
                mastery_write_authorized=False,
                trust_level="untrusted_retrieved_content",
            )
        )
        snapshot_rows.append(
            {
                "evidence_ref": row.evidence_ref,
                "rank": row.rank,
                "scores": row.scores,
                "snapshot_sha256": row.snapshot_sha256,
            }
        )
    lifecycle_hash, selected_hash = _bundle_hashes(snapshot_rows)
    if lifecycle_hash != decision.lifecycle_bundle_sha256:
        raise TrustedRetrievalError("lifecycle_bundle_hash_mismatch")
    if selected_hash != decision.selected_bundle_sha256:
        raise TrustedRetrievalError("selected_bundle_hash_mismatch")
    outcome = RetrievalOutcome(
        status=RetrievalStatus.ACCEPTED,
        evidence=tuple(evidence),
        query_hash=decision.query_sha256,
        policy_version=decision.policy_version,
        reason=decision.reason_code,
    )
    return TrustedRetrievalBundle(
        decision_id=decision.decision_id,
        outcome=outcome,
        evidence_rows=evidence_rows,
        scope=scope,
        taxonomy_sha256=decision.taxonomy_sha256,
        lifecycle_bundle_sha256=lifecycle_hash,
        selected_bundle_sha256=selected_hash,
    )


__all__ = [
    "PersistedRetrievalResult",
    "RetrievalScopeError",
    "TrustedRetrievalBundle",
    "TrustedRetrievalError",
    "load_scoped_retrieval_decision",
    "load_trusted_retrieval_bundle",
    "retrieve_material_evidence",
]
