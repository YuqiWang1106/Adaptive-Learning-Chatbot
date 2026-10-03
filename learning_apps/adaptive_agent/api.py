from __future__ import annotations

import json
import time

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse, StreamingHttpResponse
from django.views.decorators.http import require_GET, require_POST

from learning_apps.application.workflows.conversation import resolve_chat_access

from .run_service import (
    AgentRunConflict,
    AgentRunScopeError,
    answer_agent_clarification,
    cancel_agent_run,
    create_agent_run,
    decide_agent_approval,
    get_agent_event_batch,
    get_agent_run,
    serialize_run,
)
from learning_apps.application.contracts import CapabilityError
from .micro_checks import (
    active_micro_check,
    answer_micro_check,
    serialize_micro_check,
    skip_micro_check,
)
from learning_apps.adaptive_learning.probe_offer_service import (
    ProbeOfferError,
    accept_probe_offer,
    active_probe_offer,
    dismiss_probe_offer,
    serialize_probe_offer,
    snooze_probe_offer,
)


def _json_body(request) -> dict:
    try:
        payload = json.loads(request.body.decode("utf-8") or "{}")
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _error(code: str, status: int) -> JsonResponse:
    return JsonResponse({"error": code}, status=status)


@login_required
@require_POST
def create_run(request, goal_id: int):
    if not settings.LEARNING_AGENT_V2_ENABLED:
        return _error("agent_v2_disabled", 503)
    access = resolve_chat_access(request.user.username, goal_id)
    if not access.ok:
        if access.code == "goal_not_found":
            return _error("learning_goal_not_found", 404)
        if access.code == "assessment_not_completed":
            return _error("self_assessment_required", 409)
        return _error("chat_not_available", 409)
    payload = _json_body(request)
    try:
        run, created = create_agent_run(
            username=request.user.username,
            learning_goal_id=goal_id,
            question=str(payload.get("question") or ""),
            idempotency_key=str(request.headers.get("Idempotency-Key") or payload.get("idempotency_key") or ""),
        )
    except AgentRunScopeError:
        return _error("learning_goal_not_found", 404)
    except ValueError as exc:
        return _error(str(exc), 400)
    return JsonResponse(serialize_run(run), status=202 if created else 200)


@login_required
@require_GET
def get_run(request, run_id: str):
    try:
        run = get_agent_run(request.user.username, run_id)
    except AgentRunScopeError:
        return _error("agent_run_not_found", 404)
    return JsonResponse(serialize_run(run, include_events=request.GET.get("include_events") == "1"))


