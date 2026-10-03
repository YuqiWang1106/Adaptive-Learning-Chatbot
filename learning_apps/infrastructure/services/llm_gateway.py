from __future__ import annotations

import json
import logging
import random
import threading
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

import requests
from django.conf import settings
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI, RateLimitError

from learning_apps.infrastructure.services.sensitive_data_service import redact_sensitive_data
from learning_apps.infrastructure.services.trace_context import current_trace_context

logger = logging.getLogger(__name__)


STATUS_OK = "ok"
STATUS_RETRYABLE = "retryable"
STATUS_RATE_LIMITED = "rate_limited"
STATUS_TIMEOUT = "timeout"
STATUS_PROVIDER_ERROR = "provider_error"


class LLMGatewayError(RuntimeError):
    def __init__(self, result: "LLMResult"):
        super().__init__(result.error_message or result.status)
        self.result = result


@dataclass(frozen=True)
class LLMResult:
    ok: bool
    status: str
    route: str
    model: str = ""
    content: str = ""
    raw_response: Any = None
    raw_json: Optional[Dict[str, Any]] = None
    error_code: str = ""
    error_message: str = ""
    latency_ms: int = 0
    attempts: int = 1


class _RouteLimiter:
    def __init__(self) -> None:
        self._global = threading.BoundedSemaphore(max(1, int(getattr(settings, "LEARNING_LLM_MAX_CONCURRENCY", 32))))
        self._lock = threading.Lock()
        self._route_limits: Dict[str, threading.BoundedSemaphore] = {}

    def _route_limit(self, route: str):
        route_key = route or "default"
        with self._lock:
            route_limit = self._route_limits.get(route_key)
            if route_limit is None:
                configured = getattr(settings, "LEARNING_LLM_ROUTE_LIMITS", {}) or {}
                limit = int(configured.get(route_key) or configured.get(route_key.split(".")[0]) or 8)
                route_limit = threading.BoundedSemaphore(max(1, limit))
                self._route_limits[route_key] = route_limit
        return route_limit

    def acquire(self, route: str):
        route_limit = self._route_limit(route)
        self._global.acquire()
        route_limit.acquire()
        return route_limit

    def acquire_route(self, route: str):
        route_limit = self._route_limit(route)
        route_limit.acquire()
        return route_limit

    def release(self, route_limit) -> None:
        try:
            route_limit.release()
        finally:
            self._global.release()

    @staticmethod
    def release_route(route_limit) -> None:
        route_limit.release()


