from __future__ import annotations

from typing import Any

from django.core.files.uploadedfile import UploadedFile

from learning_apps.knowledge.services.material_rag_service import MaterialUploadError

from .base import ProductWorkflowError, execute_capability


def list_learning_materials(username: str, learning_goal_id: int) -> list[dict[str, Any]]:
    return list(
        execute_capability(
            "materials.list",
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="materials.list",
        ).get("materials", [])
    )


def upload_learning_material(username: str, learning_goal_id: int, uploaded_file: UploadedFile) -> dict[str, Any]:
    try:
        return execute_capability(
            "materials.upload",
            {
                "filename": str(getattr(uploaded_file, "name", "upload")),
                "content_type": str(getattr(uploaded_file, "content_type", "") or ""),
                "size": int(getattr(uploaded_file, "size", 0) or 0),
            },
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="materials.upload",
            metadata={"uploaded_file": uploaded_file},
        )["material"]
    except ProductWorkflowError as exc:
        if exc.code in {"goal_scope_fenced", "material_upload_error", "uploaded_file_required"}:
            raise MaterialUploadError(exc.message or exc.code) from exc
        raise


def get_material_job(username: str, learning_goal_id: int, job_id: str) -> dict[str, Any] | None:
    try:
        return execute_capability(
            "materials.job_status",
            {"job_id": job_id},
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="materials.job_status",
        ).get("job")
    except ProductWorkflowError as exc:
        if exc.code == "goal_scope_fenced":
            return None
        raise


def delete_learning_material(username: str, learning_goal_id: int, material_id: int) -> dict[str, Any]:
    try:
        return execute_capability(
            "materials.delete",
            {"material_id": material_id},
            username=username,
            learning_goal_id=learning_goal_id,
            workflow="materials.delete",
        )["material"]
    except ProductWorkflowError as exc:
        if exc.code in {"goal_scope_fenced", "material_not_found"}:
            raise MaterialUploadError(exc.code) from exc
        raise
