from __future__ import annotations

import logging
from typing import Any

from django.db import transaction

from learning_apps.persistence.models import UserProfile


logger = logging.getLogger(__name__)


class UnsupportedProfileFieldError(ValueError):
    pass


class ProfileRepository:
    """Persistence boundary for account profile records."""

    _writable_fields = {
        "password_hash",
        "email",
        "role",
        "age",
        "academic_level",
        "language",
        "is_admin",
        "preferences_completed",
    }

    def create(self, data: dict[str, Any]) -> bool:
        try:
            with transaction.atomic():
                UserProfile.objects.create(
                    username=data["username"],
                    password_hash=data["password_hash"],
                    email=data.get("email") or "",
                    role=data.get("role") or UserProfile.ROLE_STUDENT,
                    age=int(data.get("age") or 0),
                    academic_level=data.get("academic_level") or "",
                    language=data.get("language") or "",
                    is_admin=bool(data.get("is_admin", False)),
                    preferences_completed=bool(data.get("preferences_completed", False)),
                )
            return True
        except Exception:
            logger.exception("Could not create profile for %s", data.get("username"))
            return False

    def by_username(self, username: str) -> dict[str, Any] | None:
        return self._serialize(UserProfile.objects.filter(username=username).first())

    def by_email(self, email: str) -> dict[str, Any] | None:
        return self._serialize(UserProfile.objects.filter(email=email).first())

    def update(self, username: str, changes: dict[str, Any]) -> bool:
        unknown = set(changes) - self._writable_fields
        if unknown:
            raise UnsupportedProfileFieldError(
                "Unsupported profile fields: " + ", ".join(sorted(unknown))
            )
        profile = UserProfile.objects.filter(username=username).first()
        if profile is None:
            return False
        for field, value in changes.items():
            setattr(profile, field, value)
        try:
            profile.save(update_fields=[*changes, "updated_at"])
            return True
        except Exception:
            logger.exception("Could not update profile %s", username)
            return False

    @staticmethod
    def _serialize(profile: UserProfile | None) -> dict[str, Any] | None:
        if profile is None:
            return None
        return {
            "user_id": profile.user_id,
            "username": profile.username,
            "password_hash": profile.password_hash,
            "email": profile.email,
            "role": profile.role,
            "age": profile.age,
            "academic_level": profile.academic_level,
            "language": profile.language,
            "is_admin": profile.is_admin,
            "preferences_completed": profile.preferences_completed,
            "created_at": profile.created_at,
            "updated_at": profile.updated_at,
        }


profile_repository = ProfileRepository()