class LLMGateway:
    def __init__(self) -> None:
        self._client_lock = threading.Lock()
        self._client: OpenAI | None = None
        self._client_api_key = ""
        self._session = requests.Session()
        self._limiter = _RouteLimiter()

    def _openai_client(self) -> OpenAI:
        api_key = str(settings.OPENAI_API_KEY or "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not configured.")
        with self._client_lock:
            if self._client is None or self._client_api_key != api_key:
                self._client = OpenAI(api_key=api_key)
                self._client_api_key = api_key
            return self._client

    @staticmethod
    def _http_provider_config() -> tuple[str, str, dict[str, str]]:
        api_key = str(settings.OPENAI_API_KEY or "")
        base_url = str(settings.OPENAI_API_BASE or "https://api.openai.com/v1").rstrip("/")
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        return api_key, base_url, headers

    @contextmanager
    def route_slot(self, route: str, *, include_global: bool = True):
        """Reserve a configured provider slot around an externally managed call.

        The Agents SDK owns its OpenAI request loop, so it cannot use this
        gateway's request methods.  It can still share the same route limit.
        `include_global=False` avoids holding the global request semaphore
        while the agent executes business tools that may themselves use this
        gateway (and would otherwise deadlock at a global limit of one).
        """

        if include_global:
            route_limit = self._limiter.acquire(route)
            release = self._limiter.release
        else:
            route_limit = self._limiter.acquire_route(route)
            release = self._limiter.release_route
        try:
            yield
        finally:
            release(route_limit)

    def chat_completion(self, *, route: str, model: str, job_id: str = "", metadata: Optional[Dict[str, Any]] = None, **kwargs) -> LLMResult:
        timeout = kwargs.pop("timeout", None) or getattr(settings, "LEARNING_LLM_DEFAULT_TIMEOUT_SECONDS", 45)
        max_attempts = kwargs.pop("max_attempts", None)

        def _call():
            # Retry policy belongs to this gateway.  The OpenAI SDK also
            # retries timeouts/5xx by default, which silently multiplies a
            # route's latency and defeated the identity route's explicit
            # single-attempt fail-closed contract under load.
            client = self._openai_client().with_options(max_retries=0)
            return client.chat.completions.create(model=model, timeout=timeout, **kwargs)

        return self._run_with_policy(
            route=route,
            model=model,
            job_id=job_id,
            metadata=metadata,
            call=_call,
            extract_content=lambda response: response.choices[0].message.content or "",
            max_attempts=max_attempts,
        )

    def chat_completion_or_raise(self, **kwargs):
        result = self.chat_completion(**kwargs)
        if not result.ok:
            raise LLMGatewayError(result)
        return result.raw_response

    def embeddings(
        self,
        *,
        route: str,
        model: str,
        input_texts: list[str],
        dimensions: int | None = None,
        job_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> LLMResult:
        timeout = timeout or getattr(settings, "LEARNING_LLM_DEFAULT_TIMEOUT_SECONDS", 45)
        cleaned = [str(text or "").strip() for text in input_texts]
        if not cleaned or any(not text for text in cleaned):
            raise ValueError("Embedding input must contain non-empty strings.")

        def _call():
            payload: Dict[str, Any] = {
                "model": model,
                "input": cleaned,
                "encoding_format": "float",
                "timeout": timeout,
            }
            if dimensions:
                payload["dimensions"] = max(1, int(dimensions))
            client = self._openai_client().with_options(max_retries=0)
            return client.embeddings.create(**payload)

        return self._run_with_policy(
            route=route,
            model=model,
            job_id=job_id,
            metadata={**(metadata or {}), "input_count": len(cleaned), "dimensions": dimensions or 0},
            call=_call,
            extract_content=lambda _response: "",
        )

    def embeddings_or_raise(self, **kwargs) -> list[list[float]]:
        result = self.embeddings(**kwargs)
        if not result.ok:
            raise LLMGatewayError(result)
        data = list(getattr(result.raw_response, "data", []) or [])
        data.sort(key=lambda item: int(getattr(item, "index", 0) or 0))
        vectors = [list(getattr(item, "embedding", []) or []) for item in data]
        expected = len(kwargs.get("input_texts") or [])
        if len(vectors) != expected or any(not vector for vector in vectors):
            raise RuntimeError("Embedding provider returned an incomplete batch.")
        return vectors

    def responses_create(
        self,
        *,
        route: str,
        payload: Dict[str, Any],
        job_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
        max_attempts: Optional[int] = None,
    ) -> LLMResult:
        model = str(payload.get("model") or "")
        timeout = timeout or getattr(settings, "LEARNING_LLM_DEFAULT_TIMEOUT_SECONDS", 45)

        def _call():
            api_key, base_url, headers = self._http_provider_config()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not configured.")
            resp = self._session.post(
                f"{base_url}/responses",
                headers=headers,
                json=payload,
                timeout=timeout,
            )
            resp.raise_for_status()
            return resp.json()

        return self._run_with_policy(
            route=route,
            model=model,
            job_id=job_id,
            metadata=metadata,
            call=_call,
            extract_content=self._extract_response_text,
            max_attempts=max_attempts,
        )

    def responses_create_or_raise(self, **kwargs) -> Dict[str, Any]:
        result = self.responses_create(**kwargs)
        if not result.ok:
            raise LLMGatewayError(result)
        return result.raw_json or {}

    def responses_create_stream(
        self,
        *,
        route: str,
        payload: Dict[str, Any],
        on_text_delta: Optional[Callable[[str], None]] = None,
        job_id: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        timeout: Optional[int] = None,
    ) -> LLMResult:
        model = str(payload.get("model") or "")
        timeout = timeout or getattr(settings, "LEARNING_LLM_DEFAULT_TIMEOUT_SECONDS", 45)

        def _call():
            api_key, base_url, headers = self._http_provider_config()
            if not api_key:
                raise RuntimeError("OPENAI_API_KEY is not configured.")

            stream_payload = dict(payload)
            stream_payload["stream"] = True
            response = self._session.post(
                f"{base_url}/responses",
                headers=headers,
                json=stream_payload,
                timeout=timeout,
                stream=True,
            )
            response.raise_for_status()

            final_response: Dict[str, Any] = {}
            text_parts: list[str] = []
            try:
                for raw_line in response.iter_lines(decode_unicode=True):
                    if raw_line is None:
                        continue
                    line = str(raw_line).strip()
                    if not line or line.startswith("event:"):
                        continue
                    if not line.startswith("data:"):
                        continue

                    data = line[5:].strip()
                    if not data:
                        continue
                    if data == "[DONE]":
                        break

                    try:
                        event = json.loads(data)
                    except json.JSONDecodeError:
                        logger.debug("Skipping non-JSON stream chunk for route=%s: %s", route, data[:160])
                        continue

                    event_type = str(event.get("type") or "")
                    if event_type == "response.output_text.delta":
                        delta = str(event.get("delta") or "")
                        if delta:
                            text_parts.append(delta)
                            if on_text_delta:
                                on_text_delta(delta)
                    elif event_type == "response.completed":
                        candidate = event.get("response")
                        if isinstance(candidate, dict):
                            final_response = candidate
                    elif event_type == "response.error":
                        err = event.get("error") or {}
                        message = ""
                        if isinstance(err, dict):
                            message = str(err.get("message") or err.get("code") or "stream_error")
                        else:
                            message = str(err)
                        raise RuntimeError(f"Streaming response error: {message}")
            finally:
                response.close()

            joined_text = "".join(text_parts)
            if not final_response:
                final_response = {"output_text": joined_text}
            elif joined_text and not final_response.get("output_text"):
                final_response["output_text"] = joined_text
            return final_response

        return self._run_with_policy(
            route=route,
            model=model,
            job_id=job_id,
            metadata=metadata,
            call=_call,
            extract_content=self._extract_response_text,
        )

    def responses_create_stream_or_raise(self, **kwargs) -> Dict[str, Any]:
        result = self.responses_create_stream(**kwargs)
        if not result.ok:
            raise LLMGatewayError(result)
        return result.raw_json or {}

    def _run_with_policy(
        self,
        *,
        route: str,
        model: str,
        job_id: str,
        metadata: Optional[Dict[str, Any]],
        call,
        extract_content,
        max_attempts: Optional[int] = None,
    ) -> LLMResult:
        route_limit = self._limiter.acquire(route)
        attempts = max(
            1,
            int(
                max_attempts
                if max_attempts is not None
                else getattr(settings, "LEARNING_LLM_MAX_RETRIES", 3)
            ),
        )
        backoff = float(getattr(settings, "LEARNING_LLM_BACKOFF_BASE_SECONDS", 0.75))
        last_result: Optional[LLMResult] = None
        started_total = time.monotonic()
        try:
            for attempt in range(1, attempts + 1):
                started = time.monotonic()
                try:
                    raw = call()
                    latency_ms = int((time.monotonic() - started) * 1000)
                    content = extract_content(raw)
                    result = LLMResult(
                        ok=True,
                        status=STATUS_OK,
                        route=route,
                        model=model,
                        content=content,
                        raw_response=raw,
                        raw_json=raw if isinstance(raw, dict) else None,
                        latency_ms=latency_ms,
                        attempts=attempt,
                    )
                    self._log_request(result, job_id=job_id, metadata=metadata)
                    return result
                except Exception as exc:
                    status, code, message = self._classify_error(exc)
                    latency_ms = int((time.monotonic() - started) * 1000)
                    last_result = LLMResult(
                        ok=False,
                        status=status,
                        route=route,
                        model=model,
                        error_code=code,
                        error_message=message,
                        latency_ms=latency_ms,
                        attempts=attempt,
                    )
                    self._log_request(last_result, job_id=job_id, metadata=metadata)
                    if attempt >= attempts or status not in {STATUS_RATE_LIMITED, STATUS_TIMEOUT, STATUS_RETRYABLE}:
                        return last_result
                    time.sleep(backoff * (2 ** (attempt - 1)) + random.uniform(0, 0.25))
        finally:
            self._limiter.release(route_limit)

        return last_result or LLMResult(
            ok=False,
            status=STATUS_PROVIDER_ERROR,
            route=route,
            model=model,
            error_code="unknown",
            error_message="LLM call failed without a captured error.",
            latency_ms=int((time.monotonic() - started_total) * 1000),
        )

    @staticmethod
    def _extract_response_text(body: Any) -> str:
        if not isinstance(body, dict):
            return ""
        texts = []
        for item in body.get("output", []) or []:
            for part in item.get("content") or []:
                if part.get("type") in {"output_text", "text"}:
                    texts.append(part.get("text", ""))
        return "\n".join(text for text in texts if text).strip() or str(body.get("output_text") or "")

    @staticmethod
    def _classify_error(exc: Exception) -> tuple[str, str, str]:
        if isinstance(exc, RateLimitError):
            return STATUS_RATE_LIMITED, "rate_limited", str(exc)
        if isinstance(exc, APITimeoutError) or isinstance(exc, requests.Timeout):
            return STATUS_TIMEOUT, "timeout", str(exc)
        if isinstance(exc, APIConnectionError) or isinstance(exc, requests.ConnectionError):
            return STATUS_RETRYABLE, "connection_error", str(exc)
        if isinstance(exc, APIStatusError):
            code = getattr(exc, "status_code", None)
            message = LLMGateway._http_error_message(exc)
            if code == 429:
                return STATUS_RATE_LIMITED, "rate_limited", message
            if code and int(code) >= 500:
                return STATUS_RETRYABLE, f"http_{code}", message
            return STATUS_PROVIDER_ERROR, f"http_{code or 'error'}", message
        if isinstance(exc, requests.HTTPError):
            response = getattr(exc, "response", None)
            code = getattr(response, "status_code", None)
            message = LLMGateway._http_error_message(exc)
            if code == 429:
                return STATUS_RATE_LIMITED, "rate_limited", message
            if code and int(code) >= 500:
                return STATUS_RETRYABLE, f"http_{code}", message
            return STATUS_PROVIDER_ERROR, f"http_{code or 'error'}", message
        return STATUS_PROVIDER_ERROR, exc.__class__.__name__, str(exc)

    @staticmethod
    def _http_error_message(exc: Exception) -> str:
        response = getattr(exc, "response", None)
        if response is None:
            return str(exc)

        detail = ""
        try:
            body = response.json()
            error = body.get("error") if isinstance(body, dict) else None
            if isinstance(error, dict):
                message = error.get("message") or error.get("code") or ""
                detail = str(message or body)
            else:
                detail = json.dumps(body, ensure_ascii=False)
        except Exception:
            detail = str(getattr(response, "text", "") or "")

        status_code = getattr(response, "status_code", "")
        reason = getattr(response, "reason", "")
        prefix = f"{status_code} {reason}".strip()
        if detail:
            return f"{prefix}: {detail}"[:4000]
        return str(exc)

    @staticmethod
    def _usage_value(raw: Any, key: str) -> int:
        usage = getattr(raw, "usage", None)
        if usage is None and isinstance(raw, dict):
            usage = raw.get("usage") or {}
        if usage is None:
            return 0
        value = getattr(usage, key, None) if not isinstance(usage, dict) else usage.get(key)
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    def _log_request(self, result: LLMResult, *, job_id: str, metadata: Optional[Dict[str, Any]]) -> None:
        try:
            from learning_apps.persistence.models import LLMRequestLog

            raw = result.raw_response if result.raw_response is not None else result.raw_json
            trace = current_trace_context()
            effective_job_id = job_id or trace.job_id
            safe_metadata = redact_sensitive_data(
                {
                    **(metadata or {}),
                    "trace_id": trace.trace_id,
                    "job_id": effective_job_id,
                }
            )
            LLMRequestLog.objects.create(
                route=result.route,
                provider="openai",
                model=result.model or "",
                job_id=effective_job_id,
                status=result.status,
                error_code=result.error_code or "",
                error_message=(result.error_message or "")[:4000],
                latency_ms=max(0, result.latency_ms),
                prompt_tokens=self._usage_value(raw, "prompt_tokens") or self._usage_value(raw, "input_tokens"),
                completion_tokens=self._usage_value(raw, "completion_tokens") or self._usage_value(raw, "output_tokens"),
                total_tokens=self._usage_value(raw, "total_tokens"),
                metadata=safe_metadata,
            )
        except Exception as exc:
            logger.debug("LLM request log write failed: %s", exc)


llm_gateway = LLMGateway()
