from __future__ import annotations

import logging
from typing import Any

from django.contrib import messages
from django.contrib.auth import authenticate, login as auth_login, logout as auth_logout
from django.contrib.auth.decorators import login_required
from django.shortcuts import redirect, render

from .application_service import (
    ROLE_STUDENT,
    ROLE_TEACHER,
    authenticate_login_user,
    build_google_authorize_url,
    build_preferences_initial_data,
    get_current_profile,
    ensure_profile_for_google_user,
    exchange_code_for_userinfo,
    is_google_configured,
    register_new_user,
    save_user_preferences,
    sync_django_user,
)
from .forms import LoginForm, PreferencesSetupForm, RegistrationForm
from learning_apps.web.student_access import redirect_authenticated_user


logger = logging.getLogger(__name__)


def normalize_role(value: Any) -> str:
    role = str(value or "").strip().lower()
    return role if role in {ROLE_STUDENT, ROLE_TEACHER} else ""


def request_role(request) -> str:
    return normalize_role(
        request.POST.get("user_type")
        or request.POST.get("role")
        or request.GET.get("user_type")
        or request.GET.get("role")
    )


def landing_auth_context(**overrides):
    selected_role = normalize_role(overrides.pop("selected_role", ""))
    context = {
        "login_form": LoginForm(initial={"user_type": selected_role}),
        "register_form": RegistrationForm(initial={"user_type": selected_role}),
        "preferences_form": PreferencesSetupForm(),
        "selected_role": selected_role,
    }
    context.update(overrides)
    return context


def login_view(request):
    if request.user.is_authenticated:
        return redirect_authenticated_user(request)
    selected_role = request_role(request)
    form = LoginForm(request.POST or None, initial={"user_type": selected_role})
    modal = request.POST.get("_auth_modal") == "login"
    if request.method == "POST":
        if form.is_valid():
            username = form.cleaned_data["username"]
            selected_role = normalize_role(form.cleaned_data.get("user_type"))
            result = authenticate_login_user(
                request,
                username=username,
                password=form.cleaned_data["password"],
            )
            if result.ok and result.user is not None:
                profile = get_current_profile(result.user.username)
                if selected_role and profile and profile.get("role", ROLE_STUDENT) != selected_role:
                    messages.error(
                        request,
                        "This account belongs to a different user type. Please choose the correct entrance.",
                    )
                else:
                    auth_login(request, result.user)
                    return redirect(result.redirect_name)
            else:
                messages.error(
                    request,
                    "Too many login attempts. Please try again later."
                    if result.code == "rate_limited"
                    else "Invalid username or password.",
                )
        else:
            messages.error(request, "Please enter both username and password.")
        if modal:
            return render(
                request,
                "index.html",
                landing_auth_context(login_form=form, auth_modal="login", selected_role=selected_role),
            )
    return render(request, "accounts/login.html", {"form": form, "selected_role": selected_role})


def logout_view(request):
    auth_logout(request)
    messages.info(request, "You have been logged out.")
    return redirect("index")


def register_view(request):
    if request.user.is_authenticated:
        return redirect_authenticated_user(request)
    selected_role = request_role(request)
    form = RegistrationForm(request.POST or None, initial={"user_type": selected_role})
    modal = request.POST.get("_auth_modal") == "register"
    if request.method == "POST" and form.is_valid():
        selected_role = normalize_role(form.cleaned_data.get("user_type")) or ROLE_STUDENT
        username = form.cleaned_data["username"]
        result = register_new_user(
            username=username,
            email=form.cleaned_data["email"],
            raw_password=form.cleaned_data["password"],
            role=selected_role,
        )
        if result.ok:
            user = authenticate(request, username=username, password=form.cleaned_data["password"])
            if user is not None:
                auth_login(request, user)
                return redirect("teacher_dashboard" if selected_role == ROLE_TEACHER else "setup_preferences")
            messages.success(request, "Account created. Please log in.")
            return redirect("login")
        messages.error(request, "Could not create account with these details.")
    if request.method == "POST" and modal:
        return render(
            request,
            "index.html",
            landing_auth_context(register_form=form, auth_modal="register", selected_role=selected_role),
        )
    return render(request, "accounts/register.html", {"form": form, "selected_role": selected_role})


@login_required
def setup_preferences(request):
    profile = get_current_profile(request.user.username)
    if not profile:
        messages.error(request, "User profile not found.")
        return redirect("login")
    if profile.get("role") == ROLE_TEACHER:
        return redirect("teacher_dashboard")
    if profile.get("preferences_completed", False):
        return redirect("learning_goal")
    form = PreferencesSetupForm(request.POST or None, initial=build_preferences_initial_data(profile))
    if request.method == "POST" and form.is_valid():
        if save_user_preferences(request.user.username, form.cleaned_data):
            messages.success(request, "Preferences saved. You're ready to go!")
            return redirect("learning_goal")
        messages.error(request, "Failed to save preferences. Please try again.")
    return render(
        request,
        "index.html",
        landing_auth_context(preferences_form=form, auth_modal="preferences"),
    )


def google_login(request):
    if request.user.is_authenticated:
        return redirect_authenticated_user(request)
    if not is_google_configured():
        messages.error(request, "Google login is not configured.")
        return redirect("login")
    state, authorize_url = build_google_authorize_url(request)
    request.session["google_oauth_state"] = state
    return redirect(authorize_url)


def google_callback(request):
    if not is_google_configured():
        messages.error(request, "Google login is not configured.")
        return redirect("login")
    expected_state = request.session.pop("google_oauth_state", None)
    if not expected_state or expected_state != request.GET.get("state"):
        messages.error(request, "Google login failed (state mismatch). Please try again.")
        return redirect("login")
    if request.GET.get("error"):
        messages.error(request, "Google login was canceled.")
        return redirect("login")
    code = request.GET.get("code")
    if not code:
        messages.error(request, "Google login failed: missing authorization code.")
        return redirect("login")
    try:
        userinfo = exchange_code_for_userinfo(request, code)
    except Exception as exc:
        logger.warning("Google callback verification failed: %s", exc)
        messages.error(request, "Google login failed while verifying your account.")
        return redirect("login")
    email = str(userinfo.get("email") or "").strip().lower()
    profile = ensure_profile_for_google_user(email=email) if email else None
    if not profile:
        messages.error(request, "Could not create your account from Google profile.")
        return redirect("login")
    auth_login(
        request,
        sync_django_user(profile),
        backend="django.contrib.auth.backends.ModelBackend",
    )
    if profile.get("role") == ROLE_TEACHER:
        return redirect("teacher_dashboard")
    return redirect("learning_goal" if profile.get("preferences_completed", False) else "setup_preferences")
