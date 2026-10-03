from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from learning_apps.persistence.models import LearningGoal, UserProfile


logger = logging.getLogger(__name__)


def _normalize_goal_taxonomy(value: str, fallback: str) -> str:
    raw = (value or "").strip().lower()
    if not raw:
        return fallback
    normalized = raw.replace("/", "_").replace("-", "_")
    normalized = re.sub(r"[^a-z0-9_]+", "_", normalized)
    normalized = re.sub(r"_+", "_", normalized).strip("_")
    return (normalized or fallback)[:100]


def _humanize_taxonomy(value: str, fallback: str) -> str:
    normalized = _normalize_goal_taxonomy(value, fallback)
    words = [part for part in normalized.replace("_", " ").split() if part]
    if not words:
        return "Learning"
    return " ".join(word.capitalize() for word in words)


def _cleanup_title_text(text: str) -> str:
    cleaned = " ".join((text or "").replace("\n", " ").split()).strip()
    cleaned = cleaned.strip("\"'`")
    cleaned = re.sub(r"[.?!,:;]+$", "", cleaned).strip()
    return cleaned


def _limit_title_words(text: str, max_words: int = 9) -> str:
    words = [w for w in (text or "").split() if w]
    if not words:
        return ""
    return " ".join(words[:max_words])


def _limit_title_chars(text: str, max_chars: int = 56) -> str:
    value = (text or "").strip()
    if len(value) <= max_chars:
        return value
    trimmed = value[:max_chars].rstrip()
    if " " in trimmed:
        trimmed = trimmed.rsplit(" ", 1)[0].rstrip()
    return trimmed


def _trim_trailing_connector_words(text: str) -> str:
    words = [w for w in (text or "").split() if w]
    trailing = {"for", "to", "and", "with", "in", "on", "of", "my", "the", "a", "an"}
    while words and words[-1].lower() in trailing:
        words.pop()
    return " ".join(words).strip()


def build_local_goal_title(preference_text: str, domain: str = "", branch: str = "") -> str:
    """
    Build a fast local title (no LLM call) to keep the create flow non-blocking.
    """
    raw = _cleanup_title_text(preference_text or "")
    if raw:
        lowered = raw.lower()
        leading_patterns = [
            r"^i\s+want\s+to\s+",
            r"^i\s+need\s+to\s+",
            r"^i(?:'d|\s+would)\s+like\s+to\s+",
            r"^my\s+goal\s+is\s+to\s+",
            r"^help\s+me\s+",
        ]
        for pattern in leading_patterns:
            lowered = re.sub(pattern, "", lowered, flags=re.IGNORECASE).strip()

        lowered = re.split(r"[.?!;]+", lowered, maxsplit=1)[0].strip()
        lowered = re.sub(r"^(learn|understand|study|improve|master|get\s+better\s+at)\s+", "", lowered).strip()
        candidate = _cleanup_title_text(lowered.title())
        candidate = _limit_title_words(candidate, max_words=7)
        candidate = _limit_title_chars(candidate, max_chars=44)
        candidate = _trim_trailing_connector_words(candidate)
        if len(candidate) >= 8:
            return candidate

    branch_label = _humanize_taxonomy(branch, "exploratory")
    domain_label = _humanize_taxonomy(domain, "general_learning")
    fallback = f"{branch_label} Learning Plan"
    if branch_label.lower() == "exploratory":
        fallback = f"{domain_label} Learning Plan"
    return _limit_title_chars(fallback, max_chars=44)


def _get_user(username: str) -> Optional[UserProfile]:
    """Internal helper to return user."""
    return UserProfile.objects.filter(username=username).first()


def _goal_to_dict(goal: LearningGoal) -> Dict[str, Any]:
    """Internal helper to handle goal to dict."""
    return {
        "id": goal.id,
        "user_id": goal.user_id,
        "title": goal.title,
        "preference_text": goal.preference_text,
        "domain": goal.domain,
        "branch": goal.branch,
        "status": goal.status,
        "self_assessment_guides": goal.self_assessment_guides or {},
        "goal_snapshot": goal.goal_snapshot or {},
        "vector_store_status": goal.vector_store_status or "",
        "created_at": goal.created_at,
        "updated_at": goal.updated_at,
    }


def list_learning_goals(username: str) -> List[Dict[str, Any]]:
    """List learning goals."""
    user = _get_user(username)
    if not user:
        return []

    goals = LearningGoal.objects.filter(user=user).order_by("-updated_at", "-id")
    return [_goal_to_dict(goal) for goal in goals]


def get_learning_goal(username: str, learning_goal_id: int) -> Optional[Dict[str, Any]]:
    """Return learning goal."""
    user = _get_user(username)
    if not user:
        return None

    goal = LearningGoal.objects.filter(user=user, id=int(learning_goal_id)).first()
    if not goal:
        return None
    return _goal_to_dict(goal)


def create_learning_goal(
    username: str,
    preference_text: str,
    domain: str = "",
    branch: str = "",
) -> Optional[int]:
    """Create learning goal."""
    user = _get_user(username)
    if not user:
        return None

    preference_text = (preference_text or "").strip()
    if not preference_text:
        return None

    try:
        domain_value = _normalize_goal_taxonomy(domain or "", "general_learning")
        branch_value = _normalize_goal_taxonomy(branch or "", "exploratory")
        title = build_local_goal_title(preference_text, domain_value, branch_value)
        goal = LearningGoal.objects.create(
            user=user,
            title=title,
            preference_text=preference_text,
            domain=domain_value,
            branch=branch_value,
            self_assessment_guides={},
            goal_snapshot={
                "preference_text": preference_text,
                "domain": domain_value,
                "branch": branch_value,
                "version": 1,
            },
            status=LearningGoal.STATUS_GOAL_SUBMITTED,
        )
        return int(goal.id)
    except Exception as exc:
        logger.exception("Failed to create learning goal for %s: %s", username, exc)
        return None


def update_learning_goal_status(username: str, learning_goal_id: int, status: str) -> bool:
    """Update learning goal status."""
    user = _get_user(username)
    if not user:
        return False

    updated = LearningGoal.objects.filter(user=user, id=int(learning_goal_id)).update(status=status)
    return updated > 0


def set_goal_title(username: str, learning_goal_id: int, title: str) -> bool:
    """Set goal title."""
    user = _get_user(username)
    if not user:
        return False

    cleaned_title = _limit_title_chars(_cleanup_title_text(title), max_chars=44)
    if not cleaned_title:
        return False
    updated = LearningGoal.objects.filter(user=user, id=int(learning_goal_id)).update(title=cleaned_title)
    return updated > 0


def get_latest_goal_id(username: str) -> Optional[int]:
    """Return latest goal id."""
    user = _get_user(username)
    if not user:
        return None

    goal = LearningGoal.objects.filter(user=user).order_by("-updated_at", "-id").first()
    return int(goal.id) if goal else None
