from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from django.contrib.auth import authenticate

from learning_apps.accounts.repository import profile_repository
from learning_apps.persistence.models import UserProfile
from learning_apps.accounts.services.rate_limit_service import (
    clear_login_failures,
    get_login_rate_limit_state,
    record_login_failure,
)


@dataclass(frozen=True)
class LoginFlowResult:
    ok: bool
    code: str = ""
    user: Optional[Any] = None
    redirect_name: str = "learning_goal"
    retry_after_seconds: int = 0


def authenticate_login_user(request, username: str, password: str) -> LoginFlowResult:
    """Handle authenticate login user."""
    throttle_state = get_login_rate_limit_state(request, username)
    if throttle_state.is_limited:
        return LoginFlowResult(
            ok=False,
            code="rate_limited",
            retry_after_seconds=throttle_state.retry_after_seconds,
        )

    user = authenticate(request, username=username, password=password)
    if user is None:
        updated_state = record_login_failure(request, username)
        if updated_state.is_limited:
            return LoginFlowResult(
                ok=False,
                code="rate_limited",
                retry_after_seconds=updated_state.retry_after_seconds,
            )
        return LoginFlowResult(ok=False, code="invalid_credentials")

    clear_login_failures(request, username)
    user_profile = profile_repository.by_username(user.username)
    if user_profile and user_profile.get("role") == UserProfile.ROLE_TEACHER:
        return LoginFlowResult(ok=True, user=user, redirect_name="teacher_dashboard")

    if user_profile and not user_profile.get("preferences_completed", False):
        return LoginFlowResult(ok=True, user=user, redirect_name="setup_preferences")

    return LoginFlowResult(ok=True, user=user, redirect_name="learning_goal")
