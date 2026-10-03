from __future__ import annotations

from typing import Any, Mapping

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import Http404

from .base import ProductWorkflowError, execute_capability


def build_teacher_student_report(username: str, classroom_id: int, student_id: int) -> dict[str, Any]:
    try:
        payload = execute_capability(
            "teacher.student_report",
            {"classroom_id": classroom_id, "student_id": student_id},
            username=username,
            workflow="teacher.student_report",
        )
    except ProductWorkflowError as exc:
        if exc.code == "teacher_resource_not_found":
            raise Http404("Student not found in this class.") from exc
        raise
    return dict(payload["report"])


def build_teacher_student_goal_report(
    username: str,
    classroom_id: int,
    student_id: int,
    learning_goal_id: int,
) -> dict[str, Any]:
    try:
        payload = execute_capability(
            "teacher.student_goal_report",
            {
                "classroom_id": classroom_id,
                "student_id": student_id,
                "target_goal_id": learning_goal_id,
            },
            username=username,
            workflow="teacher.student_goal_report",
        )
    except ProductWorkflowError as exc:
        if exc.code == "teacher_resource_not_found":
            raise Http404("Learning goal not found for this student.") from exc
        raise
    return dict(payload["report"])


def _translate_error(exc: ProductWorkflowError) -> None:
    if exc.code == "teacher_resource_not_found":
        raise Http404(exc.message or "Teacher resource not found.") from exc
    if exc.code == "teacher_authority_required":
        raise PermissionDenied(exc.message or "Teacher access required.") from exc
    if exc.code == "teacher_duplicate_resource":
        raise IntegrityError("Duplicate teacher resource.") from exc
    if exc.code == "teacher_validation_error":
        raise ValidationError(exc.message or "The requested classroom action is invalid.") from exc
    raise exc


def _execute(username: str, capability: str, arguments: Mapping[str, Any] | None = None) -> dict[str, Any]:
    try:
        return execute_capability(capability, arguments, username=username, workflow=capability)
    except ProductWorkflowError as exc:
        _translate_error(exc)
    raise AssertionError("unreachable")


def build_teacher_dashboard(username: str) -> dict[str, Any]:
    return _execute(username, "teacher.dashboard")


def build_teacher_classroom_detail(username: str, classroom_id: int, query: str = "") -> dict[str, Any]:
    return _execute(username, "teacher.classroom_detail", {"classroom_id": classroom_id, "query": query})


def get_teacher_classroom_edit(username: str, classroom_id: int) -> dict[str, Any]:
    return dict(_execute(username, "teacher.classroom_edit", {"classroom_id": classroom_id})["classroom"])


def create_teacher_classroom(username: str, cleaned_data: Mapping[str, Any]) -> int:
    return int(_execute(username, "teacher.classroom_create", dict(cleaned_data))["classroom_id"])


def update_teacher_classroom(username: str, classroom_id: int, cleaned_data: Mapping[str, Any]) -> None:
    _execute(username, "teacher.classroom_update", {"classroom_id": classroom_id, **dict(cleaned_data)})


def archive_teacher_classroom(username: str, classroom_id: int) -> None:
    _execute(username, "teacher.classroom_archive", {"classroom_id": classroom_id})


def delete_teacher_classroom(username: str, classroom_id: int) -> None:
    _execute(username, "teacher.classroom_delete", {"classroom_id": classroom_id})


def invite_teacher_student(
    username: str,
    classroom_id: int,
    student_username: str,
    message: str = "",
) -> dict[str, Any]:
    return _execute(
        username,
        "teacher.invitation_create",
        {"classroom_id": classroom_id, "student_username": student_username, "message": message},
    )


def revoke_teacher_invitation(username: str, classroom_id: int, invitation_id: int) -> None:
    _execute(username, "teacher.invitation_revoke", {"classroom_id": classroom_id, "invitation_id": invitation_id})


def remove_teacher_student(username: str, classroom_id: int, student_id: int) -> None:
    _execute(username, "teacher.student_remove", {"classroom_id": classroom_id, "student_id": student_id})


def build_student_classrooms(username: str) -> dict[str, Any]:
    return _execute(username, "student.classrooms")


def get_student_invitation_notification_count(username: str) -> int:
    payload = _execute(username, "student.invitation_notifications")
    return max(0, int(payload.get("pending_count") or 0))


def accept_student_invitation(username: str, invitation_id: int) -> None:
    _execute(username, "student.invitation_accept", {"invitation_id": invitation_id})


def decline_student_invitation(username: str, invitation_id: int) -> None:
    _execute(username, "student.invitation_decline", {"invitation_id": invitation_id})
