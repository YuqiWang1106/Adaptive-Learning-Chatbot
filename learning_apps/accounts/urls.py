from django.urls import path
from . import views

urlpatterns = [
    path("login", views.login_view, name="login"),
    path("login/google", views.google_login, name="google_login"),
    path("google/callback", views.google_callback, name="google_callback"),
    path("logout", views.logout_view, name="logout"),
    path("register", views.register_view, name="register"),
    path("setup-preferences", views.setup_preferences, name="setup_preferences"),
]
