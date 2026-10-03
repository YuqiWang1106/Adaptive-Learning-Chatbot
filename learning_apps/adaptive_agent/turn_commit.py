from __future__ import annotations

import hashlib
import re

from django.db import transaction

from learning_apps.chat.services.conversation_repository_service import require_writable_conversation
from learning_apps.infrastructure.services.html_sanitizer_service import markdown_to_safe_chat_html
from learning_apps.knowledge.services.retrieval_repository_service import (
    TrustedRetrievalError,
    load_trusted_retrieval_bundle,
)
from learning_apps.persistence.models import ChatConceptSignal, ConceptRegistryEntry, UserHistory

from learning_apps.application.contracts import CapabilityError
from .conversation_memory import record_completed_turn
from .models import LearningAgentRun
from .output import TutorTurnOutput


_FORBIDDEN_REASONING = re.compile(
    r"(?:hidden\s+chain[- ]of[- ]thought|private\s+reasoning|system\s+prompt|developer\s+message)",
    re.IGNORECASE,
)
_UNSUPPORTED_DEFINITIVE_PERSONALIZATION = re.compile(
    r"(?:evidence\s+confirms|this\s+confirms|the\s+data\s+proves|your\s+self[- ]assessment\s+proves)"
    r".{0,100}(?:weakness|misconception|mastery|progress)"
    r"|(?:自评|数据).{0,40}(?:证明|确认).{0,40}(?:薄弱|误区|掌握|进步)",
    re.IGNORECASE | re.DOTALL,
)
_UNSUPPORTED_LONG_TERM_PATTERN = re.compile(
    r"(?:you\s+always|consistently\s+struggle|stable\s+weakness|long[- ]term\s+(?:weakness|pattern))"
    r"|(?:长期薄弱|总是不会|稳定的弱点|长期模式)",
    re.IGNORECASE,
)


def _tokens(value: str) -> set[str]:
    return {token for token in re.findall(r"[\w]+", value.casefold()) if len(token) > 2}


def _validate_citations(run: LearningAgentRun, output: TutorTurnOutput) -> list[dict]:
    evidence_by_ref = {}
    decision_ids = {
        str(item.get("decision_id") or "")
        for item in run.evidence_manifest
        if isinstance(item, dict) and item.get("decision_id")
    }
    for decision_id in decision_ids:
        try:
            bundle = load_trusted_retrieval_bundle(
                run.user.username,
                run.learning_goal_id,
                decision_id,
                expected_purpose="agent_answer_v2",
            )
        except TrustedRetrievalError as exc:
            raise CapabilityError("citation_evidence_stale") from exc
        for item, row in zip(bundle.outcome.evidence, bundle.evidence_rows):
            evidence_by_ref[row.evidence_ref] = (item, row)

    selected_skill_ids = {
        str(item.get("skill_id") or "")
        for item in (run.selected_skills or [])
        if isinstance(item, dict)
    }
    if (
        "source_grounded_explanation" in selected_skill_ids
        and evidence_by_ref
        and not output.citations
    ):
        raise CapabilityError("source_grounded_answer_requires_citation")

    validated = []
    for citation in output.citations:
        pair = evidence_by_ref.get(citation.evidence_ref)
        if not pair:
            raise CapabilityError("citation_outside_retrieval_bundle")
        evidence, row = pair
        if citation.claim not in output.answer_markdown:
            raise CapabilityError("citation_claim_not_in_answer")
        if citation.supporting_quote not in evidence.text:
            raise CapabilityError("citation_quote_mismatch")
        claim_tokens = _tokens(citation.claim)
        quote_tokens = _tokens(citation.supporting_quote)
        if claim_tokens and quote_tokens:
            shared = len(claim_tokens & quote_tokens)
            if shared < min(2, len(claim_tokens)) or shared / max(1, len(claim_tokens)) < 0.35:
                raise CapabilityError("citation_claim_not_supported")
        claim_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", citation.claim))
        quote_numbers = set(re.findall(r"\b\d+(?:\.\d+)?\b", citation.supporting_quote))
        if claim_numbers and not claim_numbers.issubset(quote_numbers):
            raise CapabilityError("citation_numeric_claim_not_supported")
        validated.append(
            {
                "evidence_ref": citation.evidence_ref,
                "source": row.knowledge_chunk.material.original_filename,
                "locator": evidence.locator,
                "claim": citation.claim,
                "supporting_quote": citation.supporting_quote,
                "content_sha256": evidence.content_sha256,
            }
        )
    if output.citations and not evidence_by_ref:
        raise CapabilityError("citations_without_evidence")
    return validated


