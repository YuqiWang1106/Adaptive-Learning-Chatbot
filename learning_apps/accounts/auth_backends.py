"""Authentication backend for the product profile repository."""
from __future__ import annotations

import logging
from typing import Optional

from django.contrib.auth import get_user_model
from django.contrib.auth.backends import BaseBackend
from django.contrib.auth.hashers import check_password, make_password
from werkzeug.security import check_password_hash as check_password_hash_werkzeug

from .repository import profile_repository

logger = logging.getLogger(__name__)


class UserProfileBackend(BaseBackend):
    """Authenticate against the product profile repository."""

    def authenticate(self, request, username: Optional[str] = None, password: Optional[str] = None, **kwargs):
        """Handle authenticate."""
        if not password:
            return None

        identifier = username or kwargs.get("email") or kwargs.get("username")
        if not identifier:
            return None

        user_data = profile_repository.by_username(identifier)
        if not user_data and "@" in identifier:
            user_data = profile_repository.by_email(identifier)

        if not user_data:
            return None

        stored_hash = user_data.get("password_hash") or ""
        if not stored_hash:
            return None

        password_valid = check_password(password, stored_hash)
        used_legacy_hash = False
        if not password_valid:
            try:
                password_valid = check_password_hash_werkzeug(stored_hash, password)
                used_legacy_hash = password_valid
            except ValueError:
                password_valid = False

        if not password_valid:
            return None

        # Gradually migrate any legacy werkzeug hashes to Django-native hashes.
        if used_legacy_hash:
            profile_repository.update(user_data.get("username"), {"password_hash": make_password(password)})

        return self._get_or_sync_django_user(user_data)

    def _get_or_sync_django_user(self, user_data):
        """Internal helper to return or sync django user."""
        UserModel = get_user_model()
        username = user_data.get("username") or user_data.get("email")
        if not username:
            logger.warning("User data missing username/email; cannot sync Django auth user.")
            return None

        defaults = {
            "email": user_data.get("email", ""),
            "first_name": "",
            "last_name": "",
            "is_staff": bool(user_data.get("is_admin", False)),
            "is_superuser": bool(user_data.get("is_admin", False)),
        }

        django_user, created = UserModel.objects.get_or_create(username=username, defaults=defaults)

        updated = False
        if django_user.email != defaults["email"]:
            django_user.email = defaults["email"]
            updated = True

        if defaults["first_name"] and django_user.first_name != defaults["first_name"]:
            django_user.first_name = defaults["first_name"]
            updated = True

        if defaults["last_name"] and django_user.last_name != defaults["last_name"]:
            django_user.last_name = defaults["last_name"]
            updated = True

        if django_user.is_staff != defaults["is_staff"]:
            django_user.is_staff = defaults["is_staff"]
            updated = True

        if django_user.is_superuser != defaults["is_superuser"]:
            django_user.is_superuser = defaults["is_superuser"]
            updated = True

        if created:
            django_user.set_unusable_password()
            updated = True

        if updated:
            django_user.save(update_fields=[
                "email",
                "first_name",
                "last_name",
                "is_staff",
                "is_superuser",
                "password",
            ])

        return django_user

    def get_user(self, user_id):
        """Return user."""
        UserModel = get_user_model()
        try:
            return UserModel.objects.get(pk=user_id)
        except UserModel.DoesNotExist:
            return None
