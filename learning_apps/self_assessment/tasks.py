from __future__ import annotations

from celery import shared_task

from learning_apps.application.workflows.background import run_celery_workflow


@shared_task(bind=True, max_retries=3, name="self_assessment.run_submission_job")
def run_self_assessment_submission_job(
    self,
    job_id: str,
    username: str,
    post_data: dict,
    goal_context_data: dict,
) -> None:
    run_celery_workflow(
        "jobs.assessment.evaluate",
        {
            "job_id": job_id,
            "post_data": post_data,
            "goal_context": goal_context_data,
        },
        username=username,
        idempotency_key=f"assessment:{job_id}",
    )


@shared_task(bind=True, max_retries=2, name="self_assessment.run_target_scope_job")
def run_target_scope_job(
    self,
    job_id: str,
    username: str,
    target_task: str,
    goal_context_data: dict,
) -> None:
    run_celery_workflow(
        "jobs.assessment.prepare_target_scope",
        {
            "job_id": job_id,
            "target_task": target_task,
            "goal_context": goal_context_data,
        },
        username=username,
        idempotency_key=f"assessment-scope:{job_id}",
    )
