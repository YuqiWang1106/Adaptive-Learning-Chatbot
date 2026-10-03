from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import redirect, render
from django.urls import reverse

from learning_apps.knowledge.services.material_rag_service import MaterialUploadError
from learning_apps.application.workflows.knowledge import (
    delete_learning_material,
    get_material_job,
    list_learning_materials,
    upload_learning_material,
)
from learning_apps.application.workflows.learning_goals import (
    get_learning_goal,
    get_learning_goal_creation_job,
    list_user_learning_goals,
    resolve_open_learning_goal,
    start_learning_goal_creation_job,
)
from learning_apps.web.student_access import require_student_preferences, require_student_preferences_json


logger = logging.getLogger(__name__)


@login_required
def learning_goal(request):
    _profile, denied = require_student_preferences(request)
    if denied:
        return denied
    username = request.user.username
    if request.method == "POST":
        preference = request.POST.get("preference", "").strip()
        if not preference:
            messages.warning(request, "Please describe your learning goal in one sentence.")
            return render(
                request,
                "learning_goal/page.html",
                {"learning_goals": list_user_learning_goals(username), "active_learning_goal_id": None},
            )
        job_id = start_learning_goal_creation_job(username, preference)
        return redirect("learning_goal_loading", job_id=job_id)
    return render(
        request,
        "learning_goal/page.html",
        {"learning_goals": list_user_learning_goals(username), "active_learning_goal_id": None},
    )


@login_required
def learning_goal_loading(request, job_id: str):
    _profile, denied = require_student_preferences(request)
    if denied:
        return denied
    job = get_learning_goal_creation_job(job_id, request.user.username)
    if not job:
        messages.warning(request, "That learning goal setup session expired. Please submit your goal again.")
        return redirect("learning_goal")
    return render(
        request,
        "learning_goal/loading.html",
        {
            "job_id": job_id,
            "initial_job": job,
            "status_url": reverse("learning_goal_creation_status", kwargs={"job_id": job_id}),
            "fallback_url": reverse("learning_goal"),
        },
    )


@login_required
def learning_goal_creation_status(request, job_id: str):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    job = get_learning_goal_creation_job(job_id, request.user.username)
    if job:
        return JsonResponse(job)
    return JsonResponse(
        {
            "state": "expired",
            "stage": "expired",
            "percent": 100,
            "message": "This setup session expired. Please submit your goal again.",
            "redirect_url": reverse("learning_goal"),
        },
        status=404,
    )


@login_required
def open_learning_goal(request, learning_goal_id: int):
    _profile, denied = require_student_preferences(request)
    if denied:
        return denied
    result = resolve_open_learning_goal(request.user.username, learning_goal_id)
    if not result.ok:
        messages.warning(request, "Learning goal not found.")
        return redirect("learning_goal")
    return redirect(f"{reverse(result.redirect_name)}?learning_goal_id={learning_goal_id}")


@login_required
def learning_goal_materials(request, learning_goal_id: int):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    goal = get_learning_goal(request.user.username, learning_goal_id)
    if not goal:
        return JsonResponse({"error": "Learning goal not found."}, status=404)
    return JsonResponse(
        {
            "learning_goal_id": learning_goal_id,
            "vector_store_status": goal.get("vector_store_status") or "",
            "materials": list_learning_materials(request.user.username, learning_goal_id),
        }
    )


@login_required
def learning_goal_material_upload(request, learning_goal_id: int):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    if request.method != "POST":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    uploaded_file = request.FILES.get("file")
    if uploaded_file is None:
        return JsonResponse({"error": "Missing file."}, status=400)
    try:
        material = upload_learning_material(request.user.username, learning_goal_id, uploaded_file)
        return JsonResponse({"material": material}, status=201)
    except MaterialUploadError as exc:
        return JsonResponse({"error": str(exc)}, status=400)
    except Exception:
        logger.exception("Learning material upload failed for goal %s", learning_goal_id)
        return JsonResponse({"error": "Could not accept this material."}, status=500)


@login_required
def learning_goal_material_job_status(request, learning_goal_id: int, job_id: str):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    if request.method != "GET":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    payload = get_material_job(request.user.username, learning_goal_id, job_id)
    return JsonResponse(payload) if payload else JsonResponse({"error": "Material job not found."}, status=404)


@login_required
def learning_goal_material_delete(request, learning_goal_id: int, material_id: int):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    if request.method != "DELETE":
        return JsonResponse({"error": "Method not allowed"}, status=405)
    try:
        material = delete_learning_material(request.user.username, learning_goal_id, material_id)
        return JsonResponse({"material": material}, status=202)
    except MaterialUploadError as exc:
        return JsonResponse({"error": str(exc)}, status=404)
    except Exception:
        logger.exception("Learning material deletion failed for goal %s material %s", learning_goal_id, material_id)
        return JsonResponse({"error": "Could not schedule material deletion."}, status=500)
