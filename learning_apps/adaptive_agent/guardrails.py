from __future__ import annotations

import re
import unicodedata


_CONTROL_PATTERNS = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"\bignore\s+(?:all\s+)?(?:previous|prior|above)\s+(?:instructions?|rules?|messages?)\b",
        r"\b(?:reveal|print|show|repeat|leak)\s+(?:the\s+)?(?:system|developer)\s+(?:prompt|message|instructions?)\b",
        r"\b(?:pretend|act)\s+(?:that\s+)?(?:you\s+are|as)\s+(?:the\s+)?(?:system|developer|administrator)\b",
        r"\b(?:bypass|override|disable)\s+(?:the\s+)?(?:approval|permission|policy|guardrail|safety)\b",
        r"\b(?:mastery\.update|mastery_evidence\.admit|concept\.verify|quiz\.commit|review\.schedule)\b",
        r"\b(?:call|invoke|execute)\s+(?:a\s+)?(?:tool|function|mcp)\b.{0,100}\b(?:user_id|goal_id|learning_goal_id|conversation_key)\b",
        r"<\/?(?:system|developer|tool|assistant)(?:\s|>)",
    )
)


def normalize_control_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or ""))
    normalized = "".join(char for char in normalized if unicodedata.category(char) not in {"Cf", "Cc"} or char in "\t\n\r")
    return " ".join(normalized.split())


def contains_agent_control_injection(value: str) -> bool:
    normalized = normalize_control_text(value)
    return any(pattern.search(normalized) for pattern in _CONTROL_PATTERNS)
