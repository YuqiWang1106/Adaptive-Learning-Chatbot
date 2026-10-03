from django.urls import path

from . import views


urlpatterns = [
    path("self-assessment", views.self_assessment, name="self_assessment"),
    path(
        "self-assessment/target-scope/prepare",
        views.self_assessment_target_scope_prepare,
        name="self_assessment_target_scope_prepare",
    ),
    path(
        "self-assessment/target-scope/<str:job_id>/status",
        views.self_assessment_target_scope_status,
        name="self_assessment_target_scope_status",
    ),
    path("self-assessment/loading/<str:job_id>", views.self_assessment_loading, name="self_assessment_loading"),
    path("self-assessment/loading/<str:job_id>/status", views.self_assessment_submission_status, name="self_assessment_submission_status"),
    path(
        "self-assessment/result/<int:assessment_id>",
        views.self_assessment_result,
        name="self_assessment_result",
    ),
]
