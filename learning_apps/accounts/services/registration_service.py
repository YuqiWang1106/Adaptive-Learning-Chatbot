from __future__ import annotations

from dataclasses import dataclass

from django.contrib.auth.hashers import make_password

from learning_apps.accounts.repository import profile_repository
from learning_apps.persistence.models import UserProfile


@dataclass(frozen=True)
class RegistrationResult:
    ok: bool
    code: str = ""


def _normalize_role(role: str | None) -> str:
    role_value = (role or "").strip().lower()
    if role_value == UserProfile.ROLE_TEACHER:
        return UserProfile.ROLE_TEACHER
    return UserProfile.ROLE_STUDENT


def _build_new_user_payload(username: str, email: str, raw_password: str, role: str | None = None) -> dict:
    """Internal helper to build new user payload."""
    normalized_role = _normalize_role(role)
    return {
        "username": username,
        "password_hash": make_password(raw_password),
        "email": email,
        "role": normalized_role,
        "age": 0,
        "academic_level": "",
        "language": "",
        "is_admin": False,
        "preferences_completed": normalized_role == UserProfile.ROLE_TEACHER,
    }


def register_new_user(username: str, email: str, raw_password: str, role: str | None = None) -> RegistrationResult:
    """Handle register new user."""
    if profile_repository.by_username(username):
        return RegistrationResult(ok=False, code="username_exists")

    if profile_repository.by_email(email):
        return RegistrationResult(ok=False, code="email_exists")

    payload = _build_new_user_payload(username=username, email=email, raw_password=raw_password, role=role)
    created = profile_repository.create(payload)
    if not created:
        return RegistrationResult(ok=False, code="create_failed")

    return RegistrationResult(ok=True)
