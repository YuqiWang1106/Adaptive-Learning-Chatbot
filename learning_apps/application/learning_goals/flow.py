from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from learning_apps.learning_goal.categorization_service import categorize_learning_goal
from learning_apps.learning_goal.goal_repository_service import (
    create_learning_goal_record,
    get_learning_goal_record,
    list_learning_goals_for_user,
)
from learning_apps.learning_goal.review_service import review_preference_text

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CreateLearningGoalResult:
    ok: bool
    code: str = ""
    learning_goal_id: Optional[int] = None


@dataclass(frozen=True)
class OpenLearningGoalResult:
    ok: bool
    code: str = ""
    redirect_name: str = "learning_goal"


def list_user_learning_goals(username: str) -> List[Dict[str, Any]]:
    """List user learning goals."""
    return list_learning_goals_for_user(username)


ProgressCallback = Callable[[str, int, str], None]


def _emit(message: str) -> None:
    try:
        print(message, flush=True)
    except Exception:
        logger.warning(message)


def create_learning_goal_from_preference(
    username: str,
    raw_preference_text: str,
    progress_callback: Optional[ProgressCallback] = None,
) -> CreateLearningGoalResult:
    """Create learning goal from preference."""
    _emit(
        f"[LearningGoalFlow] started user={username} raw_preference_len={len((raw_preference_text or '').strip())}"
    )
    if progress_callback:
        progress_callback("reviewing", 12, "Reading your goal and cleaning the signal.")

    review = review_preference_text(raw_preference_text)
    if not review.ok:
        return CreateLearningGoalResult(ok=False, code=review.code)

    if progress_callback:
        progress_callback("classifying", 34, "Mapping your goal to the right learning domain.")

    classification = categorize_learning_goal(review.cleaned_text)
    _emit(
        f"[LearningGoalFlow] classified user={username} "
        f"domain={classification.get('domain') or ''} branch={classification.get('branch') or ''}"
    )

    domain = str(classification.get("domain", ""))
    branch = str(classification.get("branch", ""))

    if progress_callback:
        progress_callback("saving", 72, "Saving your learning goal.")

    learning_goal_id = create_learning_goal_record(
        username=username,
        preference_text=review.cleaned_text,
        domain=domain,
        branch=branch,
    )
    if not learning_goal_id:
        _emit(f"[LearningGoalFlow] create_failed user={username}")
        return CreateLearningGoalResult(ok=False, code="create_failed")

    if progress_callback:
        progress_callback("ready", 100, "Your learning path is ready.")
    _emit(f"[LearningGoalFlow] completed user={username} goal_id={learning_goal_id}")

    return CreateLearningGoalResult(ok=True, code="created", learning_goal_id=learning_goal_id)


def resolve_open_learning_goal(username: str, learning_goal_id: int) -> OpenLearningGoalResult:
    """Resolve open learning goal."""
    goal = get_learning_goal_record(username, learning_goal_id)
    if not goal:
        return OpenLearningGoalResult(ok=False, code="not_found", redirect_name="learning_goal")

    status = (goal.get("status") or "").strip()
    if status != "self_assessment_completed":
        return OpenLearningGoalResult(ok=True, code="pending_assessment", redirect_name="self_assessment")

    return OpenLearningGoalResult(ok=True, code="ready_for_chat", redirect_name="chat")
