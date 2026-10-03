from __future__ import annotations

from django.db import models
from django.utils import timezone

from learning_apps.persistence.models import UserProfile


class TeacherClassroom(models.Model):
    teacher = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="owned_teacher_classrooms",
        db_column="teacher_user_id",
    )
    name = models.CharField(max_length=120)
    subject = models.CharField(max_length=120, blank=True, default="")
    term = models.CharField(max_length=80, blank=True, default="")
    description = models.TextField(blank=True, default="")
    is_archived = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "teacher_classrooms"
        ordering = ["-updated_at", "-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["teacher", "name"], name="uix_tp_class_teacher_name"),
        ]
        indexes = [
            models.Index(fields=["teacher", "is_archived"], name="idx_tp_class_teacher_active"),
            models.Index(fields=["updated_at"], name="idx_tp_class_updated"),
        ]

    def __str__(self) -> str:
        return self.name


class ClassroomInvitation(models.Model):
    STATUS_PENDING = "pending"
    STATUS_ACCEPTED = "accepted"
    STATUS_DECLINED = "declined"
    STATUS_CANCELLED = "cancelled"
    STATUS_REVOKED = "revoked"

    STATUS_CHOICES = [
        (STATUS_PENDING, "Pending"),
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_DECLINED, "Declined"),
        (STATUS_CANCELLED, "Cancelled"),
        (STATUS_REVOKED, "Revoked"),
    ]

    classroom = models.ForeignKey(
        TeacherClassroom,
        on_delete=models.CASCADE,
        related_name="invitations",
        db_column="classroom_id",
    )
    invited_by = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="sent_classroom_invitations",
        db_column="invited_by_user_id",
    )
    student = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="received_classroom_invitations",
        db_column="student_user_id",
    )
    invited_username_snapshot = models.CharField(max_length=50)
    message = models.TextField(blank=True, default="")
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_PENDING)
    responded_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "classroom_invitations"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["classroom", "status"], name="idx_tp_inv_class_status"),
            models.Index(fields=["student", "status"], name="idx_tp_inv_student_status"),
            models.Index(fields=["invited_by", "created_at"], name="idx_tp_inv_teacher_time"),
        ]

    def mark_accepted(self) -> None:
        self.status = self.STATUS_ACCEPTED
        self.responded_at = timezone.now()

    def mark_declined(self) -> None:
        self.status = self.STATUS_DECLINED
        self.responded_at = timezone.now()

    def __str__(self) -> str:
        return f"{self.classroom.name} -> {self.invited_username_snapshot} ({self.status})"


class ClassroomMembership(models.Model):
    STATUS_ACCEPTED = "accepted"
    STATUS_REMOVED = "removed"

    STATUS_CHOICES = [
        (STATUS_ACCEPTED, "Accepted"),
        (STATUS_REMOVED, "Removed"),
    ]

    classroom = models.ForeignKey(
        TeacherClassroom,
        on_delete=models.CASCADE,
        related_name="memberships",
        db_column="classroom_id",
    )
    student = models.ForeignKey(
        UserProfile,
        on_delete=models.CASCADE,
        related_name="classroom_memberships",
        db_column="student_user_id",
    )
    invitation = models.ForeignKey(
        ClassroomInvitation,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="memberships",
        db_column="invitation_id",
    )
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_ACCEPTED)
    accepted_at = models.DateTimeField(default=timezone.now)
    removed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "classroom_memberships"
        constraints = [
            models.UniqueConstraint(fields=["classroom", "student"], name="uix_tp_membership_class_student"),
        ]
        indexes = [
            models.Index(fields=["classroom", "status"], name="idx_tp_member_class_status"),
            models.Index(fields=["student", "status"], name="idx_tp_member_student_status"),
        ]

    def __str__(self) -> str:
        return f"{self.student.username} in {self.classroom.name} ({self.status})"
