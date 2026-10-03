"""URL configuration for the Django migration project."""
from django.contrib import admin
from django.urls import include, path

urlpatterns = [
    path("django-admin/", admin.site.urls),
    path("api/v2/", include("learning_apps.adaptive_agent.urls")),
    path("", include("learning_apps.web.urls")),
    path("auth/", include("learning_apps.accounts.urls")),
    path("", include("learning_apps.learning_goal.urls")),
    path("", include("learning_apps.self_assessment.urls")),
    path("", include("learning_apps.chat.urls")),
    path("", include("learning_apps.adaptive_learning.urls")),
    path("", include("learning_apps.teacher_portal.urls")),
]
