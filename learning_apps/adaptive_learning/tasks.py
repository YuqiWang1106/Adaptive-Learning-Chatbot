from __future__ import annotations

from celery import shared_task
from django.db import close_old_connections
from django.utils import timezone

from learning_apps.application.workflows.background import run_celery_workflow
from learning_apps.application.contracts import stable_sha256


@shared_task(bind=True, max_retries=2, name="adaptive_learning.grade_interaction")
def grade_adaptive_interaction(self, payload: dict) -> None:
    close_old_connections()
    try:
        safe_payload = payload or {}
        username = str(safe_payload.get("username") or "")
        learning_goal_id = int(safe_payload.get("learning_goal_id") or 0)
        run_celery_workflow(
            "jobs.adaptive.grade",
            {"payload": safe_payload},
            username=username,
            learning_goal_id=learning_goal_id,
            idempotency_key=f"adaptive-grade:{stable_sha256(safe_payload)[:72]}",
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** max(0, int(self.request.retries)))
    finally:
        close_old_connections()


@shared_task(bind=True, max_retries=2, name="adaptive_learning.create_probe_offer")
def create_adaptive_probe_offer(self, username: str, learning_goal_id: int) -> None:
    close_old_connections()
    try:
        run_celery_workflow(
            "jobs.probe.offer",
            {},
            username=username,
            learning_goal_id=int(learning_goal_id),
            idempotency_key=f"probe-offer:{username}:{int(learning_goal_id)}:{timezone.now().strftime('%Y%m%d%H')}"[:96],
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** max(0, int(self.request.retries)))
    finally:
        close_old_connections()


@shared_task(bind=True, max_retries=2, name="adaptive_learning.generate_probe_from_offer")
def generate_probe_from_offer_task(self, offer_id: str) -> None:
    close_old_connections()
    try:
        run_celery_workflow(
            "jobs.probe.generate_from_offer",
            {"offer_id": str(offer_id or "")},
            idempotency_key=f"probe-offer-generate:{str(offer_id or '')}"[:96],
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** max(0, int(self.request.retries)))
    finally:
        close_old_connections()


@shared_task(bind=True, max_retries=2, name="adaptive_learning.scan_reviews")
def scan_adaptive_reviews(self, limit: int = 100, cursor_id: int | None = None) -> dict:
    """Celery Beat entry point; due policy does not depend on a new chat."""

    close_old_connections()
    try:
        effective_cursor = int(cursor_id) if cursor_id is not None else None
        suffix = str(effective_cursor) if effective_cursor is not None else "scheduled"
        scan_window = timezone.now().strftime("%Y%m%d%H%M")
        return run_celery_workflow(
            "jobs.review.scan",
            {"limit": int(limit or 100), "cursor_id": effective_cursor},
            idempotency_key=(
                f"review-scan:{scan_window}:{suffix}:"
                f"{stable_sha256({'limit': int(limit or 100), 'cursor': effective_cursor})[:32]}"
            )[:96],
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** max(0, int(self.request.retries)))
    finally:
        close_old_connections()
