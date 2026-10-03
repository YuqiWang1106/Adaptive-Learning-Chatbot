from __future__ import annotations

from .base import models

class UserProfile(models.Model):
    ROLE_STUDENT = "student"
    ROLE_TEACHER = "teacher"
    ROLE_CHOICES = [
        (ROLE_STUDENT, "Student"),
        (ROLE_TEACHER, "Teacher"),
    ]

    user_id = models.AutoField(primary_key=True)
    username = models.CharField(max_length=50, unique=True)
    password_hash = models.TextField()
    email = models.EmailField(max_length=255, unique=True)
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default=ROLE_STUDENT)
    age = models.IntegerField(default=0)
    academic_level = models.CharField(max_length=50, blank=True, default="")
    language = models.CharField(max_length=50, blank=True, default="")
    is_admin = models.BooleanField(default=False)
    preferences_completed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "users"
        indexes = [
            models.Index(fields=["role"], name="idx_users_role"),
            models.Index(fields=["is_admin"], name="idx_users_is_admin"),
            models.Index(fields=["preferences_completed"], name="idx_users_pref_done"),
        ]

    def __str__(self) -> str:
        """Return a human-readable string representation."""
        return self.username
