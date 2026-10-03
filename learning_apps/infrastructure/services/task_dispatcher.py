from __future__ import annotations

import logging
import threading
from typing import Callable

from celery import current_app
from django.conf import settings
from django.db import close_old_connections

logger = logging.getLogger(__name__)


def _run_local_fallback_inline() -> bool:
    configured = getattr(settings, "LEARNING_LOCAL_TASKS_INLINE", None)
    if configured is not None:
        return bool(configured)
    database = getattr(settings, "DATABASES", {}).get("default", {})
    return database.get("ENGINE") == "django.db.backends.sqlite3"


def dispatch_task(celery_task, *args, fallback: Callable[[], None] | None = None, **kwargs) -> bool:
    """
    Dispatch to Celery in production, with a local fallback for development.

    SQLite fallbacks run inline by default because a background writer can
    race the request thread and fail immediately with ``database is locked``.
    Server databases retain the non-blocking thread fallback.

    Returns whether Celery accepted the task.
    """
    if celery_task is not None and getattr(settings, "LEARNING_USE_CELERY", False):
        try:
            celery_task.apply_async(args=args, kwargs=kwargs)
            return True
        except Exception as exc:
            logger.error("Celery dispatch failed for %s: %s", getattr(celery_task, "name", celery_task), exc)

    if fallback is not None:
        def _run() -> None:
            close_old_connections()
            try:
                fallback()
            finally:
                close_old_connections()

        if _run_local_fallback_inline():
            _run()
        else:
            thread = threading.Thread(target=_run, daemon=True, name="local-ai-job-fallback")
            thread.start()
    return False


def dispatch_task_by_name(
    task_name: str,
    *args,
    fallback: Callable[[], None] | None = None,
    **kwargs,
) -> bool:
    """Dispatch without importing another product package's task adapter."""

    if getattr(settings, "LEARNING_USE_CELERY", False):
        try:
            current_app.send_task(str(task_name), args=args, kwargs=kwargs)
            return True
        except Exception as exc:
            logger.error("Celery dispatch failed for %s: %s", task_name, exc)

    return dispatch_task(None, fallback=fallback)
