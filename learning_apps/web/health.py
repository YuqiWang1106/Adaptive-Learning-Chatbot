from __future__ import annotations

from django.conf import settings
from django.db import connections
from django.db.migrations.executor import MigrationExecutor
from django.http import JsonResponse


def _probe_database() -> tuple[bool, str]:
    try:
        connection = connections["default"]
        connection.ensure_connection()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        return True, ""
    except Exception as exc:  # pragma: no cover - defensive dependency probe
        return False, str(exc)


def _probe_redis() -> tuple[bool, str]:
    redis_url = str(getattr(settings, "REDIS_URL", "") or "").strip()
    if not redis_url:
        return True, "not_configured"
    try:
        from redis import Redis

        client = Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        pong = bool(client.ping())
        return pong, "" if pong else "redis_ping_false"
    except Exception as exc:  # pragma: no cover - defensive dependency probe
        return False, str(exc)


def _probe_celery_broker() -> tuple[bool, str]:
    broker_url = str(getattr(settings, "CELERY_BROKER_URL", "") or "").strip()
    if not broker_url or broker_url == "memory://":
        return True, "memory_broker"
    try:
        from kombu import Connection

        connection = Connection(broker_url, connect_timeout=1)
        connection.ensure_connection(max_retries=1)
        connection.release()
        return True, ""
    except Exception as exc:  # pragma: no cover - defensive dependency probe
        return False, str(exc)


def _probe_celery_workers() -> tuple[bool, str]:
    if not bool(getattr(settings, "LEARNING_USE_CELERY", False)):
        return True, "disabled"
    try:
        from learning_django.celery import app

        timeout = float(getattr(settings, "LEARNING_CELERY_WORKER_HEALTHCHECK_TIMEOUT_SECONDS", 1.0))
        replies = app.control.inspect(timeout=max(0.1, timeout)).ping() or {}
        if replies:
            return True, ""
        return False, "no_celery_workers"
    except Exception as exc:  # pragma: no cover - defensive dependency probe
        return False, str(exc)


def _probe_material_storage() -> tuple[bool, str]:
    try:
        from learning_apps.knowledge.services.material_rag_service import material_storage_readiness

        return material_storage_readiness()
    except Exception as exc:  # pragma: no cover - defensive dependency probe
        return False, str(exc)


def _probe_migrations() -> tuple[bool, str]:
    try:
        executor = MigrationExecutor(connections["default"])
        plan = executor.migration_plan(executor.loader.graph.leaf_nodes())
        if not plan:
            return True, ""
        pending = [f"{migration.app_label}.{migration.name}" for migration, _ in plan[:8]]
        return False, "pending_migrations: " + ", ".join(pending) + (" ..." if len(plan) > 8 else "")
    except Exception as exc:  # pragma: no cover - defensive dependency probe
        return False, str(exc)


def healthz(request):
    return JsonResponse({"status": "ok", "alive": True})


def readyz(request):
    checks = {
        "db": _probe_database(),
        "redis": _probe_redis(),
        "celery_broker": _probe_celery_broker(),
        "celery_workers": _probe_celery_workers(),
        "material_storage": _probe_material_storage(),
        "migrations": _probe_migrations(),
    }
    ready = all(result[0] for result in checks.values())
    return JsonResponse(
        {
            "status": "ready" if ready else "not_ready",
            "db_ok": checks["db"][0],
            "redis_ok": checks["redis"][0],
            "celery_broker_ok": checks["celery_broker"][0],
            "celery_workers_ok": checks["celery_workers"][0],
            "material_storage_ok": checks["material_storage"][0],
            "migrations_ok": checks["migrations"][0],
            "errors": {name: result[1] for name, result in checks.items()},
        },
        status=200 if ready else 503,
    )
