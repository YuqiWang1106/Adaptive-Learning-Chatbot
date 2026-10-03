from __future__ import annotations

from typing import Any

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from learning_apps.application.workflows.adaptive import (
    build_progress_payload,
    skip_probe,
    submit_probe_answer,
)
from learning_apps.application.workflows.conversation import resolve_chat_access
from learning_apps.application.workflows.learning_goals import list_user_learning_goals
from learning_apps.web.request import json_body
from learning_apps.web.student_access import require_student_preferences, require_student_preferences_json


def _has_probe_answer(payload: Any) -> bool:
    if isinstance(payload, str):
        return bool(payload.strip())
    if not isinstance(payload, list):
        return False
    answer_fields = {
        "answer",
        "final_answer",
        "method_choice",
        "reason",
        "identified_error",
        "correction",
        "explanation",
    }
    for item in payload:
        if not isinstance(item, dict):
            continue
        if any(str(item.get(field) or "").strip() for field in answer_fields):
            return True
        steps = item.get("reasoning_steps") or item.get("steps") or []
        if isinstance(steps, list) and any(str(step).strip() for step in steps):
            return True
    return False


@login_required
def learning_goal_adaptive_progress(request, learning_goal_id: int):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    payload = build_progress_payload(request.user.username, learning_goal_id)
    return JsonResponse(payload, status=200 if payload.get("ok") else 404)


@login_required
def learning_goal_dashboard(request, learning_goal_id: int):
    _profile, denied = require_student_preferences(request)
    if denied:
        return denied
    access = resolve_chat_access(request.user.username, learning_goal_id)
    if not access.ok:
        if access.redirect_name == "self_assessment" and access.redirect_learning_goal_id:
            return redirect(f"{reverse('self_assessment')}?learning_goal_id={access.redirect_learning_goal_id}")
        if access.redirect_name == "learning_goal_open" and access.redirect_learning_goal_id:
            return redirect("learning_goal_open", learning_goal_id=access.redirect_learning_goal_id)
        return redirect(access.redirect_name or "learning_goal")
    goal_id = access.learning_goal_id or learning_goal_id
    return render(
        request,
        "learning_goal/adaptive_dashboard.html",
        {
            "learning_goal": access.goal or {},
            "learning_goal_id": goal_id,
            "active_learning_goal_id": goal_id,
            "learning_goals": list_user_learning_goals(request.user.username),
        },
    )


@login_required
def learning_goal_adaptive_probe_submit(request, learning_goal_id: int, probe_id: int):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    data = json_body(request)
    answer: Any = data.get("answers") if isinstance(data.get("answers"), list) else str(data.get("answer") or "").strip()
    if not _has_probe_answer(answer):
        return JsonResponse({"error": "Missing answer"}, status=400)
    payload = submit_probe_answer(request.user.username, learning_goal_id, probe_id, answer)
    return JsonResponse(payload, status=202 if payload.get("ok") else 404)


@login_required
def learning_goal_adaptive_probe_skip(request, learning_goal_id: int, probe_id: int):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    payload = skip_probe(request.user.username, learning_goal_id, probe_id)
    return JsonResponse(payload, status=200 if payload.get("ok") else 404)
