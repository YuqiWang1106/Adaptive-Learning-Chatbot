from django.urls import path

from . import views

urlpatterns = [
    path("teacher/dashboard", views.teacher_dashboard, name="teacher_dashboard"),
    path("teacher/classes/<int:classroom_id>", views.classroom_detail, name="teacher_classroom_detail"),
    path("teacher/classes/<int:classroom_id>/edit", views.classroom_edit, name="teacher_classroom_edit"),
    path("teacher/classes/<int:classroom_id>/archive", views.classroom_archive, name="teacher_classroom_archive"),
    path("teacher/classes/<int:classroom_id>/delete", views.classroom_delete, name="teacher_classroom_delete"),
    path(
        "teacher/classes/<int:classroom_id>/invitations/<int:invitation_id>/revoke",
        views.classroom_invitation_revoke,
        name="teacher_invitation_revoke",
    ),
    path(
        "teacher/classes/<int:classroom_id>/students/<int:student_id>/remove",
        views.classroom_remove_student,
        name="teacher_remove_student",
    ),
    path(
        "teacher/classes/<int:classroom_id>/students/<int:student_id>/report",
        views.student_report,
        name="teacher_student_report",
    ),
    path(
        "teacher/classes/<int:classroom_id>/students/<int:student_id>/goals/<int:learning_goal_id>",
        views.student_goal_report,
        name="teacher_student_goal_report",
    ),
    path("student/classes", views.student_classrooms, name="student_classrooms"),
    path("student/invitations/<int:invitation_id>/accept", views.student_invitation_accept, name="student_invitation_accept"),
    path("student/invitations/<int:invitation_id>/decline", views.student_invitation_decline, name="student_invitation_decline"),
]
