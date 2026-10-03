from __future__ import annotations

from django.db.models import Avg, Count, Max, Q
from django.utils import timezone
from django.utils.timesince import timesince

from learning_apps.teacher_portal.models import ClassroomInvitation, ClassroomMembership
from learning_apps.teacher_portal.services.classroom_service import (
    get_classroom_detail,
    get_owned_classroom,
    list_pending_invitations_for_student,
    list_student_classrooms,
    list_teacher_classrooms,
)


def _iso(value) -> str:
    return value.isoformat() if value else ""


def _ago(value) -> str:
    return timesince(value, timezone.now()) if value else ""


def _profile_dto(profile) -> dict:
    return {
        "user_id": int(profile.user_id),
        "username": str(profile.username),
        "email": str(profile.email or ""),
        "academic_level": str(profile.academic_level or ""),
    }


def _classroom_dto(classroom) -> dict:
    return {
        "id": int(classroom.id),
        "name": str(classroom.name),
        "subject": str(classroom.subject or ""),
        "term": str(classroom.term or ""),
        "description": str(classroom.description or ""),
        "is_archived": bool(classroom.is_archived),
        "teacher": _profile_dto(classroom.teacher),
        "created_at": _iso(classroom.created_at),
        "updated_at": _iso(classroom.updated_at),
    }


def _invitation_dto(invitation) -> dict:
    return {
        "id": int(invitation.id),
        "classroom": _classroom_dto(invitation.classroom),
        "student": _profile_dto(invitation.student),
        "invited_by": _profile_dto(invitation.invited_by),
        "message": str(invitation.message or ""),
        "status": str(invitation.status),
        "created_at": _iso(invitation.created_at),
        "created_ago": _ago(invitation.created_at),
    }


def _visible_history_filter(path: str) -> Q:
    now = timezone.now()
    return Q(
        **{
            f"{path}__scope_status": "active",
            f"{path}__conversation__lifecycle": "active",
            f"{path}__conversation__active_scope_key__isnull": False,
            f"{path}__conversation__retention_expires_at__gt": now,
        }
    )


def teacher_dashboard_dto(teacher) -> dict:
    classrooms = list_teacher_classrooms(teacher).annotate(
        student_count=Count(
            "memberships",
            filter=Q(memberships__status=ClassroomMembership.STATUS_ACCEPTED),
            distinct=True,
        ),
        learning_goal_count=Count(
            "memberships__student__learning_goals",
            filter=Q(memberships__status=ClassroomMembership.STATUS_ACCEPTED),
            distinct=True,
        ),
        chat_count=Count(
            "memberships__student__histories",
            filter=(
                Q(memberships__status=ClassroomMembership.STATUS_ACCEPTED)
                & _visible_history_filter("memberships__student__histories")
            ),
            distinct=True,
        ),
        pending_invitation_count=Count(
            "invitations",
            filter=Q(invitations__status=ClassroomInvitation.STATUS_PENDING),
            distinct=True,
        ),
        last_student_activity=Max(
            "memberships__student__histories__timestamp",
            filter=(
                Q(memberships__status=ClassroomMembership.STATUS_ACCEPTED)
                & _visible_history_filter("memberships__student__histories")
            ),
        ),
    )
    active_memberships = ClassroomMembership.objects.filter(
        classroom__teacher=teacher,
        classroom__is_archived=False,
        status=ClassroomMembership.STATUS_ACCEPTED,
    )
    classroom_rows = []
    for classroom in classrooms:
        row = _classroom_dto(classroom)
        row.update(
            {
                "student_count": int(classroom.student_count or 0),
                "learning_goal_count": int(classroom.learning_goal_count or 0),
                "chat_count": int(classroom.chat_count or 0),
                "pending_invitation_count": int(classroom.pending_invitation_count or 0),
                "last_student_activity": _iso(classroom.last_student_activity),
                "last_student_activity_ago": _ago(classroom.last_student_activity),
            }
        )
        classroom_rows.append(row)
    return {
        "classrooms": classroom_rows,
        "dashboard_summary": {
            "classroom_count": len(classroom_rows),
            "student_count": active_memberships.values("student_id").distinct().count(),
            "learning_goal_count": active_memberships.values("student__learning_goals__id")
            .exclude(student__learning_goals__id__isnull=True)
            .distinct()
            .count(),
            "attention_student_count": active_memberships.filter(
                student__mastery_states__quality_score__lt=0.5
            )
            .values("student_id")
            .distinct()
            .count(),
        },
    }


