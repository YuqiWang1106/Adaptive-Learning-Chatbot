"""Application entrypoints for asynchronous learning-material lifecycles.

The knowledge package owns material validation, persistence and provider work.
This module is the application-facing seam used by HTTP capabilities and Celery
workers, so callers do not reach into the knowledge implementation directly.
"""

from learning_apps.knowledge.services.material_rag_service import (
    MaterialUploadError,
    delete_learning_material,
    get_material_job,
    list_learning_materials,
    run_material_deletion_job,
    run_material_ingestion_job,
    upload_learning_material,
)

__all__ = [
    "MaterialUploadError",
    "delete_learning_material",
    "get_material_job",
    "list_learning_materials",
    "run_material_deletion_job",
    "run_material_ingestion_job",
    "upload_learning_material",
]
