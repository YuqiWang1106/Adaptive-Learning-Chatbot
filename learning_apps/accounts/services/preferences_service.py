from __future__ import annotations

from typing import Any, Dict

from learning_apps.accounts.repository import profile_repository


def build_preferences_initial_data(user_profile: Dict[str, Any]) -> Dict[str, Any]:
    """Build preferences initial data."""
    return {
        "age": user_profile.get("age") or "",
        "academic_level": user_profile.get("academic_level") or "",
    }


def build_preferences_update_data(cleaned_data: Dict[str, Any]) -> Dict[str, Any]:
    """Build preferences update data."""
    return {
        "age": cleaned_data["age"],
        "academic_level": cleaned_data["academic_level"],
        "preferences_completed": True,
    }


def save_user_preferences(username: str, cleaned_data: Dict[str, Any]) -> bool:
    """Save user preferences."""
    update_data = build_preferences_update_data(cleaned_data)
    return profile_repository.update(username, update_data)