def _validate_personalization_and_skill_evidence(run: LearningAgentRun, output: TutorTurnOutput) -> None:
    basis = output.personalization_basis
    context_manifest = run.adaptive_context_manifest if isinstance(run.adaptive_context_manifest, dict) else {}
    evidence = [item for item in (run.evidence_manifest or []) if isinstance(item, dict)]
    if basis.context_policy_version != context_manifest.get("policy_version"):
        raise CapabilityError("personalization_context_policy_mismatch")
    if basis.context_decision_sha256 != context_manifest.get("context_decision_sha256"):
        raise CapabilityError("personalization_context_decision_mismatch")
    allowed_concepts = set(context_manifest.get("concept_keys") or [])
    evidence_categories = {str(item.get("category") or "") for item in evidence}
    observed_by_concept: dict[str, str] = {}
    context_focus = str(context_manifest.get("focus_concept_key") or "")
    if context_focus:
        observed_by_concept[context_focus] = str(context_manifest.get("observed_status") or "none")
    perceived_available = context_manifest.get("perceived_status") == "available"
    for item in evidence:
        allowed_concepts.update(str(value) for value in item.get("concept_keys", []) if value)
        if item.get("category") == "teaching_state":
            for key in item.get("concept_keys", []):
                if key:
                    observed_by_concept[str(key)] = str(item.get("observed_status") or "none")
            perceived_available = perceived_available or item.get("perceived_status") == "available"
        if item.get("category") == "learning_priorities":
            observed_by_concept.update(
                {str(key): str(value) for key, value in (item.get("observed_statuses") or {}).items() if key}
            )
            perceived_available = perceived_available or bool(item.get("perceived_guidance_available"))

    claimed = {str(value) for value in basis.concept_keys if str(value)}
    evidence_statuses: set[str] = set()
    if basis.status == "observed_evidence":
        if not claimed or not claimed.issubset(allowed_concepts):
            raise CapabilityError("personalization_claim_outside_loaded_evidence")
        evidence_statuses = {observed_by_concept.get(key, "none") for key in claimed}
        if "none" in evidence_statuses:
            raise CapabilityError("personalization_claim_without_probe_evidence")
        expected_level = "evidence_backed" if evidence_statuses == {"supported"} else "soft"
        if basis.adaptation_level != expected_level:
            raise CapabilityError("personalization_adaptation_level_mismatch")
    elif basis.status == "perceived_guidance":
        if not perceived_available:
            raise CapabilityError("perceived_guidance_without_self_assessment")
        if not claimed.issubset(allowed_concepts):
            raise CapabilityError("perceived_guidance_concept_outside_focus")
        if basis.adaptation_level != "soft":
            raise CapabilityError("perceived_guidance_must_be_soft")
    elif claimed:
        raise CapabilityError("non_evidence_personalization_must_not_name_concepts")
    elif basis.adaptation_level != "none":
        raise CapabilityError("non_evidence_adaptation_must_be_none")

    if basis.status != "observed_evidence" or evidence_statuses != {"supported"}:
        if _UNSUPPORTED_DEFINITIVE_PERSONALIZATION.search(output.answer_markdown):
            raise CapabilityError("unsupported_definitive_personalization")
        if _UNSUPPORTED_LONG_TERM_PATTERN.search(output.answer_markdown):
            raise CapabilityError("unsupported_long_term_personalization")
    needs_limitation = (
        basis.status in {"perceived_guidance", "goal_only", "insufficient"}
        or (basis.status == "observed_evidence" and evidence_statuses != {"supported"})
    )
    if needs_limitation and not basis.limitation.strip():
        raise CapabilityError("personalization_limitation_required")

    declared_categories = {str(value) for value in basis.evidence_categories if str(value)}
    available_categories = evidence_categories | ({"teaching_context"} if context_manifest else set())
    if not declared_categories.issubset(available_categories):
        raise CapabilityError("personalization_category_outside_run_evidence")

    selected = {
        str(item.get("skill_id") or "")
        for item in (run.selected_skills or [])
        if isinstance(item, dict)
    }
    if "assessment_reflection" in selected and not (
        {"assessment", "probe", "learning_priorities", "teaching_state"} & evidence_categories
    ):
        raise CapabilityError("assessment_reflection_requires_assessment_evidence")
    if "spaced_review" in selected and not (
        "review" in evidence_categories or context_manifest.get("freshness") == "review_due"
    ):
        raise CapabilityError("spaced_review_requires_due_evidence")
    if "misconception_repair" in selected and basis.status == "observed_evidence":
        if "probe" not in evidence_categories:
            raise CapabilityError("misconception_repair_requires_probe_evidence")


