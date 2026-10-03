from __future__ import annotations

from typing import Any, Dict, List, Optional

from learning_apps.learning_goal.services import learning_goal_service


def list_learning_goals_for_user(username: str) -> List[Dict[str, Any]]:
    """List learning goals for user."""
    return learning_goal_service.list_learning_goals(username)


def create_learning_goal_record(
    username: str,
    preference_text: str,
    domain: str,
    branch: str,
) -> Optional[int]:
    """Create learning goal record."""
    return learning_goal_service.create_learning_goal(
        username=username,
        preference_text=preference_text,
        domain=domain,
        branch=branch,
    )


def get_learning_goal_record(username: str, learning_goal_id: int) -> Optional[Dict[str, Any]]:
    """Return learning goal record."""
    return learning_goal_service.get_learning_goal(username, learning_goal_id)