@login_required
@require_GET
def run_events(request, run_id: str):
    after = request.GET.get("after") or request.headers.get("Last-Event-ID") or "0"
    try:
        after_sequence = max(0, int(after))
    except (TypeError, ValueError):
        after_sequence = 0
    if request.GET.get("format") == "json":
        try:
            batch = get_agent_event_batch(request.user.username, run_id, after_sequence=after_sequence)
        except AgentRunScopeError:
            return _error("agent_run_not_found", 404)
        return JsonResponse({"events": list(batch.events), "status": batch.status})

    username = request.user.username
    try:
        get_agent_event_batch(username, run_id, after_sequence=after_sequence, limit=1)
    except AgentRunScopeError:
        return _error("agent_run_not_found", 404)

    def stream():
        cursor = after_sequence
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            try:
                batch = get_agent_event_batch(username, run_id, after_sequence=cursor, limit=100)
            except AgentRunScopeError:
                yield "event: run_failed\ndata: {\"error\":\"agent_run_not_found\"}\n\n"
                return
            for item in batch.events:
                cursor = item["sequence"]
                data = json.dumps(item["payload"], ensure_ascii=False, separators=(",", ":"))
                yield f"id: {item['sequence']}\nevent: {item['type']}\ndata: {data}\n\n"
            if batch.should_end:
                yield f"event: stream_end\ndata: {{\"status\":\"{batch.status}\"}}\n\n"
                return
            if not batch.events:
                yield ": keepalive\n\n"
            time.sleep(0.35)

    response = StreamingHttpResponse(stream(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache, no-transform"
    response["X-Accel-Buffering"] = "no"
    return response


@login_required
@require_POST
def answer_clarification(request, run_id: str, interruption_id: str):
    payload = _json_body(request)
    try:
        run = answer_agent_clarification(
            username=request.user.username,
            run_id=run_id,
            interruption_id=interruption_id,
            answer=str(payload.get("answer") or ""),
        )
    except AgentRunScopeError:
        return _error("interruption_not_found", 404)
    except AgentRunConflict as exc:
        return _error(str(exc), 409)
    except ValueError as exc:
        return _error(str(exc), 400)
    return JsonResponse(serialize_run(run), status=202)


@login_required
@require_POST
def resolve_approval(request, run_id: str, interruption_id: str):
    payload = _json_body(request)
    if not isinstance(payload.get("approved"), bool):
        return _error("approved_boolean_required", 400)
    try:
        run = decide_agent_approval(
            username=request.user.username,
            run_id=run_id,
            interruption_id=interruption_id,
            approved=payload["approved"],
        )
    except AgentRunScopeError:
        return _error("interruption_not_found", 404)
    except AgentRunConflict as exc:
        return _error(str(exc), 409)
    return JsonResponse(serialize_run(run), status=202)


@login_required
@require_POST
def cancel_run(request, run_id: str):
    try:
        run = cancel_agent_run(username=request.user.username, run_id=run_id)
    except AgentRunScopeError:
        return _error("agent_run_not_found", 404)
    return JsonResponse(serialize_run(run))


@login_required
@require_GET
def get_active_micro_check(request, goal_id: int):
    check = active_micro_check(request.user.username, goal_id)
    return JsonResponse({"micro_check": serialize_micro_check(check) if check else None})


@login_required
@require_POST
def answer_learning_check(request, check_id: str):
    payload = _json_body(request)
    try:
        check, run = answer_micro_check(
            username=request.user.username,
            check_id=check_id,
            answer=str(payload.get("answer") or ""),
            idempotency_key=str(request.headers.get("Idempotency-Key") or payload.get("idempotency_key") or ""),
        )
    except CapabilityError as exc:
        status = 404 if exc.code == "micro_check_not_found" else 409
        return _error(exc.code, status)
    except ValueError as exc:
        return _error(str(exc), 400)
    return JsonResponse(
        {"micro_check": serialize_micro_check(check), "run": serialize_run(run)},
        status=202,
    )


@login_required
@require_POST
def skip_learning_check(request, check_id: str):
    try:
        check = skip_micro_check(username=request.user.username, check_id=check_id)
    except CapabilityError as exc:
        return _error(exc.code, 404 if exc.code == "micro_check_not_found" else 409)
    return JsonResponse({"micro_check": serialize_micro_check(check)})


@login_required
@require_GET
def get_active_probe_offer(request, goal_id: int):
    surface = str(request.GET.get("surface") or "track")
    offer = active_probe_offer(
        request.user.username,
        goal_id,
        for_chat=surface == "chat",
    )
    return JsonResponse({"probe_offer": serialize_probe_offer(offer) if offer else None})


def _probe_offer_action(request, offer_id: str, action):
    try:
        offer = action(username=request.user.username, offer_id=offer_id)
    except ProbeOfferError as exc:
        status = 404 if exc.code == "probe_offer_not_found" else 409
        return _error(exc.code, status)
    return JsonResponse({"probe_offer": serialize_probe_offer(offer)})


@login_required
@require_POST
def accept_review_offer(request, offer_id: str):
    return _probe_offer_action(request, offer_id, accept_probe_offer)


@login_required
@require_POST
def snooze_review_offer(request, offer_id: str):
    return _probe_offer_action(request, offer_id, snooze_probe_offer)


@login_required
@require_POST
def dismiss_review_offer(request, offer_id: str):
    return _probe_offer_action(request, offer_id, dismiss_probe_offer)
