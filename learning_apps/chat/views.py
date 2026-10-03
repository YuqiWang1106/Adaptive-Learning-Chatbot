from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from learning_apps.chat.services.conversation_repository_service import ConversationScopeError
from learning_apps.chat.services.learner_memory_repository_service import LearnerMemoryScopeError
from learning_apps.application.workflows.conversation import (
    build_chat_page_context,
    clear_conversation,
    confirm_learner_memory,
    list_learner_memories,
    load_chat_history_page,
    propose_learner_memory,
    resolve_chat_access,
    revoke_learner_memory,
)
from learning_apps.web.request import json_body, safe_int
from learning_apps.web.student_access import require_student_preferences, require_student_preferences_json


logger = logging.getLogger(__name__)


@login_required
def chat(request):
    _profile, denied = require_student_preferences(request)
    if denied:
        return denied
    goal_id = safe_int(request.GET.get("learning_goal_id"))
    if request.method == "POST" and not goal_id:
        goal_id = safe_int(request.POST.get("learning_goal_id"))
    access = resolve_chat_access(request.user.username, goal_id)
    if not access.ok:
        if access.redirect_name == "learning_goal_open" and access.redirect_learning_goal_id:
            return redirect("learning_goal_open", learning_goal_id=access.redirect_learning_goal_id)
        if access.redirect_name == "self_assessment" and access.redirect_learning_goal_id:
            return redirect(f"{reverse('self_assessment')}?learning_goal_id={access.redirect_learning_goal_id}")
        if access.code == "goal_not_found":
            messages.warning(request, "Learning goal not found.")
        return redirect(access.redirect_name or "learning_goal")
    context = build_chat_page_context(
        username=request.user.username,
        learning_goal_id=access.learning_goal_id or 0,
        goal=access.goal or {},
    )
    return render(request, "chat/agent_page.html", context)


@login_required
def chat_history(request):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    goal_id = safe_int(request.GET.get("learning_goal_id"))
    if not goal_id:
        return JsonResponse({"error": "Missing learning_goal_id"}, status=400)
    try:
        return JsonResponse(load_chat_history_page(request.user.username, goal_id, request.GET.get("before")))
    except Exception:
        logger.exception("Chat history failed for goal %s", goal_id)
        return JsonResponse({"history": [], "next_cursor": None})


@login_required
def learning_goal_memories(request, learning_goal_id: int):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        memories = list_learner_memories(request.user.username, learning_goal_id)
    except LearnerMemoryScopeError:
        return JsonResponse({"error": "Learning goal not found."}, status=404)
    return JsonResponse({"learning_goal_id": learning_goal_id, "memories": memories})


@login_required
def learning_goal_memory_proposal(request, learning_goal_id: int):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    data = json_body(request)
    value = data.get("value_payload")
    if not isinstance(value, dict):
        value = data.get("value") if isinstance(data.get("value"), dict) else {"value": data.get("value")}
    try:
        decision = propose_learner_memory(
            username=request.user.username,
            learning_goal_id=learning_goal_id,
            kind=data.get("kind") or "",
            memory_key=data.get("memory_key") or "",
            value_payload=value,
            source="explicit_user_statement",
            idempotency_key=str(data.get("idempotency_key") or "") or None,
        )
    except LearnerMemoryScopeError:
        return JsonResponse({"error": "Learning goal not found."}, status=404)
    return JsonResponse({"decision": dict(decision.to_metadata())}, status=201 if decision.accepted else 400)


def _memory_transition(request, learning_goal_id: int, memory_id: str, transition):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    data = json_body(request)
    try:
        decision = transition(
            username=request.user.username,
            learning_goal_id=learning_goal_id,
            memory_id=memory_id,
            idempotency_key=str(data.get("idempotency_key") or "") or None,
        )
    except LearnerMemoryScopeError:
        return JsonResponse({"error": "Learner memory not found."}, status=404)
    return JsonResponse({"decision": dict(decision.to_metadata())}, status=200 if decision.accepted else 409)


@login_required
def learning_goal_memory_confirm(request, learning_goal_id: int, memory_id: str):
    return _memory_transition(request, learning_goal_id, memory_id, confirm_learner_memory)


@login_required
def learning_goal_memory_revoke(request, learning_goal_id: int, memory_id: str):
    return _memory_transition(request, learning_goal_id, memory_id, revoke_learner_memory)


@login_required
def learning_goal_conversation_delete(request, learning_goal_id: int):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        return JsonResponse(clear_conversation(request.user.username, learning_goal_id).to_dict())
    except ConversationScopeError:
        return JsonResponse({"error": "Learning goal not found."}, status=404)
