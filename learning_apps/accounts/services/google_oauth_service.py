from __future__ import annotations

import re
import secrets
from urllib.parse import urlencode

import requests
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import make_password
from django.urls import reverse

from learning_apps.accounts.repository import profile_repository
from learning_apps.persistence.models import UserProfile

GOOGLE_AUTH_BASE_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def _google_client_config() -> tuple[str, str]:
    """Internal helper to handle google client config."""
    return (settings.GOOGLE_CLIENT_ID or "", settings.GOOGLE_CLIENT_SECRET or "")


def is_google_configured() -> bool:
    """Return whether google configured."""
    client_id, client_secret = _google_client_config()
    return bool(client_id and client_secret)


def _google_redirect_uri(request) -> str:
    """Internal helper to handle google redirect uri."""
    configured = (settings.GOOGLE_REDIRECT_URI or "").strip()
    if configured:
        return configured.rstrip("/")
    return request.build_absolute_uri(reverse("google_callback"))


def build_google_authorize_url(request) -> tuple[str, str]:
    """Return (state, authorize_url)."""
    client_id, _ = _google_client_config()
    state = secrets.token_urlsafe(32)
    params = {
        "client_id": client_id,
        "redirect_uri": _google_redirect_uri(request),
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "prompt": "select_account",
        "access_type": "online",
    }
    return state, f"{GOOGLE_AUTH_BASE_URL}?{urlencode(params)}"


def exchange_code_for_userinfo(request, code: str) -> dict:
    """Exchange code for userinfo."""
    client_id, client_secret = _google_client_config()
    redirect_uri = _google_redirect_uri(request)

    token_resp = requests.post(
        GOOGLE_TOKEN_URL,
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=15,
    )
    token_resp.raise_for_status()
    token_data = token_resp.json() or {}

    access_token = token_data.get("access_token")
    if not access_token:
        raise RuntimeError("Google token response missing access_token")

    userinfo_resp = requests.get(
        GOOGLE_USERINFO_URL,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=15,
    )
    userinfo_resp.raise_for_status()
    return userinfo_resp.json() or {}


def _make_unique_username(seed: str) -> str:
    """Internal helper to handle make unique username."""
    normalized = re.sub(r"[^\w]+", "_", (seed or "").strip().lower()).strip("_")
    if not normalized:
        normalized = "google_user"

    candidate = normalized
    counter = 0
    while profile_repository.by_username(candidate):
        counter += 1
        candidate = f"{normalized}_{counter}"
    return candidate


def ensure_profile_for_google_user(email: str) -> dict | None:
    """Ensure profile for google user."""
    user_profile = profile_repository.by_email(email)
    if user_profile:
        return user_profile

    username_seed = email.split("@", 1)[0]
    username = _make_unique_username(username_seed)
    new_user = {
        "username": username,
        "password_hash": make_password(None),
        "email": email,
        "role": UserProfile.ROLE_STUDENT,
        "age": 0,
        "academic_level": "",
        "language": "",
        "is_admin": False,
        "preferences_completed": False,
    }
    if not profile_repository.create(new_user):
        return None

    return profile_repository.by_username(username)


def sync_django_user(user_profile: dict):
    """Synchronize django user."""
    user_model = get_user_model()
    username = user_profile.get("username")
    defaults = {
        "email": user_profile.get("email", ""),
        "first_name": "",
        "last_name": "",
        "is_staff": bool(user_profile.get("is_admin", False)),
        "is_superuser": bool(user_profile.get("is_admin", False)),
    }

    django_user, created = user_model.objects.get_or_create(username=username, defaults=defaults)
    changed = False
    for field in ("email", "first_name", "last_name", "is_staff", "is_superuser"):
        if getattr(django_user, field) != defaults[field]:
            setattr(django_user, field, defaults[field])
            changed = True

    if created:
        django_user.set_unusable_password()
        changed = True

    if changed:
        django_user.save()

    return django_user
