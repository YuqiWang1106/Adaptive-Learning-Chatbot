from django.urls import path

from . import views


urlpatterns = [
    path("learning-goal", views.learning_goal, name="learning_goal"),
    path("learning-goal/loading/<str:job_id>", views.learning_goal_loading, name="learning_goal_loading"),
    path("learning-goal/loading/<str:job_id>/status", views.learning_goal_creation_status, name="learning_goal_creation_status"),
    path("learning-goal/<int:learning_goal_id>/open", views.open_learning_goal, name="learning_goal_open"),
    path("learning-goal/<int:learning_goal_id>/materials", views.learning_goal_materials, name="learning_goal_materials"),
    path("learning-goal/<int:learning_goal_id>/materials/upload", views.learning_goal_material_upload, name="learning_goal_material_upload"),
    path("learning-goal/<int:learning_goal_id>/materials/jobs/<str:job_id>", views.learning_goal_material_job_status, name="learning_goal_material_job_status"),
    path("learning-goal/<int:learning_goal_id>/materials/<int:material_id>", views.learning_goal_material_delete, name="learning_goal_material_delete"),
]
