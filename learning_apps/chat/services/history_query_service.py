from __future__ import annotations

from django.db.models import Q

from learning_apps.persistence.models import AnswerGroundingDecision, UserProfile
from .conversation_repository_service import visible_history_queryset


def read_history_page(
    username: str,
    *,
    learning_goal_id: int,
    page: int = 1,
    per_page: int = 20,
    search: str | None = None,
) -> dict:
    user = UserProfile.objects.filter(username=username).first()
    if user is None:
        return {"history": [], "total": 0}
    queryset = visible_history_queryset().filter(
        user=user,
        learning_goal_id=learning_goal_id,
        conversation__user=user,
        conversation__learning_goal_id=learning_goal_id,
    )
    if search:
        queryset = queryset.filter(Q(question__icontains=search) | Q(answer__icontains=search))
    total = queryset.count()
    offset = max(page - 1, 0) * per_page
    rows = list(
        queryset.order_by("-timestamp")[offset : offset + per_page].values(
            "id",
            "question",
            "answer",
            "answer_style",
            "timestamp",
        )
    )
    by_id = {row["id"]: row for row in rows}
    decisions = AnswerGroundingDecision.objects.filter(
        user=user,
        user_history_id__in=by_id,
    ).prefetch_related("citations__retrieval_evidence__knowledge_chunk__material")
    for decision in decisions:
        row = by_id.get(decision.user_history_id)
        if row is None:
            continue
        row["grounding_status"] = decision.status
        row["citations"] = [
            {
                "citation_id": citation.citation_id,
                "claim_ordinal": citation.claim_ordinal,
                "coordinate_space": "canonical_plain_text",
                "canonical_answer_sha256": decision.answer_sha256,
                "claim_sha256": citation.claim_sha256,
                "locator": citation.retrieval_evidence.knowledge_chunk.locator,
                "source_label": citation.retrieval_evidence.knowledge_chunk.material.original_filename,
            }
            for citation in decision.citations.all()
            if citation.retrieval_evidence.knowledge_chunk_id
        ]
    for row in rows:
        row.setdefault("grounding_status", "legacy_unverified")
        row.setdefault("citations", [])
    return {"history": rows, "total": total}
