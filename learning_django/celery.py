from __future__ import annotations

import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "learning_django.settings")

app = Celery("learning_django")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
app.conf.imports = (
    "learning_apps.adaptive_agent.tasks",
    "learning_apps.adaptive_learning.tasks",
    "learning_apps.chat.tasks",
    "learning_apps.learning_goal.tasks",
    "learning_apps.self_assessment.tasks",
)
