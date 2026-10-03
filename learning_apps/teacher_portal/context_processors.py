from __future__ import annotations

from learning_apps.application.workflows.teacher import get_student_invitation_notification_count


def class_invitation_notifications(request):
    if not getattr(request, "user", None) or not request.user.is_authenticated:
        return {}
    return {
        "student_pending_class_invitation_count": get_student_invitation_notification_count(
            request.user.username
        )
    }
