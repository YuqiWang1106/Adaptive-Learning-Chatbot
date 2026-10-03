from __future__ import annotations

from learning_apps.infrastructure.services.trace_context import bind_trace_context, normalize_trace_id, reset_trace_context


class RequestTraceMiddleware:
    """Attach a safe correlation id to each request and response."""

    response_header = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        trace_id = normalize_trace_id(request.headers.get(self.response_header, ""))
        tokens = bind_trace_context(trace_id=trace_id)
        request.trace_id = trace_id
        try:
            response = self.get_response(request)
            response[self.response_header] = trace_id
            return response
        finally:
            reset_trace_context(tokens)