def classroom_detail_dto(teacher, classroom_id: int, student_query: str = "") -> dict:
    detail = get_classroom_detail(teacher, classroom_id)
    class_overview = detail.memberships.aggregate(
        student_count=Count("id", distinct=True),
        learning_goal_count=Count("student__learning_goals", distinct=True),
        chat_count=Count(
            "student__histories",
            filter=_visible_history_filter("student__histories"),
            distinct=True,
        ),
        active_student_count=Count(
            "student",
            filter=_visible_history_filter("student__histories"),
            distinct=True,
        ),
    )
    class_overview["pending_invitation_count"] = detail.pending_invitations.count()
    query = str(student_query or "").strip()
    students = detail.memberships
    if query:
        students = students.filter(
            Q(student__username__icontains=query)
            | Q(student__email__icontains=query)
            | Q(student__academic_level__icontains=query)
        )
    students = students.annotate(
        learning_goal_count=Count("student__learning_goals", distinct=True),
        chat_count=Count(
            "student__histories",
            filter=_visible_history_filter("student__histories"),
            distinct=True,
        ),
        last_chat=Max(
            "student__histories__timestamp",
            filter=_visible_history_filter("student__histories"),
        ),
        mastery_quality=Avg("student__mastery_states__quality_score"),
        pending_probe_count=Count(
            "student__adaptive_probes",
            filter=Q(student__adaptive_probes__status="pending"),
            distinct=True,
        ),
    )
    student_rows = []
    for membership in students:
        quality = membership.mastery_quality
        quality_pct = round(float(quality or 0) * 100)
        if not membership.learning_goal_count:
            review_state, review_label = "new", "No goals yet"
        elif quality is None:
            review_state, review_label = "collecting", "Collecting evidence"
        elif float(quality) < 0.5:
            review_state, review_label = "priority", "Priority review"
        elif float(quality) < 0.75 or membership.pending_probe_count:
            review_state, review_label = "monitor", "Monitor"
        else:
            review_state, review_label = "on_track", "On track"
        student_rows.append(
            {
                "id": int(membership.id),
                "student": _profile_dto(membership.student),
                "learning_goal_count": int(membership.learning_goal_count or 0),
                "chat_count": int(membership.chat_count or 0),
                "last_chat": _iso(membership.last_chat),
                "last_chat_ago": _ago(membership.last_chat),
                "mastery_quality": float(quality) if quality is not None else None,
                "mastery_quality_pct": quality_pct,
                "pending_probe_count": int(membership.pending_probe_count or 0),
                "review_state": review_state,
                "review_label": review_label,
            }
        )
    class_overview["attention_student_count"] = sum(
        1 for row in student_rows if row["review_state"] == "priority"
    )
    invitation_history = (
        ClassroomInvitation.objects.filter(classroom=detail.classroom)
        .select_related("classroom", "classroom__teacher", "student", "invited_by")
        .order_by("-created_at")[:20]
    )
    return {
        "classroom": _classroom_dto(detail.classroom),
        "students": student_rows,
        "student_query": query,
        "class_overview": {key: int(value or 0) for key, value in class_overview.items()},
        "pending_invitations": [_invitation_dto(row) for row in detail.pending_invitations],
        "invitation_history": [_invitation_dto(row) for row in invitation_history],
    }


def classroom_edit_dto(teacher, classroom_id: int) -> dict:
    return _classroom_dto(get_owned_classroom(teacher, classroom_id))


def student_classrooms_dto(student) -> dict:
    memberships = []
    for membership in list_student_classrooms(student):
        memberships.append(
            {
                "id": int(membership.id),
                "classroom": _classroom_dto(membership.classroom),
                "accepted_at": _iso(membership.accepted_at),
            }
        )
    return {
        "memberships": memberships,
        "pending_invitations": [
            _invitation_dto(row) for row in list_pending_invitations_for_student(student)
        ],
    }


def student_invitation_notification_dto(student) -> dict:
    """Return the small navigation projection without leaking ORM to presentation."""

    return {
        "pending_count": list_pending_invitations_for_student(student).count(),
    }
