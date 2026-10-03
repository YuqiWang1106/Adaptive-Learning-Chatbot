from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import QuerySet
from django.http import Http404
from django.utils import timezone

from learning_apps.persistence.models import UserProfile
from learning_apps.teacher_portal.models import ClassroomInvitation, ClassroomMembership, TeacherClassroom


@dataclass(frozen=True)
class InvitationResult:
    invitation: ClassroomInvitation
    created: bool
    code: str = ""


@dataclass(frozen=True)
class ClassroomDetail:
    classroom: TeacherClassroom
    memberships: QuerySet[ClassroomMembership]
    pending_invitations: QuerySet[ClassroomInvitation]


def get_profile_for_django_user(django_user) -> UserProfile:
    if not getattr(django_user, "is_authenticated", False):
        raise PermissionDenied("Authentication required.")
    try:
        return UserProfile.objects.get(username=django_user.username)
    except UserProfile.DoesNotExist as exc:
        raise Http404("User profile not found.") from exc


def require_teacher_profile(django_user) -> UserProfile:
    profile = get_profile_for_django_user(django_user)
    if profile.role != UserProfile.ROLE_TEACHER:
        raise PermissionDenied("Teacher access required.")
    return profile


def require_student_profile(django_user) -> UserProfile:
    profile = get_profile_for_django_user(django_user)
    if profile.role != UserProfile.ROLE_STUDENT:
        raise PermissionDenied("Student access required.")
    return profile


def list_teacher_classrooms(teacher: UserProfile) -> QuerySet[TeacherClassroom]:
    return TeacherClassroom.objects.filter(teacher=teacher, is_archived=False).order_by("-updated_at", "-created_at")


@transaction.atomic
def create_classroom(teacher: UserProfile, cleaned_data: dict) -> TeacherClassroom:
    if teacher.role != UserProfile.ROLE_TEACHER:
        raise PermissionDenied("Teacher access required.")
    return TeacherClassroom.objects.create(
        teacher=teacher,
        name=cleaned_data["name"],
        subject=cleaned_data.get("subject", ""),
        term=cleaned_data.get("term", ""),
        description=cleaned_data.get("description", ""),
    )


@transaction.atomic
def update_classroom(teacher: UserProfile, classroom_id: int, cleaned_data: dict) -> TeacherClassroom:
    classroom = get_owned_classroom(teacher, classroom_id)
    classroom.name = cleaned_data["name"]
    classroom.subject = cleaned_data.get("subject", "")
    classroom.term = cleaned_data.get("term", "")
    classroom.description = cleaned_data.get("description", "")
    classroom.save(update_fields=["name", "subject", "term", "description", "updated_at"])
    return classroom


@transaction.atomic
def archive_classroom(teacher: UserProfile, classroom_id: int) -> TeacherClassroom:
    classroom = get_owned_classroom(teacher, classroom_id)
    classroom.is_archived = True
    classroom.save(update_fields=["is_archived", "updated_at"])
    ClassroomInvitation.objects.filter(
        classroom=classroom,
        status=ClassroomInvitation.STATUS_PENDING,
    ).update(status=ClassroomInvitation.STATUS_CANCELLED, updated_at=timezone.now())
    return classroom


@transaction.atomic
def delete_classroom(teacher: UserProfile, classroom_id: int) -> None:
    classroom = get_owned_classroom(teacher, classroom_id, include_archived=True)
    classroom.delete()


def get_owned_classroom(teacher: UserProfile, classroom_id: int, include_archived: bool = False) -> TeacherClassroom:
    classrooms = TeacherClassroom.objects.filter(teacher=teacher)
    if not include_archived:
        classrooms = classrooms.filter(is_archived=False)
    try:
        return classrooms.get(pk=classroom_id)
    except TeacherClassroom.DoesNotExist as exc:
        raise Http404("Classroom not found.") from exc


def get_classroom_detail(teacher: UserProfile, classroom_id: int) -> ClassroomDetail:
    classroom = get_owned_classroom(teacher, classroom_id)
    memberships = (
        ClassroomMembership.objects.filter(
            classroom=classroom,
            status=ClassroomMembership.STATUS_ACCEPTED,
        )
        .select_related("student")
        .order_by("student__username")
    )
    pending_invitations = (
        ClassroomInvitation.objects.filter(
            classroom=classroom,
            status=ClassroomInvitation.STATUS_PENDING,
        )
        .select_related("student", "invited_by")
        .order_by("-created_at")
    )
    return ClassroomDetail(classroom=classroom, memberships=memberships, pending_invitations=pending_invitations)


