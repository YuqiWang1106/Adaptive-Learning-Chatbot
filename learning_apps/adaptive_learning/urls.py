from django.urls import path

from . import views


urlpatterns = [
    path("learning-goal/<int:learning_goal_id>/adaptive-progress", views.learning_goal_adaptive_progress, name="learning_goal_adaptive_progress"),
    path("learning-goal/<int:learning_goal_id>/dashboard", views.learning_goal_dashboard, name="learning_goal_dashboard"),
    path("learning-goal/<int:learning_goal_id>/adaptive-probe/<int:probe_id>/submit", views.learning_goal_adaptive_probe_submit, name="learning_goal_adaptive_probe_submit"),
    path("learning-goal/<int:learning_goal_id>/adaptive-probe/<int:probe_id>/skip", views.learning_goal_adaptive_probe_skip, name="learning_goal_adaptive_probe_skip"),
]