def _record_verified_focus_signal(run: LearningAgentRun, history: UserHistory, question: str) -> None:
    manifest = run.adaptive_context_manifest if isinstance(run.adaptive_context_manifest, dict) else {}
    keys = {str(manifest.get("focus_concept_key") or "")}
    for item in run.evidence_manifest or []:
        if not isinstance(item, dict) or item.get("category") != "concept_identity" or item.get("status") != "accepted":
            continue
        keys.update(str(value) for value in item.get("concept_keys", []) if value)
    keys.discard("")
    if len(keys) != 1:
        return
    concept_key = next(iter(keys))
    registry = ConceptRegistryEntry.objects.filter(
        user_id=run.user_id,
        learning_goal_id=run.learning_goal_id,
        concept_key=concept_key,
        status=ConceptRegistryEntry.STATUS_VERIFIED,
    ).first()
    if not registry:
        return
    ChatConceptSignal.objects.get_or_create(
        user_id=run.user_id,
        learning_goal_id=run.learning_goal_id,
        chat_history=history,
        concept_key=concept_key,
        defaults={
            "concept_label": registry.concept_label,
            "confidence": 1.0,
            "evidence_snippet": str(question or "")[:500],
            "source": ChatConceptSignal.SOURCE_CONCEPT_MAP,
            "related_concepts": [],
            "metadata": {
                "source": "adaptive_teaching_context_v2",
                "context_decision_sha256": manifest.get("context_decision_sha256", ""),
                "verified_focus": True,
            },
        },
    )


def commit_tutor_turn(run: LearningAgentRun, question: str, output: TutorTurnOutput) -> dict:
    if _FORBIDDEN_REASONING.search(output.answer_markdown):
        raise CapabilityError("hidden_reasoning_disclosure_blocked")
    _validate_personalization_and_skill_evidence(run, output)
    citations = _validate_citations(run, output)
    answer_html = markdown_to_safe_chat_html(output.answer_markdown)
    event_key = "agent-v2:" + hashlib.sha256(
        f"{run.run_id}\0{run.request_sha256}\0{output.answer_markdown}".encode("utf-8")
    ).hexdigest()[:72]
    with transaction.atomic():
        current_run = LearningAgentRun.objects.select_for_update().get(run_id=run.run_id)
        if (
            current_run.status == LearningAgentRun.STATUS_EXPIRED
            or current_run.error_code == "agent_release_retired"
            or not current_run.release.active
        ):
            raise CapabilityError("agent_release_retired")
        if current_run.cancel_requested_at:
            raise CapabilityError("agent_run_cancelled")
        conversation = require_writable_conversation(
            current_run.user.username,
            current_run.learning_goal_id,
            current_run.conversation_id,
            lock=True,
        )
        history, _created = UserHistory.objects.get_or_create(
            event_key=event_key,
            defaults={
                "user": current_run.user,
                "learning_goal": current_run.learning_goal,
                "conversation": conversation,
                "scope_status": UserHistory.SCOPE_ACTIVE,
                "question": question,
                "answer": answer_html,
                "answer_style": "agent_tutor_v2",
                "processing_status": "succeeded",
            },
        )
        _record_verified_focus_signal(current_run, history, question)
        record_completed_turn(
            current_run,
            history=history,
            question=question,
            answer_markdown=output.answer_markdown,
            teaching_strategy=output.teaching_strategy,
            next_action=output.next_action,
            confidence=output.confidence,
            selected_skills=list(current_run.selected_skills or []),
            evidence_categories=list(output.used_evidence_categories or []),
        )
    return {
        "answer": answer_html,
        "answer_markdown": output.answer_markdown,
        "teaching_strategy": output.teaching_strategy,
        "citations": citations,
        "used_evidence_categories": output.used_evidence_categories,
        "next_action": output.next_action,
        "confidence": output.confidence,
        "personalization_basis": output.personalization_basis.model_dump(mode="json"),
        "history_id": history.id,
    }
