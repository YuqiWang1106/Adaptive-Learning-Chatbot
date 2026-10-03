from __future__ import annotations

from markdown import Markdown
import nh3
from pymdownx.arithmatex import makeExtension


_CHAT_HTML_CLEANER = nh3.Cleaner(
    tags={
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "del",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "ul",
    },
    clean_content_tags={"embed", "form", "iframe", "math", "object", "script", "style", "svg", "template"},
    attributes={
        "a": {"href", "title"},
        "code": {"class"},
        "div": {"role"},
        "td": {"align"},
        "th": {"align"},
    },
    allowed_classes={
        "div": {"alert", "alert-warning", "arithmatex", "chat-answer-wrapper"},
        "span": {"arithmatex"},
    },
    url_schemes={"http", "https", "mailto"},
    url_relative="deny",
    link_rel="noopener noreferrer nofollow",
    strip_comments=True,
)


def sanitize_chat_html(value: object) -> str:
    """Return a safe HTML fragment for chat and learning feedback surfaces."""
    return _CHAT_HTML_CLEANER.clean(str(value or ""))


def markdown_to_safe_chat_html(value: object) -> str:
    """Render Markdown and sanitize the generated HTML before it reaches a browser."""
    markdown = Markdown(extensions=["extra", makeExtension(generic=True)])
    return sanitize_chat_html(markdown.convert(str(value or "")))
