from __future__ import annotations

import logging
import re

from learning_apps.infrastructure.services.llm_gateway import llm_gateway


logger = logging.getLogger(__name__)
TITLE_MODEL = "gpt-5.4-mini"


def _emit(message: str) -> None:
    try:
        print(message, flush=True)
    except Exception:
        logger.warning(message)


def _sanitize_title(text: str, fallback: str) -> str:
    cleaned = " ".join((text or "").replace("\n", " ").split()).strip()
    cleaned = cleaned.strip("\"'`")
    cleaned = re.sub(r"[.?!,:;]+$", "", cleaned).strip()
    if not cleaned:
        cleaned = fallback or "Learning Plan"
    words = [word for word in cleaned.split() if word]
    if words:
        cleaned = " ".join(words[:7])
    if len(cleaned) > 44:
        clipped = cleaned[:44].rstrip()
        if " " in clipped:
            clipped = clipped.rsplit(" ", 1)[0].rstrip()
        cleaned = clipped
    return cleaned or (fallback or "Learning Plan")


def refine_goal_title_with_llm(*, preference_text: str, domain: str, branch: str, fallback_title: str) -> str:
    """
    Generate a concise display title for the learning-goal sidebar.
    Designed as a low-priority async refinement step.
    """
    system_prompt = (
        "You create concise learning-goal titles for a study dashboard. "
        "Return plain text only (no markdown, no quotes). "
        "Requirements: 3-7 words, specific, user-facing, <= 44 characters."
    )
    user_prompt = (
        f"Learner goal: {preference_text}\n"
        f"Domain: {domain or 'general_learning'}\n"
        f"Branch: {branch or 'exploratory'}\n"
        f"Current fallback title: {fallback_title}\n"
        "Return one improved title."
    )

    _emit(
        f"[GoalTitle] refine_start model={TITLE_MODEL} "
        f"domain={domain or 'general_learning'} branch={branch or 'exploratory'}"
    )

    try:
        response = llm_gateway.chat_completion_or_raise(
            route="learning_goal.title",
            model=TITLE_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.2,
            max_completion_tokens=48,
            timeout=8,
        )
        raw = response.choices[0].message.content or ""
        refined = _sanitize_title(raw, fallback_title)
        _emit(f"[GoalTitle] refine_done length={len(refined)} title={refined}")
        return refined
    except Exception as exc:
        logger.warning("Goal title refine failed for %s/%s: %s", domain, branch, exc)
        _emit(f"[GoalTitle] refine_failed error={exc}")
        return _sanitize_title(fallback_title, "Learning Plan")
