from __future__ import annotations

from typing import Any


REDACTED = "[REDACTED]"
SENSITIVE_KEYS = frozenset(
    {
        "api_key",
        "authorization",
        "db_password",
        "openai_api_key",
        "password",
        "raw_password",
        "secret",
        "secret_key",
        "token",
    }
)


def redact_sensitive_data(value: Any) -> Any:
    """Return a recursively redacted copy suitable for persisted reports."""
    if isinstance(value, dict):
        return {
            key: REDACTED if str(key).strip().lower() in SENSITIVE_KEYS else redact_sensitive_data(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_sensitive_data(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_sensitive_data(item) for item in value)
    return value


__all__ = ["REDACTED", "SENSITIVE_KEYS", "redact_sensitive_data"]
