from __future__ import annotations

import re


def fix_latex_delimiters(text: str) -> str:
    """Normalize latex delimiters."""
    if not text:
        return text
    text = re.sub(r"\[([^\[\]]*)\]_([a-zA-Z0-9π]+)\^([a-zA-Z0-9π]+)", r"\\left[\1\\right]_{\2}^{\3}", text)
    text = re.sub(r"\[([^\[\]]*)\]_([a-zA-Z0-9π]+)", r"\\left[\1\\right]_{\2}", text)
    return text


def fix_math_expressions(text: str) -> str:
    """Normalize math expressions."""
    if not text:
        return text

    text = re.sub(r"\[\s*\n([^[\]]+(?:\n[^[\]]*)*?)\n\s*\]", r"$$\n\1\n$$", text)
    text = re.sub(r"\[\s*([^[\]]*\\[a-zA-Z]+[^[\]]*)\s*\]", r"$$\1$$", text)
    text = fix_latex_delimiters(text)
    text = re.sub(r"(?<!\n)\$\$(?!\$)", r"\n$$", text)
    text = re.sub(r"(?<!\$)\$\$(?!\n)", r"$$\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def detect_answer_style(answer_text: str) -> str:
    """Detect answer style."""
    answer_text = (answer_text or "").lower()
    patterns = {
        "real_world": ["real world style", "real-world style"],
        "informational": ["informational response", "informational answer", "informational style"],
        "cause_and_effect": ["cause and effect", "cause-and-effect style"],
        "goal_based": ["goal based", "goal-based style"],
    }
    for style, phrases in patterns.items():
        for phrase in phrases:
            if phrase in answer_text:
                return style

    simple_patterns = {
        "real_world": ["real world", "real-world"],
        "informational": ["informational", "information"],
        "cause_and_effect": ["cause", "effect"],
        "goal_based": ["goal"],
    }
    for style, phrases in simple_patterns.items():
        for phrase in phrases:
            if phrase in answer_text[:500]:
                return style

    return "informational"


def strip_html(text: str) -> str:
    """Handle strip html."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", "", text)


def truncate_for_prompt(text: str, limit: int = 1200) -> str:
    """Truncate for prompt."""
    cleaned = strip_html(text or "")
    return cleaned if len(cleaned) <= limit else cleaned[:limit] + "..."
