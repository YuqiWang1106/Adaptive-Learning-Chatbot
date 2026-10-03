from __future__ import annotations

import hashlib
import hmac
import logging
import os
import re
import uuid
import zipfile
from dataclasses import dataclass
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any, Dict, List, Optional

from django.conf import settings
from django.core.files.storage import FileSystemStorage, default_storage
from django.db import IntegrityError, transaction
from django.db.models import Max
from django.utils import timezone
from openai import OpenAI

from learning_apps.infrastructure.services.task_dispatcher import dispatch_task_by_name as dispatch_task
from learning_apps.persistence import job_repository as ai_job_service
from learning_apps.persistence.models import (
    AIJob,
    KnowledgeChunk,
    LearningGoal,
    UploadedLearningMaterial,
    UserProfile,
)

logger = logging.getLogger(__name__)

ALLOWED_MATERIAL_EXTENSIONS = {".pdf", ".docx", ".pptx", ".txt", ".md"}
DEFAULT_MAX_UPLOAD_BYTES = 20 * 1024 * 1024
INGESTION_TASK_TYPE = "learning_material_ingestion"
DELETION_TASK_TYPE = "learning_material_deletion"
MAX_PROVIDER_ATTEMPTS = 3
PROCESSING_LEASE = timedelta(minutes=15)
TOMBSTONE_STATUSES = {
    UploadedLearningMaterial.STATUS_DELETING,
    UploadedLearningMaterial.STATUS_DELETE_FAILED,
    UploadedLearningMaterial.STATUS_DELETED,
}
DEFAULT_OFFICE_MAX_ENTRIES = 4096
DEFAULT_OFFICE_MAX_UNCOMPRESSED_BYTES = 200 * 1024 * 1024
DEFAULT_OFFICE_MAX_COMPRESSION_RATIO = 100.0

_MIME_TYPES = {
    ".pdf": {"application/pdf", "application/octet-stream"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/zip",
        "application/octet-stream",
    },
    ".pptx": {
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/zip",
        "application/octet-stream",
    },
    ".txt": {"text/plain", "application/octet-stream"},
    ".md": {"text/markdown", "text/plain", "application/octet-stream"},
}


class MaterialUploadError(ValueError):
    """Raised when a learning material request cannot be accepted safely."""


@dataclass(frozen=True)
class MaterialSearchResult:
    text: str
    # ``file_id`` is retained for callers, but now contains the safe source id,
    # never an OpenAI/provider identifier.
    file_id: str = ""
    filename: str = ""
    score: float = 0.0
    material_id: int = 0
    source_id: str = ""
    source_version: int = 1


def _client() -> OpenAI:
    if not settings.OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not configured.")
    return OpenAI(api_key=settings.OPENAI_API_KEY)


def _get_user(username: str) -> Optional[UserProfile]:
    return UserProfile.objects.filter(username=username).first()


def _get_goal(username: str, learning_goal_id: int) -> Optional[LearningGoal]:
    user = _get_user(username)
    if not user:
        return None
    return LearningGoal.objects.filter(user=user, id=int(learning_goal_id)).first()


def _safe_filename(name: str) -> str:
    base = os.path.basename(name or "learning-material.txt")
    base = re.sub(r"[^A-Za-z0-9._ -]+", "_", base).strip()
    return base[:180] or "learning-material.txt"


