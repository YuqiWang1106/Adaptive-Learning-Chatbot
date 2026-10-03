from __future__ import annotations

from typing import Any, Dict

from learning_apps.learning_goal.services.preference_classification_service import (
    classify_preference_only,
)


def categorize_learning_goal(preference_text: str) -> Dict[str, Any]:
    """Categorize learning goal."""
    return classify_preference_only(preference_text)
