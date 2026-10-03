from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
import re
import uuid


_TRACE_ID = ContextVar("learning_demo_trace_id", default="")
_JOB_ID = ContextVar("learning_demo_job_id", default="")
_SAFE_TRACE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")


@dataclass(frozen=True)
class TraceContext:
    trace_id: str = ""
    job_id: str = ""


@dataclass(frozen=True)
class TraceTokens:
    trace_id: Token
    job_id: Token


def normalize_trace_id(value: object) -> str:
    candidate = str(value or "").strip()
    if _SAFE_TRACE_ID.fullmatch(candidate):
        return candidate
    return uuid.uuid4().hex


def current_trace_context() -> TraceContext:
    return TraceContext(trace_id=_TRACE_ID.get(), job_id=_JOB_ID.get())


def bind_trace_context(*, trace_id: object = "", job_id: object = "") -> TraceTokens:
    normalized_trace_id = normalize_trace_id(trace_id)
    return TraceTokens(
        trace_id=_TRACE_ID.set(normalized_trace_id),
        job_id=_JOB_ID.set(str(job_id or "").strip()),
    )


def reset_trace_context(tokens: TraceTokens) -> None:
    _JOB_ID.reset(tokens.job_id)
    _TRACE_ID.reset(tokens.trace_id)
