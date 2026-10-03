from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.conf import settings
from django.http import JsonResponse
from django.views.decorators.http import require_GET, require_POST
from django.shortcuts import redirect, render
from django.urls import reverse

from learning_apps.application.workflows.assessment import (
    build_self_assessment_template_context,
    build_self_assessment_result_context,
    build_revision_form_values,
    build_prepared_scope_context,
    get_target_scope_job,
    get_self_assessment_submission_job,
    resolve_request_goal_context,
    start_self_assessment_submission_job,
    start_target_scope_job,
    validate_submission,
)
from learning_apps.web.student_access import require_student_preferences, require_student_preferences_json


@login_required
def self_assessment(request):
    _profile, denied = require_student_preferences(request)
    if denied:
        return denied
    context = resolve_request_goal_context(request, request.user.username)
    if not context.ok:
        messages.warning(request, "Learning goal not found.")
        return redirect("learning_goal")
    if request.method == "POST":
        if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED:
            validation = validate_submission(dict(request.POST.items()))
            if not validation.valid:
                payload = build_self_assessment_template_context(
                    request.user.username,
                    context,
                )
                payload.update(
                    {
                        "field_errors": validation.errors,
                        "form_values": dict(request.POST.items()),
                        "form_has_errors": True,
                    }
                )
                payload["prepared_scope"] = build_prepared_scope_context(
                    request.user.username,
                    context.learning_goal_id,
                    dict(request.POST.items()),
                )
                return render(request, "self_assessment.html", payload, status=400)
        job_id = start_self_assessment_submission_job(
            username=request.user.username,
            post_data=dict(request.POST.items()),
            goal_context=context,
        )
        return redirect("self_assessment_loading", job_id=job_id)
    payload = build_self_assessment_template_context(request.user.username, context)
    revise_id = request.GET.get("revise")
    if revise_id and context.learning_goal_id:
        try:
            payload["form_values"] = build_revision_form_values(
                request.user.username,
                int(revise_id),
                learning_goal_id=context.learning_goal_id,
            )
            payload["prepared_scope"] = build_prepared_scope_context(
                request.user.username,
                context.learning_goal_id,
                payload["form_values"],
            )
            payload["is_revision"] = True
        except (TypeError, ValueError):
            messages.warning(request, "That assessment revision link is invalid.")
    payload["from_loading"] = request.GET.get("from_loading") == "1"
    template_name = (
        "self_assessment.html"
        if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED
        else "self_assessment_v1.html"
    )
    return render(request, template_name, payload)


@login_required
@require_POST
def self_assessment_target_scope_prepare(request):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    context = resolve_request_goal_context(request, request.user.username)
    if not context.ok or not context.learning_goal_id:
        return JsonResponse(
            {"error": "learning_goal_not_found", "message": "Learning goal not found."},
            status=404,
        )
    target_task = str(request.POST.get("target_task") or "")
    try:
        job_id = start_target_scope_job(
            username=request.user.username,
            target_task=target_task,
            goal_context=context,
        )
    except ValueError as exc:
        return JsonResponse(
            {"error": "target_task_invalid", "message": str(exc)},
            status=400,
        )
    return JsonResponse(
        {
            "job_id": job_id,
            "status_url": reverse(
                "self_assessment_target_scope_status",
                kwargs={"job_id": job_id},
            ),
        },
        status=202,
    )


@login_required
@require_GET
def self_assessment_target_scope_status(request, job_id: str):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    job = get_target_scope_job(job_id, request.user.username)
    if job:
        return JsonResponse(job)
    return JsonResponse(
        {
            "state": "expired",
            "stage": "expired",
            "percent": 100,
            "message": "This scope session expired. Prepare the target task again.",
        },
        status=404,
    )


@login_required
def self_assessment_result(request, assessment_id: int):
    _profile, denied = require_student_preferences(request)
    if denied:
        return denied
    payload = build_self_assessment_result_context(
        request.user.username,
        assessment_id,
    )
    return render(request, "self_assessment_result.html", payload)


@login_required
def self_assessment_loading(request, job_id: str):
    _profile, denied = require_student_preferences(request)
    if denied:
        return denied
    job = get_self_assessment_submission_job(job_id, request.user.username)
    if not job:
        messages.warning(request, "That assessment session expired. Please submit your assessment again.")
        return redirect("self_assessment")
    fallback_url = reverse("self_assessment")
    if job.get("learning_goal_id"):
        fallback_url += f"?learning_goal_id={job['learning_goal_id']}"
    return render(
        request,
        "learning_goal/loading.html",
        {
            "job_id": job_id,
            "initial_job": job,
            "status_url": reverse("self_assessment_submission_status", kwargs={"job_id": job_id}),
            "fallback_url": fallback_url,
            "loading_title": "Evaluating Your Assessment - Learning Workflow Demo",
            "loading_kicker": "Evaluating",
            "loading_heading": "Reviewing your assessment",
            "loading_default_message": "Preparing your assessment.",
            "loading_progress_label": "Self-assessment evaluation progress",
            "loading_failure_message": "Learning Workflow Demo could not finish this assessment. Please try again.",
            "loading_fallback_label": "Back to self-assessment",
        },
    )


@login_required
def self_assessment_submission_status(request, job_id: str):
    _profile, denied = require_student_preferences_json(request)
    if denied:
        return denied
    job = get_self_assessment_submission_job(job_id, request.user.username)
    if job:
        return JsonResponse(job)
    return JsonResponse(
        {
            "state": "expired",
            "stage": "expired",
            "percent": 100,
            "message": "This assessment session expired. Please submit your assessment again.",
            "redirect_url": reverse("self_assessment"),
        },
        status=404,
    )
