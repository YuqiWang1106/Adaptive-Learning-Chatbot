import logging
import hashlib
from typing import Dict, Optional

import requests
from django.conf import settings


def _provider_config() -> tuple[str, str, dict[str, str]]:
    api_key = str(settings.OPENAI_API_KEY or "")
    base_url = str(settings.OPENAI_API_BASE or "https://api.openai.com/v1").rstrip("/")
    return api_key, base_url, {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

def is_remote_conversation_id(conversation_id: Optional[str]) -> bool:
    """Return whether a conversation id looks like an OpenAI-hosted conversation."""
    return bool(conversation_id and str(conversation_id).startswith("conv_"))


def _provider_ref(value: Optional[str]) -> str:
    return hashlib.sha256(str(value or "").encode()).hexdigest()[:12]


def create_remote_conversation(metadata: Dict[str, str]) -> Optional[str]:
    """Create remote conversation."""
    api_key, base_url, headers = _provider_config()
    if not api_key:
        return None
    try:
        resp = requests.post(
            f"{base_url}/conversations",
            headers=headers,
            json={"metadata": metadata or {}},
            timeout=10,
        )
        if resp.ok:
            payload = resp.json() or {}
            conversation_id = payload.get("id")
            if is_remote_conversation_id(conversation_id):
                return conversation_id
            logging.warning("Conversation create returned invalid id (ref=%s)", _provider_ref(conversation_id))
            return None
        logging.warning("Conversation create failed (status=%s)", resp.status_code)
    except Exception as exc:  # pragma: no cover - network
        logging.warning("Conversation create exception: %s", exc.__class__.__name__)
    return None


def delete_remote_conversation(conversation_id: str) -> int:
    """Delete an optional provider mirror and return its HTTP status.

    Callers own retry policy. A missing key/invalid id is reported as zero so
    it cannot accidentally be treated as successful cleanup.
    """

    api_key, base_url, headers = _provider_config()
    if not api_key or not is_remote_conversation_id(conversation_id):
        return 0
    try:
        response = requests.delete(
            f"{base_url}/conversations/{conversation_id}",
            headers=headers,
            timeout=10,
        )
        return int(response.status_code)
    except Exception as exc:  # pragma: no cover - network
        logging.warning("Conversation delete exception: %s", exc.__class__.__name__)
        return 0
