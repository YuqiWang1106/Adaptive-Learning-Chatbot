from django.contrib import admin

from .models import ClassroomInvitation, ClassroomMembership, TeacherClassroom


@admin.register(TeacherClassroom)
class TeacherClassroomAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "teacher", "subject", "is_archived", "updated_at")
    search_fields = ("name", "subject", "teacher__username", "teacher__email")
    list_filter = ("is_archived", "subject")
    readonly_fields = ("created_at", "updated_at")


@admin.register(ClassroomInvitation)
class ClassroomInvitationAdmin(admin.ModelAdmin):
    list_display = ("id", "classroom", "student", "invited_by", "status", "created_at", "responded_at")
    search_fields = ("classroom__name", "student__username", "invited_by__username", "invited_username_snapshot")
    list_filter = ("status",)
    readonly_fields = ("created_at", "updated_at", "responded_at")


@admin.register(ClassroomMembership)
class ClassroomMembershipAdmin(admin.ModelAdmin):
    list_display = ("id", "classroom", "student", "status", "accepted_at", "removed_at")
    search_fields = ("classroom__name", "student__username", "student__email")
    list_filter = ("status",)
    readonly_fields = ("created_at", "updated_at", "accepted_at", "removed_at")