@transaction.atomic
def invite_student_by_username(
    teacher: UserProfile,
    classroom_id: int,
    username: str,
    message: str = "",
) -> InvitationResult:
    classroom = get_owned_classroom(teacher, classroom_id)
    if teacher.role != UserProfile.ROLE_TEACHER:
        raise PermissionDenied("Teacher access required.")

    normalized_username = username.strip()
    if not normalized_username:
        raise ValidationError("Enter a username to invite.")
    if normalized_username == teacher.username:
        raise ValidationError("Teachers cannot invite themselves as students.")

    try:
        student = UserProfile.objects.get(username=normalized_username)
    except UserProfile.DoesNotExist as exc:
        raise ValidationError("No user exists with that username.") from exc
    if student.role != UserProfile.ROLE_STUDENT:
        raise ValidationError("Only student accounts can be invited to a class.")

    if ClassroomMembership.objects.filter(
        classroom=classroom,
        student=student,
        status=ClassroomMembership.STATUS_ACCEPTED,
    ).exists():
        raise ValidationError("That student is already in this class.")

    pending_invitation = ClassroomInvitation.objects.select_for_update().filter(
        classroom=classroom,
        student=student,
        status=ClassroomInvitation.STATUS_PENDING,
    ).first()
    if pending_invitation:
        return InvitationResult(invitation=pending_invitation, created=False, code="already_pending")

    invitation = ClassroomInvitation.objects.create(
        classroom=classroom,
        invited_by=teacher,
        student=student,
        invited_username_snapshot=student.username,
        message=message,
    )
    return InvitationResult(invitation=invitation, created=True)


def list_pending_invitations_for_student(student: UserProfile) -> QuerySet[ClassroomInvitation]:
    return (
        ClassroomInvitation.objects.filter(
            student=student,
            status=ClassroomInvitation.STATUS_PENDING,
            classroom__is_archived=False,
        )
        .select_related("classroom", "classroom__teacher", "invited_by")
        .order_by("-created_at")
    )


def list_student_classrooms(student: UserProfile) -> QuerySet[ClassroomMembership]:
    return (
        ClassroomMembership.objects.filter(
            student=student,
            status=ClassroomMembership.STATUS_ACCEPTED,
            classroom__is_archived=False,
        )
        .select_related("classroom", "classroom__teacher")
        .order_by("classroom__name")
    )


@transaction.atomic
def accept_invitation(student: UserProfile, invitation_id: int) -> ClassroomMembership:
    if student.role != UserProfile.ROLE_STUDENT:
        raise PermissionDenied("Student access required.")
    try:
        invitation = ClassroomInvitation.objects.select_for_update().select_related("classroom").get(
            pk=invitation_id,
            student=student,
        )
    except ClassroomInvitation.DoesNotExist as exc:
        raise Http404("Invitation not found.") from exc

    if invitation.classroom.is_archived:
        raise ValidationError("This class is no longer accepting invitations.")
    if invitation.status == ClassroomInvitation.STATUS_ACCEPTED:
        membership = ClassroomMembership.objects.filter(
            classroom=invitation.classroom,
            student=student,
            status=ClassroomMembership.STATUS_ACCEPTED,
        ).first()
        if membership:
            return membership
    if invitation.status != ClassroomInvitation.STATUS_PENDING:
        raise ValidationError("This invitation is no longer pending.")

    membership, _created = ClassroomMembership.objects.update_or_create(
        classroom=invitation.classroom,
        student=student,
        defaults={
            "invitation": invitation,
            "status": ClassroomMembership.STATUS_ACCEPTED,
            "accepted_at": timezone.now(),
            "removed_at": None,
        },
    )
    invitation.mark_accepted()
    invitation.save(update_fields=["status", "responded_at", "updated_at"])
    return membership


@transaction.atomic
def decline_invitation(student: UserProfile, invitation_id: int) -> ClassroomInvitation:
    if student.role != UserProfile.ROLE_STUDENT:
        raise PermissionDenied("Student access required.")
    try:
        invitation = ClassroomInvitation.objects.select_for_update().get(pk=invitation_id, student=student)
    except ClassroomInvitation.DoesNotExist as exc:
        raise Http404("Invitation not found.") from exc

    if invitation.status != ClassroomInvitation.STATUS_PENDING:
        raise ValidationError("This invitation is no longer pending.")
    invitation.mark_declined()
    invitation.save(update_fields=["status", "responded_at", "updated_at"])
    return invitation


def get_student_membership_for_teacher(
    teacher: UserProfile,
    classroom_id: int,
    student_id: int,
) -> ClassroomMembership:
    classroom = get_owned_classroom(teacher, classroom_id)
    try:
        return ClassroomMembership.objects.select_related("student", "classroom").get(
            classroom=classroom,
            student__user_id=student_id,
            status=ClassroomMembership.STATUS_ACCEPTED,
        )
    except ClassroomMembership.DoesNotExist as exc:
        raise Http404("Student not found in this class.") from exc


@transaction.atomic
def revoke_invitation(teacher: UserProfile, classroom_id: int, invitation_id: int) -> ClassroomInvitation:
    classroom = get_owned_classroom(teacher, classroom_id)
    try:
        invitation = ClassroomInvitation.objects.select_for_update().get(
            pk=invitation_id,
            classroom=classroom,
            status=ClassroomInvitation.STATUS_PENDING,
        )
    except ClassroomInvitation.DoesNotExist as exc:
        raise Http404("Invitation not found.") from exc
    invitation.status = ClassroomInvitation.STATUS_REVOKED
    invitation.responded_at = timezone.now()
    invitation.save(update_fields=["status", "responded_at", "updated_at"])
    return invitation


@transaction.atomic
def remove_student_from_classroom(teacher: UserProfile, classroom_id: int, student_id: int) -> ClassroomMembership:
    membership = get_student_membership_for_teacher(teacher, classroom_id, student_id)
    membership.status = ClassroomMembership.STATUS_REMOVED
    membership.removed_at = timezone.now()
    membership.save(update_fields=["status", "removed_at", "updated_at"])
    return membership
