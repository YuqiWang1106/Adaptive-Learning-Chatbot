from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from django.conf import settings
from django.core.cache import cache


@dataclass(frozen=True)
class LoginRateLimitState:
    is_limited: bool
    retry_after_seconds: int = 0
    attempts_remaining: int = 0


def _client_ip(request) -> str:
    """Return a stable client identifier for login throttling."""
    forwarded_for = request.META.get("HTTP_X_FORWARDED_FOR", "")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.META.get("REMOTE_ADDR", "") or "unknown"


def _normalized_identifier(identifier: str) -> str:
    """Normalize username or email for login throttling."""
    return (identifier or "").strip().lower()


def _cache_key(request, identifier: str) -> str:
    """Build a cache key without storing raw usernames or IP addresses in cache keys."""
    raw = f"{_client_ip(request)}|{_normalized_identifier(identifier)}"
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    return f"auth-login-rate:{digest}"


def _limits() -> tuple[int, int, int]:
    """Return configured rate-limit settings."""
    attempts = max(int(getattr(settings, "LOGIN_RATE_LIMIT_ATTEMPTS", 5)), 1)
    window_seconds = max(int(getattr(settings, "LOGIN_RATE_LIMIT_WINDOW_SECONDS", 900)), 1)
    lockout_seconds = max(int(getattr(settings, "LOGIN_RATE_LIMIT_LOCKOUT_SECONDS", 900)), 1)
    return attempts, window_seconds, lockout_seconds


def get_login_rate_limit_state(request, identifier: str) -> LoginRateLimitState:
    """Return whether the current IP + identifier pair is locked out."""
    max_attempts, _window_seconds, _lockout_seconds = _limits()
    payload = cache.get(_cache_key(request, identifier)) or {}
    locked_until = float(payload.get("locked_until") or 0)
    now = time.time()

    if locked_until > now:
        return LoginRateLimitState(
            is_limited=True,
            retry_after_seconds=max(1, int(locked_until - now)),
            attempts_remaining=0,
        )

    failures = int(payload.get("failures") or 0)
    return LoginRateLimitState(
        is_limited=False,
        retry_after_seconds=0,
        attempts_remaining=max(max_attempts - failures, 0),
    )


def record_login_failure(request, identifier: str) -> LoginRateLimitState:
    """Record a failed login attempt and return the updated throttle state."""
    max_attempts, window_seconds, lockout_seconds = _limits()
    key = _cache_key(request, identifier)
    payload = cache.get(key) or {}
    failures = int(payload.get("failures") or 0) + 1

    locked_until = 0
    if failures >= max_attempts:
        locked_until = time.time() + lockout_seconds

    cache.set(
        key,
        {"failures": failures, "locked_until": locked_until},
        timeout=max(window_seconds, lockout_seconds) + 60,
    )
    return get_login_rate_limit_state(request, identifier)


def clear_login_failures(request, identifier: str) -> None:
    """Clear failed login state after a successful login."""
    cache.delete(_cache_key(request, identifier))
