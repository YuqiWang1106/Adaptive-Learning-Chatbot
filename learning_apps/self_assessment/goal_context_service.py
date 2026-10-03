from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from learning_apps.learning_goal.services import learning_goal_service


@dataclass(frozen=True)
class GoalContextResult:
    ok: bool
    code: str = ""
    learning_goal_id: Optional[int] = None
    goal: Optional[Dict[str, Any]] = None
    domain: str = ""
    branch: str = ""
    preference: str = ""


def resolve_goal_context(
    username: str,
    learning_goal_id: Optional[int],
    domain: str,
    branch: str,
    preference: str,
) -> GoalContextResult:
    """Resolve goal context."""
    goal = None
    resolved_domain = domain or ""
    resolved_branch = branch or ""
    resolved_preference = preference or ""

    if learning_goal_id:
        goal = learning_goal_service.get_learning_goal(username, learning_goal_id)
        if not goal:
            return GoalContextResult(ok=False, code="goal_not_found", learning_goal_id=learning_goal_id)

        resolved_domain = goal.get("domain") or resolved_domain
        resolved_branch = goal.get("branch") or resolved_branch
        resolved_preference = goal.get("preference_text") or goal.get("title") or resolved_preference

    return GoalContextResult(
        ok=True,
        code="ok",
        learning_goal_id=learning_goal_id,
        goal=goal,
        domain=resolved_domain,
        branch=resolved_branch,
        preference=resolved_preference,
    )


def list_learning_goals_for_sidebar(username: str) -> List[Dict[str, Any]]:
    """List learning goals for sidebar."""
    return learning_goal_service.list_learning_goals(username)