def _extension(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def _max_upload_bytes() -> int:
    return int(getattr(settings, "LEARNING_MATERIAL_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES))


def _rewind(uploaded_file) -> None:
    try:
        uploaded_file.seek(0)
    except (AttributeError, OSError) as exc:
        raise MaterialUploadError("Uploaded file cannot be read safely.") from exc


def _content_sha256(uploaded_file) -> str:
    digest = hashlib.sha256()
    _rewind(uploaded_file)
    chunks = getattr(uploaded_file, "chunks", None)
    if callable(chunks):
        for chunk in chunks():
            digest.update(chunk)
    else:
        while True:
            chunk = uploaded_file.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    _rewind(uploaded_file)
    return digest.hexdigest()


def _validate_content_type(uploaded_file, ext: str) -> None:
    supplied = str(getattr(uploaded_file, "content_type", "") or "").split(";", 1)[0].strip().lower()
    if supplied and supplied not in _MIME_TYPES[ext]:
        raise MaterialUploadError("File content type does not match its extension.")


def _validate_office_archive(archive: zipfile.ZipFile) -> set[str]:
    infos = archive.infolist()
    max_entries = int(
        getattr(settings, "LEARNING_MATERIAL_OFFICE_MAX_ENTRIES", DEFAULT_OFFICE_MAX_ENTRIES)
    )
    if len(infos) > max_entries:
        raise MaterialUploadError("Office document contains too many archive entries.")

    total_size = 0
    total_compressed = 0
    names: set[str] = set()
    for info in infos:
        normalized = str(info.filename or "").replace("\\", "/")
        path = PurePosixPath(normalized)
        if (
            not normalized
            or normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or ".." in path.parts
        ):
            raise MaterialUploadError("Office document contains an unsafe archive path.")
        if info.flag_bits & 0x1:
            raise MaterialUploadError("Encrypted Office archives are not supported.")
        names.add(normalized)
        total_size += max(0, int(info.file_size or 0))
        total_compressed += max(0, int(info.compress_size or 0))

    max_uncompressed = int(
        getattr(
            settings,
            "LEARNING_MATERIAL_OFFICE_MAX_UNCOMPRESSED_BYTES",
            DEFAULT_OFFICE_MAX_UNCOMPRESSED_BYTES,
        )
    )
    if total_size > max_uncompressed:
        raise MaterialUploadError("Office document expands beyond the safe size limit.")
    max_ratio = float(
        getattr(
            settings,
            "LEARNING_MATERIAL_OFFICE_MAX_COMPRESSION_RATIO",
            DEFAULT_OFFICE_MAX_COMPRESSION_RATIO,
        )
    )
    ratio = total_size / max(1, total_compressed)
    if ratio > max_ratio:
        raise MaterialUploadError("Office document compression ratio is unsafe.")
    return names


def _content_signature(uploaded_file, ext: str) -> str:
    """Validate bytes, not only the attacker-controlled name and MIME header."""
    _rewind(uploaded_file)
    try:
        if ext == ".pdf":
            prefix = uploaded_file.read(1024)
            if b"%PDF-" not in prefix:
                raise MaterialUploadError("PDF signature is invalid.")
            return "pdf"

        if ext in {".docx", ".pptx"}:
            try:
                with zipfile.ZipFile(uploaded_file) as archive:
                    names = _validate_office_archive(archive)
            except (zipfile.BadZipFile, OSError) as exc:
                raise MaterialUploadError("Office document signature is invalid.") from exc
            required = "word/document.xml" if ext == ".docx" else "ppt/presentation.xml"
            if "[Content_Types].xml" not in names or required not in names:
                raise MaterialUploadError("Office document type does not match its extension.")
            return "office_open_xml"

        prefix = uploaded_file.read(min(int(getattr(uploaded_file, "size", 0) or 0), 64 * 1024))
        if b"\x00" in prefix:
            raise MaterialUploadError("Text material contains binary data.")
        try:
            prefix.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise MaterialUploadError("Text material must use UTF-8 encoding.") from exc
        return "utf8_text"
    finally:
        _rewind(uploaded_file)


def validate_material_file(uploaded_file) -> tuple[str, str]:
    filename = _safe_filename(getattr(uploaded_file, "name", "") or "")
    ext = _extension(filename)
    if ext not in ALLOWED_MATERIAL_EXTENSIONS:
        allowed = ", ".join(sorted(ALLOWED_MATERIAL_EXTENSIONS))
        raise MaterialUploadError(f"Unsupported file type. Allowed: {allowed}.")
    size = int(getattr(uploaded_file, "size", 0) or 0)
    if size <= 0:
        raise MaterialUploadError("Uploaded file is empty.")
    if size > _max_upload_bytes():
        limit_mb = _max_upload_bytes() / (1024 * 1024)
        raise MaterialUploadError(f"File is too large. Maximum size is {limit_mb:.0f} MB.")
    _validate_content_type(uploaded_file, ext)
    _content_signature(uploaded_file, ext)
    return filename, ext


def _canonical_source_key(filename: str) -> str:
    normalized = re.sub(r"\s+", " ", filename.strip().casefold())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()[:32]


def _provider_scope_id(goal: LearningGoal) -> str:
    """Build a deterministic opaque scope without sending user identifiers."""
    secret = str(settings.SECRET_KEY).encode("utf-8")
    local_scope = f"material-scope:v1:{goal.user_id}:{goal.id}".encode("utf-8")
    return hmac.new(secret, local_scope, hashlib.sha256).hexdigest()[:32]


def material_storage_readiness() -> tuple[bool, str]:
    """Report whether async workers can read uploaded source bytes."""
    cross_process = bool(getattr(settings, "LEARNING_USE_CELERY", False)) and not bool(
        getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False)
    )
    if not cross_process:
        return True, "not_required"
    configured_shared = settings.LEARNING_MATERIAL_STORAGE_SHARED
    explicitly_shared = (
        configured_shared
        if isinstance(configured_shared, bool)
        else str(configured_shared).strip().lower() in {"1", "true", "yes", "on"}
    )
    if isinstance(default_storage, FileSystemStorage) and not explicitly_shared:
        return False, "local_material_filesystem_not_shared"
    return True, ""


def _validate_async_storage_contract() -> None:
    """Fail closed when a separate worker cannot be assumed to see source bytes.

    A plain FileSystemStorage path is process-host local by default. Deployments
    using a genuinely shared filesystem must explicitly acknowledge that fact;
    object-storage backends satisfy the cross-process durability contract.
    """
    ready, _reason = material_storage_readiness()
    if not ready:
        raise MaterialUploadError(
            "Asynchronous material ingestion requires durable shared storage. "
            "Configure object storage or explicitly confirm a shared filesystem."
        )


def ensure_vector_store_for_goal(username: str, learning_goal_id: int) -> str:
    goal = _get_goal(username, learning_goal_id)
    if not goal:
        raise MaterialUploadError("Learning goal not found.")
    with transaction.atomic():
        goal = LearningGoal.objects.select_for_update().get(id=goal.id, user=goal.user)
        if goal.vector_store_id:
            return goal.vector_store_id
        if (
            goal.vector_store_status == LearningGoal.VECTOR_STORE_CREATING
            and goal.updated_at >= timezone.now() - PROCESSING_LEASE
        ):
            # Another worker owns the provider-side create. Retrying is safer
            # than creating an untracked duplicate vector store.
            raise RuntimeError("Vector store creation is already in progress.")
        creation_marker = f"creation_token:{uuid.uuid4().hex}"
        goal.vector_store_status = LearningGoal.VECTOR_STORE_CREATING
        goal.vector_store_error = creation_marker
        goal.save(update_fields=["vector_store_status", "vector_store_error", "updated_at"])
    try:
        client = _client()
        store = client.vector_stores.create(
            name=f"Learning Workflow Demo material scope {_provider_scope_id(goal)}",
            metadata={
                "app": "learning_demo",
                "scope_id": _provider_scope_id(goal),
            },
        )
        with transaction.atomic():
            goal = LearningGoal.objects.select_for_update().get(id=goal.id, user=goal.user)
            owns_creation = (
                goal.vector_store_status == LearningGoal.VECTOR_STORE_CREATING
                and goal.vector_store_error == creation_marker
            )
            if owns_creation:
                goal.vector_store_id = store.id
                goal.vector_store_status = LearningGoal.VECTOR_STORE_READY
                goal.vector_store_error = ""
                goal.save(update_fields=["vector_store_id", "vector_store_status", "vector_store_error", "updated_at"])
                current_store_id = store.id
            else:
                current_store_id = goal.vector_store_id
        if not owns_creation:
            try:
                _provider_delete(client.vector_stores.delete, store.id)
            except Exception:
                logger.exception("Could not remove vector store created by a superseded worker")
            if not current_store_id:
                raise RuntimeError("Vector store creation was superseded by a newer worker.")
        return current_store_id
    except Exception as exc:
        with transaction.atomic():
            goal = LearningGoal.objects.select_for_update().get(id=goal.id, user=goal.user)
            if (
                not goal.vector_store_id
                and goal.vector_store_status == LearningGoal.VECTOR_STORE_CREATING
                and goal.vector_store_error == creation_marker
            ):
                goal.vector_store_status = LearningGoal.VECTOR_STORE_FAILED
                goal.vector_store_error = str(exc)[:4000]
                goal.save(update_fields=["vector_store_status", "vector_store_error", "updated_at"])
        raise


def _public_error_for_status(status: str) -> str:
    if status == UploadedLearningMaterial.STATUS_FAILED:
        return "Indexing failed. Upload the same file again to retry."
    if status == UploadedLearningMaterial.STATUS_DELETE_FAILED:
        return "Provider cleanup is pending. Delete again to retry."
    return ""


def material_to_dict(material: UploadedLearningMaterial) -> Dict[str, Any]:
    """Public DTO: provider ids and raw provider errors are deliberately absent."""
    active_job_id = (
        material.deletion_job_id
        if material.status in {
            UploadedLearningMaterial.STATUS_DELETING,
            UploadedLearningMaterial.STATUS_DELETE_FAILED,
            UploadedLearningMaterial.STATUS_DELETED,
        }
        else material.ingestion_job_id
    )
    return {
        "id": material.id,
        "source_id": material.source_key,
        "source_version": material.source_version,
        "learning_goal_id": material.learning_goal_id,
        "filename": material.original_filename,
        "file_extension": material.file_extension,
        "file_size": material.file_size,
        "status": material.status,
        "content_verified": bool(material.content_signature and material.content_signature != "legacy_unverified"),
        "job_id": active_job_id,
        "error_message": _public_error_for_status(material.status),
        "deleted_at": material.deleted_at.isoformat() if material.deleted_at else "",
        "created_at": material.created_at.isoformat() if material.created_at else "",
        "updated_at": material.updated_at.isoformat() if material.updated_at else "",
    }


def _dispatch_ingestion_job(job_id: str, username: str, material_id: int) -> None:
    dispatch_task(
        "learning_goal.run_material_ingestion_job",
        job_id,
        username,
        material_id,
        fallback=lambda: run_material_ingestion_job(job_id, username, material_id),
    )


def _enqueue_ingestion(material: UploadedLearningMaterial, username: str) -> str:
    with transaction.atomic():
        locked = (
            UploadedLearningMaterial.objects.select_for_update()
            .filter(id=material.id, user__username=username)
            .first()
        )
        if not locked:
            raise MaterialUploadError("Learning material not found.")
        if locked.deleted_at or locked.status in {
            UploadedLearningMaterial.STATUS_DELETING,
            UploadedLearningMaterial.STATUS_DELETE_FAILED,
            UploadedLearningMaterial.STATUS_DELETED,
        }:
            raise MaterialUploadError("Deleted learning material cannot be indexed.")
        current_job = (
            AIJob.objects.filter(
                job_id=locked.ingestion_job_id,
                task_type=INGESTION_TASK_TYPE,
                username=username,
            ).first()
            if locked.ingestion_job_id
            else None
        )
        if (
            locked.status in {
                UploadedLearningMaterial.STATUS_QUEUED,
                UploadedLearningMaterial.STATUS_PROCESSING,
            }
            and current_job
            and current_job.status in {
                AIJob.STATUS_QUEUED,
                AIJob.STATUS_RUNNING,
                AIJob.STATUS_RETRYING,
            }
        ):
            return current_job.job_id

        idempotency_key = f"material:{locked.id}:ingest:{locked.ingestion_attempts + 1}"
        job = ai_job_service.create_job(
            task_type=INGESTION_TASK_TYPE,
            username=username,
            learning_goal_id=locked.learning_goal_id,
            idempotency_key=idempotency_key,
            stage="queued",
            percent=2,
            message="Material accepted and queued for indexing.",
            max_retries=MAX_PROVIDER_ATTEMPTS,
        )
        locked.status = UploadedLearningMaterial.STATUS_QUEUED
        locked.ingestion_job_id = job.job_id
        locked.error_message = ""
        if locked.content_signature != "legacy_unverified":
            locked.chunk_status = UploadedLearningMaterial.CHUNK_STATUS_QUEUED
            locked.chunk_error_code = ""
        locked.save(
            update_fields=[
                "status",
                "ingestion_job_id",
                "error_message",
                "chunk_status",
                "chunk_error_code",
                "updated_at",
            ]
        )
        transaction.on_commit(
            lambda: _dispatch_ingestion_job(job.job_id, username, locked.id)
        )
        return job.job_id


def upload_learning_material(username: str, learning_goal_id: int, uploaded_file) -> Dict[str, Any]:
    """Persist an immutable source version and enqueue provider indexing."""
    goal = _get_goal(username, learning_goal_id)
    if not goal:
        raise MaterialUploadError("Learning goal not found.")
    _validate_async_storage_contract()
    filename, ext = validate_material_file(uploaded_file)
    signature = _content_signature(uploaded_file, ext)
    content_sha = _content_sha256(uploaded_file)
    source_key = _canonical_source_key(filename)
    material: UploadedLearningMaterial
    created = False

    try:
        with transaction.atomic():
            locked_goal = LearningGoal.objects.select_for_update().get(id=goal.id, user=goal.user)
            existing = UploadedLearningMaterial.objects.filter(
                user=locked_goal.user,
                learning_goal=locked_goal,
                active_content_sha256=content_sha,
            ).first()
            if existing:
                material = existing
            else:
                current_version = (
                    UploadedLearningMaterial.objects.filter(
                        learning_goal=locked_goal,
                        source_key=source_key,
                    ).aggregate(max_version=Max("source_version"))["max_version"]
                    or 0
                )
                _rewind(uploaded_file)
                material = UploadedLearningMaterial.objects.create(
                    user=locked_goal.user,
                    learning_goal=locked_goal,
                    original_filename=filename,
                    file_extension=ext,
                    content_type=getattr(uploaded_file, "content_type", "") or "",
                    file_size=int(getattr(uploaded_file, "size", 0) or 0),
                    source_key=source_key,
                    source_version=int(current_version) + 1,
                    content_sha256=content_sha,
                    active_content_sha256=content_sha,
                    content_signature=signature,
                    chunk_status=UploadedLearningMaterial.CHUNK_STATUS_QUEUED,
                    source_file=uploaded_file,
                    status=UploadedLearningMaterial.STATUS_UPLOADED,
                )
                created = True
    except IntegrityError:
        material = UploadedLearningMaterial.objects.get(
            user=goal.user,
            learning_goal=goal,
            active_content_sha256=content_sha,
        )

    retryable = material.status in {
        UploadedLearningMaterial.STATUS_UPLOADED,
        UploadedLearningMaterial.STATUS_FAILED,
    }
    if retryable:
        _enqueue_ingestion(material, username)
        material.refresh_from_db()

    payload = material_to_dict(material)
    payload["idempotent_replay"] = not created
    return payload


def _mark_stale_material_job(job_id: str) -> None:
    ai_job_service.update_job(
        job_id,
        status=AIJob.STATUS_FAILED,
        stage="superseded",
        percent=100,
        message="This material job was superseded by a newer request.",
        error_code="material_job_superseded",
        error_message="Material job superseded.",
    )


def _reject_ingestion_material_write(material_id: int, job_id: str) -> None:
    current = UploadedLearningMaterial.objects.filter(id=material_id).only(
        "ingestion_job_id",
        "status",
        "deleted_at",
    ).first()
    if (
        current
        and current.ingestion_job_id == job_id
        and (current.deleted_at or current.status in TOMBSTONE_STATUSES)
    ):
        ai_job_service.update_job(
            job_id,
            status=AIJob.STATUS_FAILED,
            stage="cancelled",
            percent=100,
            message="Indexing was cancelled because the material was deleted.",
            error_code="material_deleted",
        )
        return
    _mark_stale_material_job(job_id)


def _mark_ingestion_failed(
    material: UploadedLearningMaterial,
    job_id: str,
    exc: Exception,
) -> bool:
    updated = UploadedLearningMaterial.objects.filter(
        id=material.id,
        ingestion_job_id=job_id,
        deleted_at__isnull=True,
    ).exclude(status__in=TOMBSTONE_STATUSES).update(
        status=UploadedLearningMaterial.STATUS_FAILED,
        error_message=str(exc)[:4000],
        processing_started_at=None,
        updated_at=timezone.now(),
    )
    if not updated:
        _reject_ingestion_material_write(material.id, job_id)
        return False
    terminal = material.ingestion_attempts >= MAX_PROVIDER_ATTEMPTS
    ai_job_service.update_job(
        job_id,
        status=AIJob.STATUS_FAILED if terminal else AIJob.STATUS_RETRYING,
        stage="failed" if terminal else "retrying",
        percent=100 if terminal else 35,
        message="Material indexing failed." if terminal else "Material indexing will be retried.",
        error_code="material_indexing_failed",
        error_message="Provider indexing failed.",
        retry_count=material.ingestion_attempts,
    )
    return not terminal


def _cancel_ingestion_for_tombstone(
    material: UploadedLearningMaterial,
    job_id: str,
    username: str,
) -> None:
    """Ensure any provider object created during a delete race is cleaned."""
    _enqueue_deletion(material, username)
    ai_job_service.update_job(
        job_id,
        status=AIJob.STATUS_FAILED,
        stage="cancelled",
        percent=100,
        message="Indexing was cancelled because the material was deleted.",
        error_code="material_deleted",
    )


def run_material_ingestion_job(job_id: str, username: str, material_id: int) -> None:
    """Idempotent worker entrypoint used by Celery and the local thread fallback."""
    with transaction.atomic():
        material = (
            UploadedLearningMaterial.objects.select_for_update()
            .filter(id=int(material_id), user__username=username)
            .first()
        )
        if not material:
            ai_job_service.update_job(
                job_id,
                status=AIJob.STATUS_FAILED,
                stage="rejected",
                percent=100,
                message="Material is unavailable.",
                error_code="material_not_found",
            )
            return
        if material.ingestion_job_id != job_id:
            _mark_stale_material_job(job_id)
            return
        if material.status == UploadedLearningMaterial.STATUS_READY:
            ai_job_service.update_job(
                job_id,
                status=AIJob.STATUS_SUCCEEDED,
                stage="ready",
                percent=100,
                message="Material is ready.",
                result={"material": material_to_dict(material)},
            )
            return
        if material.status in {
            UploadedLearningMaterial.STATUS_DELETING,
            UploadedLearningMaterial.STATUS_DELETE_FAILED,
            UploadedLearningMaterial.STATUS_DELETED,
        }:
            ai_job_service.update_job(
                job_id,
                status=AIJob.STATUS_FAILED,
                stage="cancelled",
                percent=100,
                message="Indexing was cancelled because the material was deleted.",
                error_code="material_deleted",
            )
            return
        if (
            material.status == UploadedLearningMaterial.STATUS_PROCESSING
            and material.processing_started_at
            and material.processing_started_at >= timezone.now() - PROCESSING_LEASE
        ):
            return
        if not material.source_file:
            exc = RuntimeError("Persisted source bytes are unavailable.")
            material.ingestion_attempts += 1
            _mark_ingestion_failed(material, job_id, exc)
            return
        material.status = UploadedLearningMaterial.STATUS_PROCESSING
        material.processing_started_at = timezone.now()
        material.ingestion_attempts += 1
        material.error_message = ""
        material.save(
            update_fields=[
                "status",
                "processing_started_at",
                "ingestion_attempts",
                "error_message",
                "updated_at",
            ]
        )

    ai_job_service.update_job(
        job_id,
        status=AIJob.STATUS_RUNNING,
        stage="indexing",
        percent=20,
        message="Indexing uploaded material.",
        retry_count=material.ingestion_attempts - 1,
    )
    try:
        from .chunk_extraction_service import (
            ChunkExtractionError,
            extract_and_persist_material_chunks,
        )

        try:
            extract_and_persist_material_chunks(material.id, username)
        except ChunkExtractionError as exc:
            material.refresh_from_db()
            updated = UploadedLearningMaterial.objects.filter(
                id=material.id,
                ingestion_job_id=job_id,
                deleted_at__isnull=True,
            ).exclude(status__in=TOMBSTONE_STATUSES).update(
                status=UploadedLearningMaterial.STATUS_FAILED,
                error_message=f"chunk_extraction:{exc.code}",
                processing_started_at=None,
                updated_at=timezone.now(),
            )
            if not updated:
                _reject_ingestion_material_write(material.id, job_id)
                return
            ai_job_service.update_job(
                job_id,
                status=AIJob.STATUS_FAILED,
                stage="chunk_extraction_failed",
                percent=100,
                message="Material text extraction failed.",
                error_code=exc.code,
                error_message="Material text extraction failed.",
                retry_count=material.ingestion_attempts - 1,
            )
            return

        vector_store_id = ensure_vector_store_for_goal(username, material.learning_goal_id)
        material.refresh_from_db()
        if material.ingestion_job_id != job_id:
            _mark_stale_material_job(job_id)
            return
        if material.deleted_at or material.status in {
            UploadedLearningMaterial.STATUS_DELETING,
            UploadedLearningMaterial.STATUS_DELETE_FAILED,
            UploadedLearningMaterial.STATUS_DELETED,
        }:
            _cancel_ingestion_for_tombstone(material, job_id, username)
            return
        client = _client()
        if not material.openai_file_id:
            material.source_file.open("rb")
            try:
                provider_file = client.files.create(
                    file=(
                        f"{material.source_key}{material.file_extension}",
                        material.source_file.file,
                        material.content_type or "application/octet-stream",
                    ),
                    purpose="assistants",
                )
            finally:
                material.source_file.close()
            with transaction.atomic():
                material = UploadedLearningMaterial.objects.select_for_update().get(id=material.id)
                stale_job = material.ingestion_job_id != job_id
                if not stale_job:
                    material.openai_file_id = provider_file.id
                    material.vector_store_id = vector_store_id
                    tombstoned = bool(material.deleted_at) or material.status in {
                        UploadedLearningMaterial.STATUS_DELETING,
                        UploadedLearningMaterial.STATUS_DELETE_FAILED,
                        UploadedLearningMaterial.STATUS_DELETED,
                    }
                    if tombstoned:
                        material.provider_cleanup_pending = True
                    material.save(
                        update_fields=[
                            "openai_file_id",
                            "vector_store_id",
                            "provider_cleanup_pending",
                            "updated_at",
                        ]
                    )
            if stale_job:
                try:
                    _provider_delete(client.files.delete, provider_file.id)
                except Exception:
                    logger.exception("Could not clean provider file created by stale ingestion job %s", job_id)
                _mark_stale_material_job(job_id)
                return
            if tombstoned:
                _cancel_ingestion_for_tombstone(material, job_id, username)
                return

        batch = client.vector_stores.file_batches.create_and_poll(
            vector_store_id=vector_store_id,
            file_ids=[material.openai_file_id],
            poll_interval_ms=1000,
            chunking_strategy={"type": "auto"},
        )
        if str(getattr(batch, "status", "")).lower() != "completed":
            raise RuntimeError(f"Provider indexing ended with status: {getattr(batch, 'status', 'unknown')}")
        with transaction.atomic():
            material = UploadedLearningMaterial.objects.select_for_update().get(id=material.id)
            stale_job = material.ingestion_job_id != job_id
            tombstoned = (not stale_job) and (
                bool(material.deleted_at) or material.status in {
                    UploadedLearningMaterial.STATUS_DELETING,
                    UploadedLearningMaterial.STATUS_DELETE_FAILED,
                    UploadedLearningMaterial.STATUS_DELETED,
                }
            )
            if not stale_job and not tombstoned:
                material.vector_store_batch_id = batch.id
                material.status = UploadedLearningMaterial.STATUS_READY
                material.error_message = ""
                material.processing_started_at = None
                material.save(
                    update_fields=[
                        "vector_store_batch_id",
                        "status",
                        "error_message",
                        "processing_started_at",
                        "updated_at",
                    ]
                )
        if stale_job:
            _mark_stale_material_job(job_id)
            return
        if tombstoned:
            _cancel_ingestion_for_tombstone(material, job_id, username)
            return
        ai_job_service.update_job(
            job_id,
            status=AIJob.STATUS_SUCCEEDED,
            stage="ready",
            percent=100,
            message="Material is ready.",
            result={"material": material_to_dict(material)},
            retry_count=material.ingestion_attempts - 1,
        )
    except Exception as exc:
        logger.exception("Material ingestion failed for material %s: %s", material.id, exc)
        material.refresh_from_db()
        if _mark_ingestion_failed(material, job_id, exc):
            raise


def _dispatch_deletion_job(job_id: str, username: str, material_id: int) -> None:
    dispatch_task(
        "learning_goal.run_material_deletion_job",
        job_id,
        username,
        material_id,
        fallback=lambda: run_material_deletion_job(job_id, username, material_id),
    )


def _enqueue_deletion(
    material: UploadedLearningMaterial,
    username: str,
) -> tuple[str, bool]:
    with transaction.atomic():
        locked = (
            UploadedLearningMaterial.objects.select_for_update()
            .filter(
                id=material.id,
                user__username=username,
                learning_goal_id=material.learning_goal_id,
            )
            .first()
        )
        if not locked:
            raise MaterialUploadError("Learning material not found.")
        if locked.status == UploadedLearningMaterial.STATUS_DELETED:
            return locked.deletion_job_id, True
        current_job = (
            AIJob.objects.filter(
                job_id=locked.deletion_job_id,
                task_type=DELETION_TASK_TYPE,
                username=username,
            ).first()
            if locked.deletion_job_id
            else None
        )
        if (
            locked.status == UploadedLearningMaterial.STATUS_DELETING
            and current_job
            and current_job.status in {
                AIJob.STATUS_QUEUED,
                AIJob.STATUS_RUNNING,
                AIJob.STATUS_RETRYING,
            }
        ):
            return current_job.job_id, True

        idempotency_key = f"material:{locked.id}:delete:{locked.cleanup_attempts + 1}"
        job = ai_job_service.create_job(
            task_type=DELETION_TASK_TYPE,
            username=username,
            learning_goal_id=locked.learning_goal_id,
            idempotency_key=idempotency_key,
            stage="queued",
            percent=2,
            message="Material tombstoned; provider cleanup queued.",
            max_retries=MAX_PROVIDER_ATTEMPTS,
        )
        locked.status = UploadedLearningMaterial.STATUS_DELETING
        locked.deletion_job_id = job.job_id
        locked.provider_cleanup_pending = True
        # Preserve the immutable evidence hash, but release the active dedupe
        # key in the same transaction that publishes the deletion job token.
        locked.active_content_sha256 = None
        locked.deleted_at = locked.deleted_at or timezone.now()
        locked.error_message = ""
        locked.save(
            update_fields=[
                "status",
                "deletion_job_id",
                "provider_cleanup_pending",
                "active_content_sha256",
                "deleted_at",
                "error_message",
                "updated_at",
            ]
        )
        now = timezone.now()
        KnowledgeChunk.objects.filter(
            material=locked,
            lifecycle__in={
                KnowledgeChunk.LIFECYCLE_ACTIVE,
                KnowledgeChunk.LIFECYCLE_SUPERSEDED,
            },
        ).update(
            lifecycle=KnowledgeChunk.LIFECYCLE_DELETED,
            deleted_at=now,
            updated_at=now,
        )
        transaction.on_commit(
            lambda: _dispatch_deletion_job(job.job_id, username, locked.id)
        )
        return job.job_id, False


def delete_learning_material(username: str, learning_goal_id: int, material_id: int) -> Dict[str, Any]:
    """Tombstone first; remote cleanup is idempotent and retryable."""
    material = UploadedLearningMaterial.objects.filter(
        id=int(material_id),
        user__username=username,
        learning_goal_id=int(learning_goal_id),
    ).first()
    if not material:
        raise MaterialUploadError("Learning material not found.")
    _job_id, replay = _enqueue_deletion(material, username)
    material.refresh_from_db()
    payload = material_to_dict(material)
    payload["idempotent_replay"] = replay
    return payload


def _provider_delete(callable_obj, *args, **kwargs) -> None:
    try:
        callable_obj(*args, **kwargs)
    except Exception as exc:
        if int(getattr(exc, "status_code", 0) or 0) == 404:
            return
        raise


def run_material_deletion_job(job_id: str, username: str, material_id: int) -> None:
    with transaction.atomic():
        material = (
            UploadedLearningMaterial.objects.select_for_update()
            .filter(id=int(material_id), user__username=username)
            .first()
        )
        if not material:
            ai_job_service.update_job(
                job_id,
                status=AIJob.STATUS_SUCCEEDED,
                stage="deleted",
                percent=100,
                message="Material was already removed.",
            )
            return
        if material.deletion_job_id != job_id:
            _mark_stale_material_job(job_id)
            return
        if material.status == UploadedLearningMaterial.STATUS_DELETED and not material.provider_cleanup_pending:
            ai_job_service.update_job(
                job_id,
                status=AIJob.STATUS_SUCCEEDED,
                stage="deleted",
                percent=100,
                message="Material is deleted.",
                result={"material": material_to_dict(material)},
            )
            return

        material.cleanup_attempts += 1
        material.status = UploadedLearningMaterial.STATUS_DELETING
        material.provider_cleanup_pending = True
        material.save(update_fields=["cleanup_attempts", "status", "provider_cleanup_pending", "updated_at"])
    ai_job_service.update_job(
        job_id,
        status=AIJob.STATUS_RUNNING,
        stage="provider_cleanup",
        percent=35,
        message="Removing indexed material.",
        retry_count=material.cleanup_attempts - 1,
    )
    try:
        if material.openai_file_id:
            client = _client()
            if material.vector_store_id:
                _provider_delete(
                    client.vector_stores.files.delete,
                    file_id=material.openai_file_id,
                    vector_store_id=material.vector_store_id,
                )
            _provider_delete(client.files.delete, material.openai_file_id)
        with transaction.atomic():
            current = UploadedLearningMaterial.objects.select_for_update().get(id=material.id)
            if current.deletion_job_id != job_id:
                stale_job = True
            else:
                stale_job = False
                if current.source_file:
                    current.source_file.delete(save=False)
                current.status = UploadedLearningMaterial.STATUS_DELETED
                current.provider_cleanup_pending = False
                current.error_message = ""
                current.processing_started_at = None
                current.source_file = ""
                current.save(
                    update_fields=[
                        "status",
                        "provider_cleanup_pending",
                        "error_message",
                        "processing_started_at",
                        "source_file",
                        "updated_at",
                    ]
                )
                material = current
        if stale_job:
            _mark_stale_material_job(job_id)
            return
        ai_job_service.update_job(
            job_id,
            status=AIJob.STATUS_SUCCEEDED,
            stage="deleted",
            percent=100,
            message="Material is deleted.",
            result={"material": material_to_dict(material)},
            retry_count=material.cleanup_attempts - 1,
        )
    except Exception as exc:
        logger.exception("Material provider cleanup failed for material %s: %s", material.id, exc)
        with transaction.atomic():
            current = UploadedLearningMaterial.objects.select_for_update().get(id=material.id)
            if current.deletion_job_id != job_id:
                stale_job = True
                terminal = True
            else:
                stale_job = False
                current.status = UploadedLearningMaterial.STATUS_DELETE_FAILED
                current.provider_cleanup_pending = True
                current.error_message = str(exc)[:4000]
                current.save(update_fields=["status", "provider_cleanup_pending", "error_message", "updated_at"])
                material = current
                terminal = material.cleanup_attempts >= MAX_PROVIDER_ATTEMPTS
        if stale_job:
            _mark_stale_material_job(job_id)
            return
        ai_job_service.update_job(
            job_id,
            status=AIJob.STATUS_FAILED if terminal else AIJob.STATUS_RETRYING,
            stage="failed" if terminal else "retrying",
            percent=100 if terminal else 60,
            message="Provider cleanup failed." if terminal else "Provider cleanup will be retried.",
            error_code="material_cleanup_failed",
            error_message="Provider cleanup failed.",
            retry_count=material.cleanup_attempts,
        )
        if not terminal:
            raise


def get_material_job(username: str, learning_goal_id: int, job_id: str) -> Optional[Dict[str, Any]]:
    job = (
        AIJob.objects.filter(
            job_id=job_id,
            username=username,
            learning_goal_id=int(learning_goal_id),
            task_type__in={INGESTION_TASK_TYPE, DELETION_TASK_TYPE},
        )
        .only(
            "job_id",
            "task_type",
            "status",
            "stage",
            "percent",
            "message",
            "result",
            "error_code",
            "created_at",
            "updated_at",
            "finished_at",
        )
        .first()
    )
    if not job:
        return None
    return {
        "job_id": job.job_id,
        "task_type": job.task_type,
        "state": job.status,
        "stage": job.stage,
        "percent": job.percent,
        "message": job.message,
        "result": job.result or {},
        "error": job.error_code,
        "created_at": job.created_at.isoformat() if job.created_at else "",
        "updated_at": job.updated_at.isoformat() if job.updated_at else "",
        "finished_at": job.finished_at.isoformat() if job.finished_at else "",
    }


def list_learning_materials(username: str, learning_goal_id: int) -> List[Dict[str, Any]]:
    goal = _get_goal(username, learning_goal_id)
    if not goal:
        return []
    materials = UploadedLearningMaterial.objects.filter(
        user=goal.user,
        learning_goal=goal,
    ).exclude(status=UploadedLearningMaterial.STATUS_DELETED)
    return [material_to_dict(material) for material in materials.order_by("-created_at", "-id")]


def get_ready_vector_store_id(username: str, learning_goal_id: int) -> str:
    goal = _get_goal(username, learning_goal_id)
    if not goal or not goal.vector_store_id:
        return ""
    has_ready_material = UploadedLearningMaterial.objects.filter(
        user=goal.user,
        learning_goal=goal,
        status=UploadedLearningMaterial.STATUS_READY,
        deleted_at__isnull=True,
    ).exists()
    return goal.vector_store_id if has_ready_material else ""


def build_file_search_tool(username: str, learning_goal_id: int, *, max_num_results: int = 4) -> Optional[Dict[str, Any]]:
    vector_store_id = get_ready_vector_store_id(username, learning_goal_id)
    if not vector_store_id:
        return None
    return {
        "type": "file_search",
        "vector_store_ids": [vector_store_id],
        "max_num_results": max(1, int(max_num_results)),
    }


def _dump_search_item(item: Any) -> Dict[str, Any]:
    if hasattr(item, "model_dump"):
        return item.model_dump()
    if isinstance(item, dict):
        return item
    return {}


def _extract_search_text(payload: Dict[str, Any]) -> str:
    parts = []
    for content in payload.get("content") or []:
        if isinstance(content, dict):
            text = content.get("text") or content.get("content") or ""
        else:
            text = getattr(content, "text", "") or getattr(content, "content", "")
        if text:
            parts.append(str(text).strip())
    if not parts and payload.get("text"):
        parts.append(str(payload.get("text")).strip())
    return "\n".join(part for part in parts if part).strip()


def search_learning_materials(
    username: str,
    learning_goal_id: int,
    query: str,
    *,
    max_results: int = 4,
) -> List[MaterialSearchResult]:
    goal = _get_goal(username, learning_goal_id)
    vector_store_id = get_ready_vector_store_id(username, learning_goal_id)
    if not goal or not vector_store_id or not (query or "").strip():
        return []
    ready_materials = UploadedLearningMaterial.objects.filter(
        user=goal.user,
        learning_goal=goal,
        status=UploadedLearningMaterial.STATUS_READY,
        deleted_at__isnull=True,
    )
    material_by_provider_id = {m.openai_file_id: m for m in ready_materials if m.openai_file_id}
    try:
        page = _client().vector_stores.search(
            vector_store_id,
            query=query,
            max_num_results=max(1, int(max_results)),
            rewrite_query=True,
            timeout=30,
        )
    except Exception as exc:
        logger.warning("Vector store search failed for goal %s: %s", learning_goal_id, exc)
        return []

    results: List[MaterialSearchResult] = []
    for item in getattr(page, "data", []) or []:
        payload = _dump_search_item(item)
        provider_file_id = str(payload.get("file_id") or payload.get("id") or "")
        material = material_by_provider_id.get(provider_file_id)
        # Fail closed: a provider result without an active owner+goal source
        # mapping must never enter the learner prompt.
        if not material:
            continue
        text = _extract_search_text(payload)
        if not text:
            continue
        results.append(
            MaterialSearchResult(
                text=text,
                file_id=material.source_key,
                filename=material.original_filename,
                score=float(payload.get("score") or 0.0),
                material_id=material.id,
                source_id=material.source_key,
                source_version=material.source_version,
            )
        )
    return results


def format_material_search_context(results: List[MaterialSearchResult]) -> str:
    if not results:
        return ""
    blocks = []
    for idx, result in enumerate(results, start=1):
        source = result.filename or result.source_id or "uploaded material"
        version = f"v{result.source_version}"
        score = f"{result.score:.3f}" if result.score else "n/a"
        blocks.append(f"[Uploaded Material {idx}: {source}, {version}, score={score}]\n{result.text}")
    return "\n\n".join(blocks)


__all__ = [
    "ALLOWED_MATERIAL_EXTENSIONS",
    "MaterialSearchResult",
    "MaterialUploadError",
    "build_file_search_tool",
    "delete_learning_material",
    "ensure_vector_store_for_goal",
    "format_material_search_context",
    "get_material_job",
    "get_ready_vector_store_id",
    "list_learning_materials",
    "material_to_dict",
    "run_material_deletion_job",
    "run_material_ingestion_job",
    "search_learning_materials",
    "upload_learning_material",
    "material_storage_readiness",
    "validate_material_file",
]
