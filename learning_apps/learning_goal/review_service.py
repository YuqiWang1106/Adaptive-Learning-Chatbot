from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreferenceReviewResult:
    ok: bool
    code: str
    cleaned_text: str = ""


def review_preference_text(raw_preference_text: str) -> PreferenceReviewResult:
    """Handle review preference text."""
    cleaned_text = (raw_preference_text or "").strip()
    if not cleaned_text:
        return PreferenceReviewResult(ok=False, code="empty_preference", cleaned_text="")
    return PreferenceReviewResult(ok=True, code="ok", cleaned_text=cleaned_text)
