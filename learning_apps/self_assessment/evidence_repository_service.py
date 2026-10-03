"""Database trust boundary for self-assessment evidence admission.

Only authenticated username/goal scope and persisted retrieval decision ids cross
this boundary.  Retrieval text, lifecycle, trust signals, hashes and policy version
are rehydrated from current database state; none are accepted from a request or an
LLM response.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Mapping

from django.conf import settings
from django.db import transaction

from learning_apps.adaptive_learning.concept_identity_service import taxonomy_fingerprint_for_goal
from learning_apps.knowledge.services.retrieval_evidence_service import (
    RETRIEVAL_POLICY_VERSION,
    RetrievalOutcome,
    RetrievalStatus,
)
from learning_apps.knowledge.services.retrieval_repository_service import (
    TrustedRetrievalError,
    load_scoped_retrieval_decision,
    load_trusted_retrieval_bundle,
)
from learning_apps.persistence.models import (
    CSAReferenceBlueprint,
    LearningGoal,
    SelfAssessment,
    SelfAssessmentEvidenceDecision as PersistedEvidenceDecision,
    SelfAssessmentEvidenceRetrieval,
    UserProfile,
)

from .csa_framework import (
    CSA_BLUEPRINT_PROMPT_VERSION,
    CSA_FRAMEWORK_VERSION,
    CSA_PROMPT_VERSION,
)
from .evidence_admission_service import (
    GroundingMode,
    REQUIRED_MATERIAL_TRUST_SIGNALS,
    SELF_ASSESSMENT_EVIDENCE_POLICY_VERSION,
    evaluate_self_assessment_evidence,
    retrieval_bundle_sha256,
    stable_sha256,
)


PROMPT_VERSION = CSA_PROMPT_VERSION
LEGACY_PROMPT_VERSION = "p2.3-self-assessment-report-v2"
class SelfAssessmentAdmissionError(ValueError):
    pass


@dataclass(frozen=True)
class PersistedSelfAssessmentResult:
    assessment: SelfAssessment
    decision: PersistedEvidenceDecision
    perceived_state_id: int | None = None
    perceived_state_persisted: bool = False
    goal_completed: bool = False

    @property
    def accepted(self) -> bool:
        return self.decision.status == PersistedEvidenceDecision.STATUS_ACCEPTED


def _dimension_decisions(evaluation: Mapping[str, Any]) -> dict[str, str]:
    raw = evaluation.get("retrieval_decisions")
    if not isinstance(raw, Mapping):
        return {}
    result: dict[str, str] = {}
    for dimension in ("Facts", "Strategies", "Procedures", "Rationales"):
        item = raw.get(dimension)
        if isinstance(item, Mapping) and str(item.get("decision_id") or "").strip():
            result[dimension] = str(item["decision_id"]).strip()
    return result


def _aggregate_outcome(bundles, decision_ids: Mapping[str, str]) -> RetrievalOutcome:
    evidence = []
    seen = set()
    for bundle in bundles:
        for item in bundle.outcome.evidence:
            identity = (item.source_id, item.source_version, item.chunk_id)
            if identity in seen:
                continue
            seen.add(identity)
            evidence.append(replace(item, rank=len(evidence) + 1))
    aggregate_hash = stable_sha256(
        {"dimensions": dict(sorted(decision_ids.items())), "policy": RETRIEVAL_POLICY_VERSION}
    )
    if evidence:
        return RetrievalOutcome(
            status=RetrievalStatus.ACCEPTED,
            evidence=tuple(evidence),
            query_hash=aggregate_hash,
            policy_version=RETRIEVAL_POLICY_VERSION,
            reason="accepted_aggregate",
        )
    return RetrievalOutcome(
        status=RetrievalStatus.ABSTAINED,
        evidence=(),
        query_hash=aggregate_hash,
        policy_version=RETRIEVAL_POLICY_VERSION,
        reason="no_material_evidence",
    )


def _resolve_reference_blueprint(
    *,
    user: UserProfile,
    goal: LearningGoal,
    evaluation: Mapping[str, Any],
    taxonomy_sha256: str,
    decision_ids: Mapping[str, str],
) -> CSAReferenceBlueprint | None:
    if not settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED:
        return None
    try:
        blueprint_id = int(evaluation.get("reference_blueprint_id"))
    except (TypeError, ValueError) as exc:
        raise SelfAssessmentAdmissionError("reference_blueprint_missing") from exc
    blueprint = (
        CSAReferenceBlueprint.objects.filter(
            id=blueprint_id,
            user=user,
            learning_goal=goal,
            status=CSAReferenceBlueprint.STATUS_FROZEN,
        )
        .prefetch_related("retrieval_links")
        .first()
    )
    if not blueprint:
        raise SelfAssessmentAdmissionError("reference_blueprint_scope_invalid")
    expected_hash = str(evaluation.get("reference_blueprint_sha256") or "")
    diagnostic_metadata = evaluation.get("diagnostic_metadata")
    diagnostic_metadata = (
        diagnostic_metadata if isinstance(diagnostic_metadata, Mapping) else {}
    )
    if (
        blueprint.framework_version != CSA_FRAMEWORK_VERSION
        or blueprint.prompt_version != CSA_BLUEPRINT_PROMPT_VERSION
        or blueprint.taxonomy_sha256 != taxonomy_sha256
        or not expected_hash
        or expected_hash != blueprint.blueprint_sha256
        or diagnostic_metadata.get("reference_blueprint_id") != blueprint.id
        or diagnostic_metadata.get("reference_blueprint_sha256")
        != blueprint.blueprint_sha256
        or stable_sha256(blueprint.blueprint) != blueprint.blueprint_sha256
        or not isinstance(blueprint.target_problem_snapshot, Mapping)
        or diagnostic_metadata.get("target_task_sha256")
        != blueprint.target_problem_snapshot.get("sha256")
        or diagnostic_metadata.get("target_task")
        != blueprint.target_problem_snapshot.get("raw")
        or not isinstance(blueprint.scope_contract, Mapping)
        or blueprint.scope_contract.get("status") != "ready"
        or diagnostic_metadata.get("scope_contract") != blueprint.scope_contract
    ):
        raise SelfAssessmentAdmissionError("reference_blueprint_integrity_invalid")
    blueprint_decisions = {
        link.dimension: str(link.retrieval_decision_id)
        for link in blueprint.retrieval_links.all()
    }
    if blueprint_decisions != dict(decision_ids):
        raise SelfAssessmentAdmissionError(
            "reference_blueprint_retrieval_mismatch"
        )
    return blueprint


def persist_evaluation_with_evidence(
    *,
    username: str,
    learning_goal_id: int,
    assessment_payload: Mapping[str, Any],
    evaluation: Mapping[str, Any],
) -> PersistedSelfAssessmentResult:
    """Validate and atomically persist the assessment plus immutable decision."""

    user = UserProfile.objects.filter(username=str(username)).first()
    goal = (
        LearningGoal.objects.filter(id=int(learning_goal_id), user=user).first()
        if user
        else None
    )
    if not user or not goal:
        raise SelfAssessmentAdmissionError("scope_not_found")
    from .services.self_assessment_evaluation_service import (
        create_self_assessment_text,
    )

    expected_student_text = create_self_assessment_text(dict(assessment_payload))
    if str(evaluation.get("student_text") or "") != expected_student_text:
        raise SelfAssessmentAdmissionError("student_text_payload_mismatch")

    decision_ids = _dimension_decisions(evaluation)
    retrieval_error = (
        ""
        if len(decision_ids) in {0, 4}
        else "retrieval_decision_missing"
    )
    scoped_decisions = []
    trusted_bundles = []
    allowed_evidence_ids_by_dimension: dict[str, list[str]] = {
        dimension: [] for dimension in ("Facts", "Strategies", "Procedures", "Rationales")
    }
    taxonomy_hash = taxonomy_fingerprint_for_goal(user, goal)
    reference_blueprint = _resolve_reference_blueprint(
        user=user,
        goal=goal,
        evaluation=evaluation,
        taxonomy_sha256=taxonomy_hash,
        decision_ids=decision_ids,
    )
    frozen_taxonomy_hash = str(
        evaluation.get("taxonomy_sha256_before_generation") or ""
    )
    if not frozen_taxonomy_hash or frozen_taxonomy_hash != taxonomy_hash:
        retrieval_error = "taxonomy_changed_during_generation"
    lifecycle_rows = []
    if not retrieval_error:
        for dimension, decision_id in decision_ids.items():
            purpose = f"self_assessment_{dimension.casefold()}"
            try:
                decision = load_scoped_retrieval_decision(
                    username,
                    goal.id,
                    decision_id,
                    expected_purpose=purpose,
                )
                scoped_decisions.append((dimension, decision))
                if decision.status == decision.STATUS_FAILED:
                    retrieval_error = decision.failure_code or "retrieval_failed"
                    continue
                if decision.status == decision.STATUS_ACCEPTED:
                    bundle = load_trusted_retrieval_bundle(
                        username,
                        goal.id,
                        decision_id,
                        expected_purpose=purpose,
                    )
                    trusted_bundles.append(bundle)
                    lifecycle_rows.append(bundle.lifecycle_bundle_sha256)
                    allowed_evidence_ids_by_dimension[dimension] = [
                        row.evidence_ref for row in bundle.evidence_rows
                    ]
            except TrustedRetrievalError as exc:
                retrieval_error = exc.code
                break

    aggregate = _aggregate_outcome(trusted_bundles, decision_ids)
    lifecycle = {
        row.evidence_ref: "active"
        for bundle in trusted_bundles
        for row in bundle.evidence_rows
    }
    mode = (
        GroundingMode.RETRIEVED_MATERIAL
        if trusted_bundles
        else GroundingMode.GENERAL_DOMAIN
    )
    admission = evaluate_self_assessment_evidence(
        assessment_payload=assessment_payload,
        structured_report=evaluation.get("structured_report") or {},
        grounding_mode=mode,
        retrieval_outcome=aggregate if trusted_bundles else None,
        expected_scope=trusted_bundles[0].scope if trusted_bundles else None,
        lifecycle_by_evidence_id=lifecycle if trusted_bundles else None,
        allowed_evidence_ids_by_dimension=allowed_evidence_ids_by_dimension,
        is_partial=bool(evaluation.get("is_partial")),
        dimension_errors=evaluation.get("dimension_errors") or {},
        provider_error=retrieval_error,
        evaluation_error=str(evaluation.get("evaluation_error") or ""),
        trust_signals=(
            tuple(sorted(REQUIRED_MATERIAL_TRUST_SIGNALS)) if trusted_bundles else ()
        ),
        require_csa_contract=bool(settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED),
    )
    prompt_version = (
        PROMPT_VERSION
        if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED
        else LEGACY_PROMPT_VERSION
    )
    reported_model = str(evaluation.get("model") or "")
    configured_model = str(
        settings.LEARNING_SELF_ASSESSMENT_MODEL
        if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED
        else settings.OPENAI_MODEL
    )
    model = (
        reported_model
        if reported_model == configured_model
        or reported_model.startswith(f"{configured_model}-")
        else configured_model
    )[:96]
    model_configuration = (
        dict(evaluation.get("model_configuration") or {})
        if isinstance(evaluation.get("model_configuration"), Mapping)
        else {}
    )
    if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED:
        diagnostic_metadata = evaluation.get("diagnostic_metadata")
        diagnostic_metadata = (
            diagnostic_metadata if isinstance(diagnostic_metadata, Mapping) else {}
        )
        framework_version = str(
            diagnostic_metadata.get("csa_framework_version") or ""
        )
        if framework_version != CSA_FRAMEWORK_VERSION:
            raise SelfAssessmentAdmissionError("csa_framework_version_invalid")
    idempotency_key = stable_sha256(
        {
            "user_id": user.user_id,
            "goal_id": goal.id,
            "assessment": admission.assessment_hash,
            "report": admission.structured_report_hash,
            "retrieval_decisions": dict(sorted(decision_ids.items())),
            "policy": SELF_ASSESSMENT_EVIDENCE_POLICY_VERSION,
            "prompt": prompt_version,
            "model": model,
            "model_configuration": model_configuration,
            "reference_blueprint_id": (
                reference_blueprint.id if reference_blueprint else None
            ),
            "reference_blueprint_sha256": (
                reference_blueprint.blueprint_sha256
                if reference_blueprint
                else ""
            ),
        }
    )
    dimension_status = {
        item.dimension: {
            "valid": item.valid,
            "aspect_count": item.aspect_count,
            "reason": item.reason,
        }
        for item in admission.dimension_validation
    }
    quotes = {
        dimension: [
            {
                "student_quote": str(aspect.get("student_quote") or ""),
                "evidence_quotes": aspect.get("evidence_quotes") or {},
            }
            for aspect in (content.get("aspects") or [])
            if isinstance(aspect, Mapping)
        ]
        for dimension, content in (evaluation.get("structured_report") or {}).items()
        if isinstance(content, Mapping)
    }

    with transaction.atomic():
        existing = PersistedEvidenceDecision.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).select_related("self_assessment").first()
        if existing:
            return PersistedSelfAssessmentResult(existing.self_assessment, existing)

        if reference_blueprint:
            locked_blueprint = (
                CSAReferenceBlueprint.objects.select_for_update()
                .prefetch_related("retrieval_links")
                .filter(
                    id=reference_blueprint.id,
                    user=user,
                    learning_goal=goal,
                    status=CSAReferenceBlueprint.STATUS_FROZEN,
                )
                .first()
            )
            if (
                not locked_blueprint
                or locked_blueprint.taxonomy_sha256 != frozen_taxonomy_hash
                or locked_blueprint.blueprint_sha256
                != reference_blueprint.blueprint_sha256
                or stable_sha256(locked_blueprint.blueprint)
                != locked_blueprint.blueprint_sha256
                or {
                    link.dimension: str(link.retrieval_decision_id)
                    for link in locked_blueprint.retrieval_links.all()
                }
                != dict(decision_ids)
            ):
                raise SelfAssessmentAdmissionError(
                    "reference_blueprint_changed_before_commit"
                )

        # Lock and recheck every retrieval decision immediately before commit.
        locked_bundles = []
        locked_lifecycle_rows = []
        for dimension, decision_id in decision_ids.items():
            locked_decision = load_scoped_retrieval_decision(
                username,
                goal.id,
                decision_id,
                expected_purpose=f"self_assessment_{dimension.casefold()}",
                lock=True,
            )
            if locked_decision.status == locked_decision.STATUS_ACCEPTED:
                locked_bundle = load_trusted_retrieval_bundle(
                    username,
                    goal.id,
                    decision_id,
                    expected_purpose=f"self_assessment_{dimension.casefold()}",
                    lock=True,
                )
                locked_bundles.append(locked_bundle)
                locked_lifecycle_rows.append(locked_bundle.lifecycle_bundle_sha256)
        locked_aggregate = _aggregate_outcome(locked_bundles, decision_ids)
        if (
            retrieval_bundle_sha256(locked_aggregate if locked_bundles else None)
            != admission.retrieval_bundle_hash
            or stable_sha256(sorted(locked_lifecycle_rows))
            != stable_sha256(sorted(lifecycle_rows))
            or taxonomy_fingerprint_for_goal(user, goal) != frozen_taxonomy_hash
        ):
            raise SelfAssessmentAdmissionError("evidence_changed_before_commit")
        assessment = SelfAssessment.objects.create(
            username=username,
            learning_goal=goal,
            reference_blueprint=reference_blueprint,
            target_problem=str(
                (
                    evaluation.get("diagnostic_metadata")
                    if isinstance(evaluation.get("diagnostic_metadata"), Mapping)
                    else {}
                ).get("target_task")
                or ""
            ),
            scope_contract=(
                dict(reference_blueprint.scope_contract or {})
                if reference_blueprint
                else {}
            ),
            domain=str(evaluation.get("domain") or ""),
            branch=str(evaluation.get("branch") or ""),
            student_text=str(evaluation.get("student_text") or ""),
            evaluation_report=str(evaluation.get("report") or ""),
            structured_report=evaluation.get("structured_report") or {},
            operational_strategy={
                "domain": str(evaluation.get("domain") or ""),
                "branch": str(evaluation.get("branch") or ""),
                "is_partial": bool(evaluation.get("is_partial")),
                "dimension_errors": evaluation.get("dimension_errors") or {},
                "evidence_admission": admission.status.value,
            },
            submission_snapshot=(
                dict(evaluation.get("submission_snapshot") or {})
                if isinstance(evaluation.get("submission_snapshot"), Mapping)
                else {}
            ),
            diagnostic_metadata=(
                dict(evaluation.get("diagnostic_metadata") or {})
                if isinstance(evaluation.get("diagnostic_metadata"), Mapping)
                else {}
            ),
        )
        persisted = PersistedEvidenceDecision.objects.create(
            user=user,
            learning_goal=goal,
            self_assessment=assessment,
            status=admission.status.value,
            reason_code=admission.reason_code[:96],
            policy_version=admission.policy_version,
            assessment_sha256=admission.assessment_hash,
            structured_report_sha256=admission.structured_report_hash,
            retrieval_bundle_sha256=admission.retrieval_bundle_hash,
            scope_sha256=(trusted_bundles[0].scope.fingerprint if trusted_bundles else stable_sha256({"user": user.user_id, "goal": goal.id})),
            taxonomy_sha256=taxonomy_hash,
            lifecycle_bundle_sha256=stable_sha256(sorted(lifecycle_rows)),
            decision_sha256=admission.decision_hash,
            prompt_version=prompt_version,
            model=model,
            model_configuration=model_configuration,
            dimension_status=dimension_status,
            quote_validation_sha256=stable_sha256(quotes),
            is_partial=bool(evaluation.get("is_partial")),
            idempotency_key=idempotency_key,
            mastery_write_authorized=False,
        )
        SelfAssessmentEvidenceRetrieval.objects.bulk_create(
            [
                SelfAssessmentEvidenceRetrieval(
                    evidence_decision=persisted,
                    retrieval_decision=decision,
                    dimension=dimension,
                )
                for dimension, decision in scoped_decisions
            ]
        )
    return PersistedSelfAssessmentResult(assessment, persisted)


def accepted_decision_is_current(
    assessment: SelfAssessment, *, lock: bool = False
) -> bool:
    """Revalidate an accepted persisted decision before downstream baseline use."""

    decisions = PersistedEvidenceDecision.objects.select_related(
        "user", "learning_goal"
    )
    if lock:
        decisions = decisions.select_for_update()
    decision = decisions.filter(self_assessment=assessment).first()
    if not decision:
        return False
    if (
        decision.status != PersistedEvidenceDecision.STATUS_ACCEPTED
        or decision.invalidated_at is not None
        or decision.mastery_write_authorized
        or decision.policy_version != SELF_ASSESSMENT_EVIDENCE_POLICY_VERSION
        or decision.user.username != assessment.username
        or decision.learning_goal_id != assessment.learning_goal_id
        or decision.taxonomy_sha256
        != taxonomy_fingerprint_for_goal(decision.user, decision.learning_goal)
    ):
        return False
    if assessment.reference_blueprint_id:
        blueprint = assessment.reference_blueprint
        if (
            blueprint.user_id != decision.user_id
            or blueprint.learning_goal_id != decision.learning_goal_id
            or blueprint.status != CSAReferenceBlueprint.STATUS_FROZEN
            or blueprint.framework_version != CSA_FRAMEWORK_VERSION
            or blueprint.prompt_version != CSA_BLUEPRINT_PROMPT_VERSION
            or blueprint.taxonomy_sha256 != decision.taxonomy_sha256
            or stable_sha256(blueprint.blueprint) != blueprint.blueprint_sha256
            or not isinstance(blueprint.scope_contract, Mapping)
            or blueprint.scope_contract.get("status") != "ready"
            or not isinstance(blueprint.target_problem_snapshot, Mapping)
        ):
            return False
        blueprint_links = {
            link.dimension: str(link.retrieval_decision_id)
            for link in blueprint.retrieval_links.all()
        }
        decision_links = {
            link.dimension: str(link.retrieval_decision_id)
            for link in decision.retrieval_links.all()
        }
        if blueprint_links != decision_links:
            return False
    links = decision.retrieval_links.select_related("retrieval_decision")
    if lock:
        links = links.select_for_update()
    for link in links.all():
        retrieval = link.retrieval_decision
        try:
            load_scoped_retrieval_decision(
                decision.user.username,
                decision.learning_goal_id,
                retrieval.decision_id,
                expected_purpose=f"self_assessment_{link.dimension.casefold()}",
                lock=lock,
            )
            if retrieval.status == retrieval.STATUS_ACCEPTED:
                load_trusted_retrieval_bundle(
                    decision.user.username,
                    decision.learning_goal_id,
                    retrieval.decision_id,
                    expected_purpose=f"self_assessment_{link.dimension.casefold()}",
                    lock=lock,
                )
        except TrustedRetrievalError:
            return False
    # New stage-one blueprints have zero links because course materials are not
    # part of that model call. Four-link historical decisions remain verifiable
    # and readable through the same lifecycle checks.
    return decision.retrieval_links.count() in {0, 4}


__all__ = [
    "PersistedSelfAssessmentResult",
    "SelfAssessmentAdmissionError",
    "accepted_decision_is_current",
    "persist_evaluation_with_evidence",
]
