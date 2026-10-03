"""Account application boundary used by presentation adapters.

Authentication and OAuth remain infrastructure workflows, not Agent Tools.
Learning-product profile reads use the shared P6 Capability Runtime.
"""

from __future__ import annotations

from learning_apps.application.workflows.profiles import get_current_profile
from learning_apps.persistence.models import UserProfile

from .services.google_oauth_service import (
    build_google_authorize_url,
    ensure_profile_for_google_user,
    exchange_code_for_userinfo,
    is_google_configured,
    sync_django_user,
)
from .services.login_service import authenticate_login_user
from .services.preferences_service import build_preferences_initial_data, save_user_preferences
from .services.registration_service import register_new_user


ROLE_STUDENT = UserProfile.ROLE_STUDENT
ROLE_TEACHER = UserProfile.ROLE_TEACHER


__all__ = [
    "ROLE_STUDENT",
    "ROLE_TEACHER",
    "authenticate_login_user",
    "build_google_authorize_url",
    "build_preferences_initial_data",
    "get_current_profile",
    "ensure_profile_for_google_user",
    "exchange_code_for_userinfo",
    "is_google_configured",
    "register_new_user",
    "save_user_preferences",
    "sync_django_user",
]
