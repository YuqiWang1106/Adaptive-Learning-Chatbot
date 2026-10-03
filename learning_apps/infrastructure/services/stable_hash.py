"""Deterministic hashing shared by application and product services."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def stable_sha256(value: Any) -> str:
    encoded = json.dumps(
        value,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


__all__ = ["stable_sha256"]
