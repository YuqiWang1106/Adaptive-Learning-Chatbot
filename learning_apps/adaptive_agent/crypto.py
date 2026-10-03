from __future__ import annotations

import base64
import hashlib
import json
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured


def _key() -> bytes:
    configured = str(getattr(settings, "LEARNING_AGENT_CHECKPOINT_ENCRYPTION_KEY", "") or "").strip()
    if configured:
        try:
            raw = configured.encode("ascii")
            Fernet(raw)
            return raw
        except Exception as exc:
            raise ImproperlyConfigured("LEARNING_AGENT_CHECKPOINT_ENCRYPTION_KEY must be a Fernet key") from exc
    if not settings.DEBUG:
        raise ImproperlyConfigured("LEARNING_AGENT_CHECKPOINT_ENCRYPTION_KEY is required outside DEBUG")
    digest = hashlib.sha256(f"agent-v2\0{settings.SECRET_KEY}".encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def seal_json(payload: Any) -> tuple[str, str]:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return Fernet(_key()).encrypt(raw).decode("ascii"), hashlib.sha256(raw).hexdigest()


def unseal_json(token: str, expected_sha256: str) -> Any:
    try:
        raw = Fernet(_key()).decrypt(str(token).encode("ascii"))
    except (InvalidToken, ValueError) as exc:
        raise ValueError("checkpoint_decryption_failed") from exc
    if hashlib.sha256(raw).hexdigest() != str(expected_sha256):
        raise ValueError("checkpoint_integrity_failed")
    return json.loads(raw.decode("utf-8"))
