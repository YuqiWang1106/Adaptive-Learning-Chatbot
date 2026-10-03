from __future__ import annotations

from typing import Any

from .base import execute_capability


def get_current_profile(username: str) -> dict[str, Any] | None:
    payload = execute_capability("profile.get_current", username=username, workflow="profile.read")
    return payload.get("profile") or None
