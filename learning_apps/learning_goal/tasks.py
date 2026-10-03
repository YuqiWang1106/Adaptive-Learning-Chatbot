from __future__ import annotations

from celery import shared_task

from learning_apps.application.workflows.background import run_celery_workflow


@shared_task(bind=True, max_retries=3, name="learning_goal.run_creation_job")
def run_learning_goal_creation_job(self, job_id: str, username: str, raw_preference_text: str) -> None:
    run_celery_workflow(
        "jobs.learning_goal.create",
        {"job_id": job_id, "raw_preference_text": raw_preference_text},
        username=username,
        idempotency_key=f"learning-goal-create:{job_id}",
    )


@shared_task(bind=True, max_retries=3, name="learning_goal.run_concept_map_job")
def run_learning_goal_concept_map_job(
    self,
    job_id: str,
    username: str,
    learning_goal_id: int,
    force_refresh: bool = False,
) -> None:
    run_celery_workflow(
        "jobs.concept_map.generate",
        {"job_id": job_id, "force_refresh": force_refresh},
        username=username,
        learning_goal_id=learning_goal_id,
        idempotency_key=f"concept-map:{job_id}",
    )


@shared_task(bind=True, max_retries=1, name="learning_goal.run_title_refine_job")
def run_learning_goal_title_refine_job(
    self,
    username: str,
    learning_goal_id: int,
) -> None:
    run_celery_workflow(
        "jobs.learning_goal.refine_title",
        username=username,
        learning_goal_id=int(learning_goal_id),
        idempotency_key=f"title-refine:{username}:{int(learning_goal_id)}",
    )


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    name="learning_goal.run_material_ingestion_job",
)
def run_learning_material_ingestion_job(
    self,
    job_id: str,
    username: str,
    material_id: int,
) -> None:
    run_celery_workflow(
        "jobs.material.ingest",
        {"job_id": job_id, "material_id": int(material_id)},
        username=username,
        metadata={"material_id": int(material_id)},
        idempotency_key=f"material-ingest:{job_id}",
    )


@shared_task(
    bind=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 2},
    name="learning_goal.run_material_deletion_job",
)
def run_learning_material_deletion_job(
    self,
    job_id: str,
    username: str,
    material_id: int,
) -> None:
    run_celery_workflow(
        "jobs.material.delete",
        {"job_id": job_id, "material_id": int(material_id)},
        username=username,
        metadata={"material_id": int(material_id)},
        idempotency_key=f"material-delete:{job_id}",
    )
