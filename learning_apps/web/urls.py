from django.urls import path

from . import health, public


urlpatterns = [
    path("", public.index, name="index"),
    path("about", public.about, name="about"),
    path("healthz", health.healthz, name="healthz"),
    path("readyz", health.readyz, name="readyz"),
]
