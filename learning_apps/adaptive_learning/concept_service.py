from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Dict, List

from learning_apps.persistence.models import ChatConceptSignal, LearnerMasteryState, LearningGoal, UserProfile
from learning_apps.chat.services.conversation_repository_service import visible_history_queryset

from .concept_identity_service import (
    chat_signal_source_for_resolution,
    resolution_metadata,
    resolve_concept_identity,
)
from .mastery_service import _emit_adaptive_trace


@dataclass(frozen=True)
class ConceptSignalPayload:
    concept_key: str
    concept_label: str
    confidence: float
    evidence_snippet: str
    source: str
    related_concepts: List[str]
    metadata: Dict[str, Any]


def _existing_mastery_surface_match(
    user: UserProfile,
    goal: LearningGoal,
    text: str,
) -> LearnerMasteryState | None:
    """Keep chat context attached to an already tracked goal concept.

    This compatibility read path does not verify a new identity or grant
    mastery. It only prevents a free-form sentence from replacing an existing
    mastery concept with a provisional query slug when no Concept Map exists.
    """
    query_tokens = set(re.findall(r"[a-z0-9]+", str(text or "").lower()))
    if not query_tokens:
        return None
    from .concept_identity_service import CONCEPT_ALIASES

    best: tuple[float, LearnerMasteryState] | None = None
    for state in LearnerMasteryState.objects.filter(user=user, learning_goal=goal):
        phrases = [state.concept_key.replace("_", " "), *CONCEPT_ALIASES.get(state.concept_key, [])]
        for phrase in phrases:
            tokens = set(re.findall(r"[a-z0-9]+", str(phrase or "").lower()))
            overlap = len(tokens & query_tokens)
            if not tokens or not (
                tokens.issubset(query_tokens)
                or (len(tokens) >= 2 and overlap >= 2 and overlap / len(tokens) >= 0.5)
            ):
                continue
            score = overlap / max(len(query_tokens), 1)
            if best is None or score > best[0] or (score == best[0] and state.concept_key < best[1].concept_key):
                best = (score, state)
    return best[1] if best else None


def extract_concept_signal(
    goal: LearningGoal,
    question: str,
    answer: str,
    *,
    user: UserProfile | None = None,
    allow_llm: bool = True,
) -> ConceptSignalPayload:
    """Compatibility adapter over the single Concept Identity pipeline."""
    text = f"{question or ''}\n{answer or ''}".strip()
    resolution = resolve_concept_identity(
        user,
        goal,
        text,
        source_context="chat",
        allow_llm=allow_llm,
    )
    if user:
        surface_state = _existing_mastery_surface_match(user, goal, text)
        if surface_state is not None and (
            resolution.source in {"new_candidate", "fallback", "semantic_match", "trusted_key"}
            or resolution.concept_key == surface_state.concept_key
        ):
            return ConceptSignalPayload(
                concept_key=surface_state.concept_key,
                concept_label=surface_state.concept_key.replace("_", " ").title(),
                confidence=max(float(resolution.confidence or 0.0), 0.82),
                evidence_snippet=resolution.evidence_snippet,
                source=ChatConceptSignal.SOURCE_VECTOR_MATCH,
                related_concepts=[],
                metadata={
                    "legacy_surface_state_match": True,
                    "similarity": max(float(resolution.confidence or 0.0), 0.82),
                    "similarity_margin": 0.08,
                    "identity_candidate_key": resolution.concept_key,
                    "identity_candidate_status": resolution.lifecycle_status,
                    "identity_candidate_decision_id": resolution.decision_id,
                },
            )
    signal_source = chat_signal_source_for_resolution(resolution.source)
    return ConceptSignalPayload(
        concept_key=resolution.concept_key,
        concept_label=resolution.concept_label,
        confidence=resolution.confidence,
        evidence_snippet=resolution.evidence_snippet,
        source=signal_source,
        related_concepts=resolution.related_concepts,
        metadata={
            **resolution.decision_scores,
            **resolution_metadata(resolution),
            "decision_scores": resolution.decision_scores,
            "legacy_signal_source": signal_source,
        },
    )


def record_chat_concept_signal_for_history(
    *,
    username: str,
    learning_goal_id: int,
    history_id: int | None = None,
) -> ChatConceptSignal | None:
    user = UserProfile.objects.filter(username=username).first()
    goal = LearningGoal.objects.filter(user=user, id=int(learning_goal_id)).first() if user else None
    if not user or not goal:
        _emit_adaptive_trace(
            f"concept_signal_skipped user={username} goal_id={learning_goal_id} reason=missing_user_or_goal"
        )
        return None

    history_qs = visible_history_queryset().filter(user=user, learning_goal=goal)
    history = history_qs.filter(id=int(history_id)).first() if history_id else history_qs.order_by("-timestamp").first()
    if not history:
        _emit_adaptive_trace(f"concept_signal_skipped user={username} goal_id={learning_goal_id} reason=no_chat_history")
        return None

    existing = ChatConceptSignal.objects.filter(user=user, learning_goal=goal, chat_history=history).first()
    if existing:
        _emit_adaptive_trace(
            "concept_signal_reused "
            f"user={username} goal_id={learning_goal_id} history_id={history.id} "
            f"concept={existing.concept_key} source={existing.source}"
        )
        return existing

    payload = extract_concept_signal(goal, history.question or "", history.answer or "", user=user)
    signal = ChatConceptSignal.objects.create(
        user=user,
        learning_goal=goal,
        chat_history=history,
        concept_key=payload.concept_key,
        concept_label=payload.concept_label,
        confidence=payload.confidence,
        evidence_snippet=payload.evidence_snippet,
        source=payload.source,
        related_concepts=payload.related_concepts,
        metadata=payload.metadata,
    )
    _emit_adaptive_trace(
        "concept_signal_saved "
        f"user={username} goal_id={learning_goal_id} history_id={history.id} signal_id={signal.id} "
        f"concept={signal.concept_key} label='{signal.concept_label}' "
        f"confidence={signal.confidence:.4f} source={signal.source}"
    )
    return signal
