from __future__ import annotations

import json

from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from learning_apps.application.contracts import CapabilityError
from .mcp import MCP_SERVER_NAME, authenticate_mcp_token, process_mcp_request


@csrf_exempt
@require_POST
def adaptive_learning_mcp(request):
    authorization = str(request.headers.get("Authorization") or "")
    if not authorization.startswith("Bearer "):
        return JsonResponse({"error": "bearer_token_required"}, status=401)
    try:
        grant = authenticate_mcp_token(authorization[7:].strip(), audience=MCP_SERVER_NAME)
    except CapabilityError:
        return JsonResponse({"error": "invalid_or_expired_token"}, status=401)
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return JsonResponse({"error": "invalid_json"}, status=400)
    if not isinstance(payload, dict):
        return JsonResponse({"error": "invalid_jsonrpc_request"}, status=400)
    result = process_mcp_request(grant, payload)
    if "text/event-stream" in str(request.headers.get("Accept") or ""):
        data = json.dumps(result, ensure_ascii=False, separators=(",", ":"))
        response = StreamingHttpResponse(iter([f"event: message\ndata: {data}\n\n"]), content_type="text/event-stream")
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response
    return JsonResponse(result)
