from __future__ import annotations

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect

from learning_apps.accounts.application_service import ROLE_TEACHER, get_current_profile


def redirect_name_for_profile(profile: dict | None) -> str:
    if profile and profile.get("role") == ROLE_TEACHER:
        return "teacher_dashboard"
    if profile and not profile.get("preferences_completed", False):
        return "setup_preferences"
    return "learning_goal"


def redirect_authenticated_user(request):
    profile = get_current_profile(request.user.username)
    return redirect(redirect_name_for_profile(profile))


def require_student_preferences(request):
    profile = get_current_profile(request.user.username)
    if not profile:
        messages.error(request, "User profile not found.")
        return None, redirect("login")
    if profile.get("role") == ROLE_TEACHER:
        return profile, redirect("teacher_dashboard")
    if not profile.get("preferences_completed", False):
        messages.info(request, "Please complete your preferences setup to continue.")
        return profile, redirect("setup_preferences")
    return profile, None


def require_student_preferences_json(request):
    profile = get_current_profile(request.user.username)
    if not profile:
        return None, JsonResponse({"error": "User profile not found."}, status=404)
    if profile.get("role") == ROLE_TEACHER:
        return profile, JsonResponse(
            {"error": "Teacher accounts cannot access student learning APIs."}, status=403
        )
    if not profile.get("preferences_completed", False):
        return profile, JsonResponse({"error": "Preferences not completed."}, status=403)
    return profile, None
