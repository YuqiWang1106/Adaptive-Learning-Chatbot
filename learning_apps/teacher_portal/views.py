from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.db import IntegrityError
from django.shortcuts import redirect, render
from django.views.decorators.http import require_POST

from learning_apps.application.workflows.teacher import (
    accept_student_invitation,
    archive_teacher_classroom,
    build_student_classrooms,
    build_teacher_classroom_detail,
    build_teacher_dashboard,
    build_teacher_student_goal_report,
    build_teacher_student_report,
    create_teacher_classroom,
    decline_student_invitation,
    delete_teacher_classroom,
    get_teacher_classroom_edit,
    invite_teacher_student,
    remove_teacher_student,
    revoke_teacher_invitation,
    update_teacher_classroom,
)
from learning_apps.teacher_portal.forms import ClassroomInvitationForm, TeacherClassroomForm
from learning_apps.teacher_portal.services.classroom_service import require_student_profile, require_teacher_profile


def _validation_message(exc: ValidationError) -> str:
    if hasattr(exc, "messages") and exc.messages:
        return " ".join(str(message) for message in exc.messages)
    return str(exc)


@login_required
def teacher_dashboard(request):
    teacher = require_teacher_profile(request.user)
    form = TeacherClassroomForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            try:
                classroom_id = create_teacher_classroom(teacher.username, form.cleaned_data)
            except IntegrityError:
                form.add_error("name", "You already have a class with this name.")
            else:
                messages.success(request, "Classroom created.")
                return redirect("teacher_classroom_detail", classroom_id=classroom_id)
        messages.error(request, "Please fix the classroom details.")

    dashboard = build_teacher_dashboard(teacher.username)

    return render(
        request,
        "teacher_portal/dashboard.html",
        {
            "classrooms": dashboard["classrooms"],
            "dashboard_summary": dashboard["dashboard_summary"],
            "form": form,
        },
    )


@login_required
def classroom_detail(request, classroom_id: int):
    teacher = require_teacher_profile(request.user)
    invite_form = ClassroomInvitationForm(request.POST or None)
    if request.method == "POST":
        if invite_form.is_valid():
            try:
                result = invite_teacher_student(
                    teacher.username,
                    classroom_id,
                    invite_form.cleaned_data["username"],
                    invite_form.cleaned_data.get("message", ""),
                )
            except ValidationError as exc:
                messages.error(request, _validation_message(exc))
            else:
                messages.success(request, "Invitation sent." if result["created"] else "That student already has a pending invitation.")
                return redirect("teacher_classroom_detail", classroom_id=classroom_id)
        else:
            messages.error(request, "Please enter a valid student username.")

    student_query = (request.GET.get("q") or "").strip()
    page = build_teacher_classroom_detail(teacher.username, classroom_id, student_query)
    return render(
        request,
        "teacher_portal/classroom_detail.html",
        {
            "classroom": page["classroom"],
            "students": page["students"],
            "student_query": page["student_query"],
            "class_overview": page["class_overview"],
            "invite_form": invite_form,
            "pending_invitations": page["pending_invitations"],
            "invitation_history": page["invitation_history"],
        },
    )


@login_required
def classroom_edit(request, classroom_id: int):
    teacher = require_teacher_profile(request.user)
    classroom = get_teacher_classroom_edit(teacher.username, classroom_id)
    form = TeacherClassroomForm(request.POST or None, initial=classroom)
    if request.method == "POST":
        if form.is_valid():
            try:
                update_teacher_classroom(teacher.username, classroom_id, form.cleaned_data)
            except IntegrityError:
                form.add_error("name", "You already have a class with this name.")
            else:
                messages.success(request, "Classroom updated.")
                return redirect("teacher_classroom_detail", classroom_id=classroom_id)
        messages.error(request, "Please fix the classroom details.")
    return render(request, "teacher_portal/classroom_form.html", {"classroom": classroom, "form": form})


@require_POST
@login_required
def classroom_archive(request, classroom_id: int):
    teacher = require_teacher_profile(request.user)
    archive_teacher_classroom(teacher.username, classroom_id)
    messages.info(request, "Classroom archived.")
    return redirect("teacher_dashboard")


@require_POST
@login_required
def classroom_delete(request, classroom_id: int):
    teacher = require_teacher_profile(request.user)
    delete_teacher_classroom(teacher.username, classroom_id)
    messages.info(request, "Classroom deleted.")
    return redirect("teacher_dashboard")


@require_POST
@login_required
def classroom_invitation_revoke(request, classroom_id: int, invitation_id: int):
    teacher = require_teacher_profile(request.user)
    revoke_teacher_invitation(teacher.username, classroom_id, invitation_id)
    messages.info(request, "Invitation revoked.")
    return redirect("teacher_classroom_detail", classroom_id=classroom_id)


@require_POST
@login_required
def classroom_remove_student(request, classroom_id: int, student_id: int):
    teacher = require_teacher_profile(request.user)
    remove_teacher_student(teacher.username, classroom_id, student_id)
    messages.info(request, "Student removed from this class.")
    return redirect("teacher_classroom_detail", classroom_id=classroom_id)


@login_required
def student_report(request, classroom_id: int, student_id: int):
    teacher = require_teacher_profile(request.user)
    return render(
        request,
        "teacher_portal/student_report.html",
        {"report": build_teacher_student_report(teacher.username, classroom_id, student_id)},
    )


@login_required
def student_goal_report(request, classroom_id: int, student_id: int, learning_goal_id: int):
    teacher = require_teacher_profile(request.user)
    return render(
        request,
        "teacher_portal/student_goal_report.html",
        {
            "report": build_teacher_student_goal_report(
                teacher.username,
                classroom_id,
                student_id,
                learning_goal_id,
            )
        },
    )


@login_required
def student_classrooms(request):
    student = require_student_profile(request.user)
    page = build_student_classrooms(student.username)
    return render(
        request,
        "teacher_portal/student_classrooms.html",
        {
            "memberships": page["memberships"],
            "pending_invitations": page["pending_invitations"],
        },
    )


@require_POST
@login_required
def student_invitation_accept(request, invitation_id: int):
    student = require_student_profile(request.user)
    try:
        accept_student_invitation(student.username, invitation_id)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.success(request, "Invitation accepted. You joined the class.")
    return redirect("student_classrooms")


@require_POST
@login_required
def student_invitation_decline(request, invitation_id: int):
    student = require_student_profile(request.user)
    try:
        decline_student_invitation(student.username, invitation_id)
    except ValidationError as exc:
        messages.error(request, _validation_message(exc))
    else:
        messages.info(request, "Invitation declined.")
    return redirect("student_classrooms")
