"""Trusted persistence boundary for grounded chat answers and citations."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
import unicodedata
from typing import Any

from django.db import transaction
from learning_apps.infrastructure.services.html_sanitizer_service import markdown_to_safe_chat_html
from learning_apps.chat.services.conversation_repository_service import (
    ensure_active_conversation_key,
    require_writable_conversation,
)

from learning_apps.chat.services.citation_grounding_service import (
    CITATION_GROUNDING_POLICY_VERSION,
    CitationGroundingStatus,
    extract_response_answer_text,
    validate_response_citations,
)
from learning_apps.knowledge.services.retrieval_repository_service import (
    load_scoped_retrieval_decision,
    load_trusted_retrieval_bundle,
)
from learning_apps.persistence.models import (
    AnswerCitation,
    AnswerGroundingDecision,
    LearningGoal,
    RetrievalDecision,
    UserHistory,
    UserProfile,
)


ANSWER_PROMPT_VERSION = "p2.3-chat-extractive-v1"
VERIFIER_PROMPT_VERSION = "p2.3-extractive-verifier-v1"
ANSWER_MODEL = "deterministic-extractive"
GENERAL_ANSWER_MODEL = "gpt-4.1-2025-04-14"
_INJECTION_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+instructions?\b",
        r"\b(?:system|developer)\s+(?:prompt|message)\b",
        r"<\/?(?:system|developer|assistant|tool)(?:\s|>)",
        r"\b(?:call|invoke|execute)\s+(?:the\s+)?(?:tool|function|mcp)\b",
        r"\b(?:override|bypass)\s+(?:the\s+)?(?:policy|safety|instructions?)\b",
        r"\bmastery_write_authorized\b",
    )
)

_GENERAL_MATERIAL_CLAIM_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\baccording\s+to\s+(?:your|the)\s+(?:(?:uploaded|provided|attached)\s+)?(?:file|document|pdf|materials?|notes?|slides?)\b",
        r"\bbased\s+on\s+(?:your|the)\s+(?:(?:uploaded|provided|attached)\s+)?(?:file|document|pdf|materials?|notes?|slides?)\b",
        r"\b(?:from|in)\s+(?:your|the)\s+(?:(?:uploaded|provided|attached)\s+)?(?:file|document|pdf|materials?|notes?|slides?)\b",
        r"\b(?:your|the)\s+(?:(?:uploaded|provided|attached)\s+)?(?:file|document|pdf|materials?|notes?|slides?)\s+(?:says?|states?|mentions?|shows?|explains?|indicates?)\b",
        r"\b(?:i\s+)?(?:read|found|retrieved|reviewed|checked|saw)\s+(?:in\s+)?(?:your|the)\s+(?:(?:uploaded|provided|attached)\s+)?(?:file|document|pdf|materials?|notes?|slides?)\b",
        r"\bpage\s+\d+\s+(?:of|in|from)\s+(?:your|the)\s+(?:file|document|pdf|materials?|notes?|slides?)\b",
        r"\bsegun\s+(?:tu|el|su)\s+(?:archivo|documento|pdf|material)\b",
        r"\bselon\s+(?:votre|le)\s+(?:fichier|document|pdf|materiel)\b",
        r"根据(?:你|您)?(?:上传|提供|附加)的?(?:文件|文档|pdf|材料|讲义|课件)",
        r"(?:你|您)(?:上传|提供|附加)的?(?:文件|文档|pdf|材料|讲义|课件)(?:中|里)?(?:写道|指出|说明|提到|显示)",
        r"在(?:你|您)?(?:上传|提供|附加)的?(?:文件|文档|pdf|材料|讲义|课件)(?:中|里)",
        r"(?:我(?:已)?(?:查阅|读取|检索|看过)|第\s*\d+\s*页).{0,24}(?:文件|文档|pdf|材料|讲义|课件)",
        r"(?:アップロード|添付).{0,16}(?:ファイル|文書|pdf|資料)",
        r"(?:업로드|첨부).{0,16}(?:파일|문서|pdf|자료)",
    )
)


def _control_normalized_text(value: Any) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) not in {"Cf", "Cc"}
        or character in "\t\n\r"
    )
    # Accent removal makes the small deterministic multilingual boundary less
    # fragile without retaining or sending the answer to another model.
    normalized = "".join(
        character
        for character in unicodedata.normalize("NFKD", normalized)
        if not unicodedata.combining(character)
    )
    return " ".join(normalized.casefold().split())


def _claims_uploaded_material_without_evidence(answer: str) -> bool:
    normalized = _control_normalized_text(answer)
    return any(pattern.search(normalized) for pattern in _GENERAL_MATERIAL_CLAIM_PATTERNS)


def _response_answer_binding(response_body: dict | None, answer: str) -> tuple[str, bool]:
    """Derive the canonical answer and reject a separate, mismatched value."""

    if response_body is None:
        return str(answer or "").strip(), True
    response_answer = extract_response_answer_text(response_body).strip()
    supplied_answer = str(answer or "").strip()
    if supplied_answer and supplied_answer != response_answer:
        return supplied_answer, False
    return response_answer, bool(response_answer)


def _sha(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class GroundedAnswerResult:
    accepted: bool
    answer: str
    history_id: int | None
    grounding_decision_id: str
    reason_code: str
    grounding_status: str
    citations: tuple[dict[str, Any], ...]


def build_extractive_response_body(bundle) -> tuple[str, dict, dict[str, str]]:
    """Build a conservative answer wholly copied from the top persisted chunk."""

    evidence = bundle.outcome.evidence[0]
    if any(
        pattern.search(item.text)
        for item in bundle.outcome.evidence
        for pattern in _INJECTION_PATTERNS
    ):
        raise ValueError("retrieved_instruction_injection_detected")
    answer = evidence.text.strip()
    if len(answer) > 1200:
        cutoff = max(answer.rfind(". ", 0, 1200), answer.rfind("\n", 0, 1200))
        answer = answer[: cutoff + 1 if cutoff >= 80 else 1200].strip()
    row = bundle.evidence_rows[0]
    provider_ref = row.evidence_ref
    response_body = {
        "output": [
            {
                "type": "file_search_call",
                "results": [
                    {
                        "provider_reference": provider_ref,
                        "text": evidence.text,
                        "score": evidence.scores.hybrid,
                    }
                ],
            },
            {
                "type": "message",
                "content": [
                    {
                        "type": "output_text",
                        "text": answer,
                        "annotations": [
                            {
                                "type": "file_citation",
                                "provider_reference": provider_ref,
                                "start_index": 0,
                                "end_index": len(answer),
                                "quote": answer,
                            }
                        ],
                    }
                ],
            },
        ]
    }
    return answer, response_body, {provider_ref: provider_ref}


def persist_chat_answer(
    *,
    username: str,
    learning_goal_id: int,
    retrieval_decision_id: str,
    question: str,
    answer_style: str,
    answer: str = "",
    response_body: dict | None = None,
    provider_reference_map: dict[str, str] | None = None,
    conversation_key: str | None = None,
) -> GroundedAnswerResult:
    """Persist history only if current DB evidence admits the exact answer."""

    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(id=int(learning_goal_id), user=user).first() if user else None
    if not user or not goal:
        raise ValueError("scope_not_found")
    captured_conversation_key = conversation_key or ensure_active_conversation_key(
        username, goal.id
    )
    decision = load_scoped_retrieval_decision(
        username,
        goal.id,
        retrieval_decision_id,
        expected_purpose="chat_answer",
    )
    bundle = None
    grounding = None
    if decision.status == RetrievalDecision.STATUS_ACCEPTED:
        bundle = load_trusted_retrieval_bundle(
            username, goal.id, retrieval_decision_id, expected_purpose="chat_answer"
        )
        try:
            if response_body is None:
                answer, response_body, provider_reference_map = build_extractive_response_body(bundle)
            elif any(
                pattern.search(item.text)
                for item in bundle.outcome.evidence
                for pattern in _INJECTION_PATTERNS
            ):
                raise ValueError("retrieved_instruction_injection_detected")
        except ValueError:
            status = AnswerGroundingDecision.STATUS_BLOCKED
            reason = "retrieved_instruction_injection_detected"
            response_body = response_body or {"output": []}
            provider_reference_map = {}
            grounding = None
        else:
            lifecycle = {row.evidence_ref: "active" for row in bundle.evidence_rows}
            grounding = validate_response_citations(
                response_body,
                retrieval_bundle=bundle.outcome,
                expected_scope=bundle.scope,
                lifecycle_by_evidence_id=lifecycle,
                provider_reference_map=provider_reference_map or {},
            )
            status = (
                AnswerGroundingDecision.STATUS_ACCEPTED
                if grounding.status is CitationGroundingStatus.ACCEPTED
                else AnswerGroundingDecision.STATUS_BLOCKED
            )
            reason = grounding.reason_code
    elif decision.status == RetrievalDecision.STATUS_ABSTAINED:
        status = AnswerGroundingDecision.STATUS_NOT_REQUIRED
        reason = "retrieval_abstained_general_answer"
    else:
        status = AnswerGroundingDecision.STATUS_BLOCKED
        reason = decision.failure_code or "retrieval_failed"
    clean_answer, answer_binding_valid = _response_answer_binding(response_body, answer)
    if not answer_binding_valid and status != AnswerGroundingDecision.STATUS_BLOCKED:
        status = AnswerGroundingDecision.STATUS_BLOCKED
        reason = "answer_response_mismatch"
    if (
        decision.status == RetrievalDecision.STATUS_ABSTAINED
        and clean_answer
        and _claims_uploaded_material_without_evidence(clean_answer)
    ):
        status = AnswerGroundingDecision.STATUS_BLOCKED
        reason = "material_claim_without_evidence"
    if not clean_answer and status != AnswerGroundingDecision.STATUS_BLOCKED:
        status = AnswerGroundingDecision.STATUS_BLOCKED
        reason = "answer_missing"

    idempotency_key = _sha(
        {
            "user": user.user_id,
            "goal": goal.id,
            "conversation_key": captured_conversation_key,
            "retrieval": decision.decision_id,
            "question": _sha(question),
            "answer": _sha(clean_answer),
            "policy": CITATION_GROUNDING_POLICY_VERSION,
            "prompt": ANSWER_PROMPT_VERSION,
        }
    )
    with transaction.atomic():
        conversation = require_writable_conversation(
            username,
            goal.id,
            captured_conversation_key,
            lock=True,
        )
        existing = AnswerGroundingDecision.objects.select_for_update().filter(
            idempotency_key=idempotency_key
        ).first()
        if existing:
            return _result_dto(existing, clean_answer)
        locked_decision = load_scoped_retrieval_decision(
            username,
            goal.id,
            retrieval_decision_id,
            expected_purpose="chat_answer",
            lock=True,
        )
        if locked_decision.status == RetrievalDecision.STATUS_ACCEPTED:
            locked_bundle = load_trusted_retrieval_bundle(
                username,
                goal.id,
                retrieval_decision_id,
                expected_purpose="chat_answer",
                lock=True,
            )
            injection_detected = any(
                pattern.search(item.text)
                for item in locked_bundle.outcome.evidence
                for pattern in _INJECTION_PATTERNS
            )
            lifecycle = {row.evidence_ref: "active" for row in locked_bundle.evidence_rows}
            rechecked = (
                validate_response_citations(
                    response_body,
                    retrieval_bundle=locked_bundle.outcome,
                    expected_scope=locked_bundle.scope,
                    lifecycle_by_evidence_id=lifecycle,
                    provider_reference_map=provider_reference_map or {},
                )
                if not injection_detected
                else None
            )
            locked_answer, locked_binding_valid = _response_answer_binding(
                response_body,
                clean_answer,
            )
            if (
                injection_detected
                or not rechecked.accepted
                or not locked_binding_valid
                or locked_answer != clean_answer
            ):
                status = AnswerGroundingDecision.STATUS_BLOCKED
                reason = (
                    "retrieved_instruction_injection_detected"
                    if injection_detected
                    else "answer_response_mismatch"
                    if not locked_binding_valid or locked_answer != clean_answer
                    else rechecked.reason_code
                )
            grounding = rechecked
            bundle = locked_bundle
        elif locked_decision.status == RetrievalDecision.STATUS_ABSTAINED:
            if _claims_uploaded_material_without_evidence(clean_answer):
                status = AnswerGroundingDecision.STATUS_BLOCKED
                reason = "material_claim_without_evidence"
        else:
            status = AnswerGroundingDecision.STATUS_BLOCKED
            reason = locked_decision.failure_code or "retrieval_failed"

        history = None
        if status in {
            AnswerGroundingDecision.STATUS_ACCEPTED,
            AnswerGroundingDecision.STATUS_NOT_REQUIRED,
        }:
            history = UserHistory.objects.create(
                user=user,
                learning_goal=goal,
                conversation=conversation,
                event_key=f"evt_{idempotency_key[:48]}",
                scope_status=UserHistory.SCOPE_ACTIVE,
                question=question,
                answer=markdown_to_safe_chat_html(clean_answer),
                answer_style=answer_style or "informational",
                processing_status="succeeded",
            )
        citations = (
            grounding.citations
            if grounding
            and grounding.accepted
            and status == AnswerGroundingDecision.STATUS_ACCEPTED
            else ()
        )
        persisted = AnswerGroundingDecision.objects.create(
            user=user,
            learning_goal=goal,
            user_history=history,
            retrieval_decision=locked_decision,
            status=status,
            reason_code=reason[:96],
            policy_version=CITATION_GROUNDING_POLICY_VERSION,
            answer_sha256=_sha(clean_answer),
            claim_set_sha256=_sha(
                [
                    [citation.answer_start, citation.answer_end, _sha(citation.quoted_text)]
                    for citation in citations
                ]
            ),
            support_decision_sha256=_sha(
                grounding.to_metadata() if grounding else {"status": status, "reason": reason}
            ),
            scope_sha256=locked_decision.scope_sha256,
            taxonomy_sha256=locked_decision.taxonomy_sha256,
            lifecycle_bundle_sha256=locked_decision.lifecycle_bundle_sha256,
            answer_model=(
                GENERAL_ANSWER_MODEL
                if status == AnswerGroundingDecision.STATUS_NOT_REQUIRED
                else ANSWER_MODEL
            ),
            prompt_version=ANSWER_PROMPT_VERSION,
            verifier_model="deterministic-extractive",
            verifier_prompt_version=VERIFIER_PROMPT_VERSION,
            idempotency_key=idempotency_key,
            mastery_write_authorized=False,
        )
        if citations and bundle:
            rows = {row.evidence_ref: row for row in bundle.evidence_rows}
            AnswerCitation.objects.bulk_create(
                [
                    AnswerCitation(
                        citation_id=hashlib.sha256(
                            f"{persisted.decision_id}\0{citation.citation_id}\0{index}".encode()
                        ).hexdigest(),
                        grounding_decision=persisted,
                        retrieval_evidence=rows[citation.evidence_id],
                        claim_ordinal=index,
                        claim_sha256=_sha(clean_answer[citation.answer_start:citation.answer_end]),
                        answer_start=citation.answer_start,
                        answer_end=citation.answer_end,
                        relation=AnswerCitation.RELATION_SUPPORTED,
                        confidence_band="high",
                        support_quote_sha256=_sha(citation.quoted_text),
                        verifier_version=VERIFIER_PROMPT_VERSION,
                        mastery_write_authorized=False,
                    )
                    for index, citation in enumerate(citations, start=1)
                ]
            )
    return _result_dto(persisted, clean_answer)


def _result_dto(decision: AnswerGroundingDecision, answer: str) -> GroundedAnswerResult:
    citations = tuple(
        {
            "citation_id": row.citation_id,
            "claim_ordinal": row.claim_ordinal,
            "answer_start": row.answer_start,
            "answer_end": row.answer_end,
            "coordinate_space": "canonical_plain_text",
            "canonical_answer_sha256": decision.answer_sha256,
            "locator": row.retrieval_evidence.knowledge_chunk.locator,
            "source_label": row.retrieval_evidence.knowledge_chunk.material.original_filename,
        }
        for row in decision.citations.select_related(
            "retrieval_evidence__knowledge_chunk__material"
        ).order_by("claim_ordinal", "citation_id")
        if row.retrieval_evidence.knowledge_chunk_id
    )
    return GroundedAnswerResult(
        accepted=decision.status in {
            AnswerGroundingDecision.STATUS_ACCEPTED,
            AnswerGroundingDecision.STATUS_NOT_REQUIRED,
        },
        answer=answer,
        history_id=decision.user_history_id,
        grounding_decision_id=decision.decision_id,
        reason_code=decision.reason_code,
        grounding_status=decision.status,
        citations=citations,
    )


__all__ = [
    "GroundedAnswerResult",
    "build_extractive_response_body",
    "persist_chat_answer",
]
