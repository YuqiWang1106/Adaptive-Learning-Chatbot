from __future__ import annotations

from celery import shared_task
from django.conf import settings

from .runtime import execute_agent_run
from .run_service import expire_stale_agent_runs, recover_stale_agent_runs


@shared_task(
    bind=True,
    max_retries=0,
    soft_time_limit=settings.LEARNING_AGENT_TIMEOUT_SECONDS + 5,
    time_limit=settings.LEARNING_AGENT_TIMEOUT_SECONDS + 15,
    name="adaptive_agent.execute_run",
)
def execute_learning_agent_run(self, run_id: str) -> None:
    execute_agent_run(run_id)


@shared_task(name="adaptive_agent.expire_stale_runs")
def expire_stale_learning_agent_runs(limit: int = 200) -> int:
    return expire_stale_agent_runs(limit=limit)


@shared_task(name="adaptive_agent.recover_stale_runs")
def recover_stale_learning_agent_runs(limit: int = 100) -> int:
    return recover_stale_agent_runs(limit=limit)
