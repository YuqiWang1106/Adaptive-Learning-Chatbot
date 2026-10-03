from __future__ import annotations

from celery import shared_task

from learning_apps.application.workflows.background import run_celery_workflow

@shared_task(bind=True, max_retries=3, name="chat.reconcile_conversation_cleanup")
def reconcile_conversation_cleanup_task(self, cleanup_limit: int = 100, retention_limit: int = 500) -> dict:
    try:
        return run_celery_workflow(
            "jobs.conversation.cleanup",
            {
                "cleanup_limit": cleanup_limit,
                "retention_limit": retention_limit,
            },
            idempotency_key=f"conversation-cleanup:{self.request.id or 'local'}",
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** max(0, int(self.request.retries)))


@shared_task(bind=True, max_retries=2, name="chat.expire_learner_memories")
def expire_learner_memories_task(self, limit: int = 500) -> dict:
    try:
        return run_celery_workflow(
            "jobs.memory.expire",
            {"limit": limit},
            idempotency_key=f"memory-expire:{self.request.id or 'local'}",
        )
    except Exception as exc:
        raise self.retry(exc=exc, countdown=2 ** max(0, int(self.request.retries)))
