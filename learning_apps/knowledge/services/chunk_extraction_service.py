"""Deterministic, owner-scoped extraction of untrusted learning-material chunks."""

from __future__ import annotations

import hashlib
import logging
import re
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from typing import Iterable

from django.conf import settings
from django.db import transaction
from django.db.models import Max
from django.utils import timezone

from learning_apps.persistence.models import KnowledgeChunk, UploadedLearningMaterial

logger = logging.getLogger(__name__)

CHUNK_TRANSFORM_VERSION = "p2.2-extract-v1"
DEFAULT_MAX_CHUNKS = 2000
DEFAULT_MAX_CHUNK_CHARS = 1800
DEFAULT_MAX_EXTRACTED_CHARS = 2_000_000
_SLIDE_PATH_RE = re.compile(r"^ppt/slides/slide([1-9][0-9]*)\.xml$")


class ChunkExtractionError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExtractedUnit:
    text: str
    locator: str
    locator_type: str
    locator_start: int
    locator_end: int
    locator_part: int = 1


@dataclass(frozen=True)
class ChunkBuildResult:
    material_id: int
    transform_version: str
    chunk_count: int
    status: str


def _max_chunks() -> int:
    return max(1, int(getattr(settings, "LEARNING_MATERIAL_MAX_CHUNKS", DEFAULT_MAX_CHUNKS)))


def _max_chunk_chars() -> int:
    return max(200, int(getattr(settings, "LEARNING_MATERIAL_MAX_CHUNK_CHARS", DEFAULT_MAX_CHUNK_CHARS)))


def _max_extracted_chars() -> int:
    return max(
        _max_chunk_chars(),
        int(getattr(settings, "LEARNING_MATERIAL_MAX_EXTRACTED_CHARS", DEFAULT_MAX_EXTRACTED_CHARS)),
    )


def _clean_text(value: str) -> str:
    # Preserve the material's words and possible prompt injection as untrusted
    # evidence; only normalize line endings and surrounding whitespace.
    return str(value or "").replace("\r\n", "\n").replace("\r", "\n").strip()


def _parts(value: str) -> tuple[str, ...]:
    cleaned = _clean_text(value)
    if not cleaned:
        return ()
    limit = _max_chunk_chars()
    return tuple(cleaned[offset : offset + limit] for offset in range(0, len(cleaned), limit))


def _line_units(text: str) -> list[ExtractedUnit]:
    units: list[ExtractedUnit] = []
    pending: list[str] = []
    start_line = 0
    end_line = 0

    def flush() -> None:
        nonlocal pending, start_line, end_line
        if not pending:
            return
        combined = "\n".join(pending)
        for part_index, part in enumerate(_parts(combined), start=1):
            suffix = f".part:{part_index}" if len(combined) > _max_chunk_chars() else ""
            units.append(
                ExtractedUnit(
                    text=part,
                    locator=f"line:{start_line}-{end_line}{suffix}",
                    locator_type=KnowledgeChunk.LOCATOR_LINE,
                    locator_start=start_line,
                    locator_end=end_line,
                    locator_part=part_index,
                )
            )
        pending = []
        start_line = 0
        end_line = 0

    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        projected = len("\n".join(pending + [line]))
        if pending and projected > _max_chunk_chars():
            flush()
        if not pending:
            start_line = line_number
        pending.append(line)
        end_line = line_number
        if len(line) > _max_chunk_chars():
            flush()
    flush()
    return units


def _located_units(
    items: Iterable[tuple[int, str]],
    *,
    locator_type: str,
) -> list[ExtractedUnit]:
    units: list[ExtractedUnit] = []
    for ordinal, raw_text in items:
        parts = _parts(raw_text)
        for part_index, part in enumerate(parts, start=1):
            suffix = f".part:{part_index}" if len(parts) > 1 else ""
            units.append(
                ExtractedUnit(
                    text=part,
                    locator=f"{locator_type}:{ordinal}{suffix}",
                    locator_type=locator_type,
                    locator_start=ordinal,
                    locator_end=ordinal,
                    locator_part=part_index,
                )
            )
    return units


def _extract_text(handle) -> list[ExtractedUnit]:
    payload = handle.read(_max_extracted_chars() + 1)
    if len(payload) > _max_extracted_chars():
        raise ChunkExtractionError("extracted_text_limit", "Text source exceeds extraction limit.")
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ChunkExtractionError("text_decode_failed", "Text source is not valid UTF-8.") from exc
    return _line_units(text)


def _archive_names(archive: zipfile.ZipFile) -> set[str]:
    # Reuse the same bomb/path validation applied at upload time, because stored
    # bytes may have been written by a legacy or external storage path.
    from .material_rag_service import _validate_office_archive

    return _validate_office_archive(archive)


def _extract_docx(handle) -> list[ExtractedUnit]:
    try:
        with zipfile.ZipFile(handle) as archive:
            names = _archive_names(archive)
            if "word/document.xml" not in names:
                raise ChunkExtractionError("docx_document_missing", "DOCX document body is missing.")
            root = ET.fromstring(archive.read("word/document.xml"))
    except ChunkExtractionError:
        raise
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise ChunkExtractionError("docx_parse_failed", "DOCX extraction failed.") from exc

    paragraphs: list[tuple[int, str]] = []
    ordinal = 0
    for element in root.iter():
        if not element.tag.endswith("}p"):
            continue
        ordinal += 1
        text = "".join(
            child.text or ""
            for child in element.iter()
            if child.tag.endswith("}t")
        )
        if _clean_text(text):
            paragraphs.append((ordinal, text))
    return _located_units(paragraphs, locator_type=KnowledgeChunk.LOCATOR_PARAGRAPH)


def _extract_pptx(handle) -> list[ExtractedUnit]:
    try:
        with zipfile.ZipFile(handle) as archive:
            names = _archive_names(archive)
            slides = sorted(
                (
                    (int(match.group(1)), name)
                    for name in names
                    if (match := _SLIDE_PATH_RE.match(name))
                ),
                key=lambda item: item[0],
            )
            extracted: list[tuple[int, str]] = []
            for slide_number, name in slides:
                root = ET.fromstring(archive.read(name))
                text = "\n".join(
                    (element.text or "").strip()
                    for element in root.iter()
                    if element.tag.endswith("}t") and (element.text or "").strip()
                )
                if _clean_text(text):
                    extracted.append((slide_number, text))
    except (zipfile.BadZipFile, KeyError, ET.ParseError, OSError) as exc:
        raise ChunkExtractionError("pptx_parse_failed", "PPTX extraction failed.") from exc
    if not slides:
        raise ChunkExtractionError("pptx_slides_missing", "PPTX contains no slides.")
    return _located_units(extracted, locator_type=KnowledgeChunk.LOCATOR_SLIDE)


def _extract_pdf(handle) -> list[ExtractedUnit]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover - deployment preflight
        raise ChunkExtractionError("pdf_dependency_missing", "PDF extraction dependency is unavailable.") from exc
    try:
        reader = PdfReader(handle, strict=False)
        if reader.is_encrypted:
            raise ChunkExtractionError("pdf_encrypted", "Encrypted PDFs are not supported.")
        pages = []
        for page_number, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if _clean_text(text):
                pages.append((page_number, text))
    except ChunkExtractionError:
        raise
    except Exception as exc:
        raise ChunkExtractionError("pdf_parse_failed", "PDF extraction failed.") from exc
    if not pages:
        raise ChunkExtractionError(
            "pdf_no_extractable_text",
            "PDF contains no extractable text; no page locator was fabricated.",
        )
    return _located_units(pages, locator_type=KnowledgeChunk.LOCATOR_PAGE)


def _extract_units(material: UploadedLearningMaterial) -> list[ExtractedUnit]:
    if material.content_signature == "legacy_unverified":
        raise ChunkExtractionError(
            "legacy_source_unverified",
            "Legacy material cannot produce trusted-locator chunks without source verification.",
        )
    if not material.source_file:
        raise ChunkExtractionError("source_file_missing", "Persisted source bytes are unavailable.")
    if not re.fullmatch(r"[0-9a-f]{64}", str(material.content_sha256 or "")):
        raise ChunkExtractionError(
            "source_hash_missing",
            "Verified source is missing its immutable SHA-256 digest.",
        )

    material.source_file.open("rb")
    try:
        handle = material.source_file.file
        if material.file_extension in {".txt", ".md"}:
            units = _extract_text(handle)
        elif material.file_extension == ".docx":
            units = _extract_docx(handle)
        elif material.file_extension == ".pptx":
            units = _extract_pptx(handle)
        elif material.file_extension == ".pdf":
            units = _extract_pdf(handle)
        else:
            raise ChunkExtractionError("source_type_unsupported", "Source type is unsupported.")
    finally:
        material.source_file.close()

    if not units:
        raise ChunkExtractionError("no_extractable_text", "Source contains no extractable text.")
    if len(units) > _max_chunks():
        raise ChunkExtractionError("chunk_count_limit", "Source produces too many chunks.")
    total_chars = sum(len(unit.text) for unit in units)
    if total_chars > _max_extracted_chars():
        raise ChunkExtractionError("extracted_text_limit", "Extracted text exceeds safe limit.")
    return units


def _chunk_identity(material: UploadedLearningMaterial, unit: ExtractedUnit) -> tuple[str, str]:
    content_hash = hashlib.sha256(unit.text.encode("utf-8")).hexdigest()
    identity = "\0".join(
        (
            # The resulting id is a SHA-256 digest; local owner/goal ids never
            # leave this boundary, but prevent global cross-scope collisions.
            str(material.user_id),
            str(material.learning_goal_id),
            material.source_key,
            str(material.source_version),
            CHUNK_TRANSFORM_VERSION,
            unit.locator,
            content_hash,
        )
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest(), content_hash


def _set_material_failure(material_id: int, code: str) -> None:
    UploadedLearningMaterial.objects.filter(id=material_id).update(
        chunk_status=(
            UploadedLearningMaterial.CHUNK_STATUS_LEGACY_UNVERIFIED
            if code == "legacy_source_unverified"
            else UploadedLearningMaterial.CHUNK_STATUS_FAILED
        ),
        chunk_error_code=code[:96],
        chunk_transform_version=CHUNK_TRANSFORM_VERSION,
        chunks_built_at=None,
        updated_at=timezone.now(),
    )


def extract_and_persist_material_chunks(material_id: int, username: str) -> ChunkBuildResult:
    material = (
        UploadedLearningMaterial.objects.select_related("user", "learning_goal")
        .filter(id=int(material_id), user__username=username)
        .first()
    )
    if not material:
        raise ChunkExtractionError("material_not_found", "Material is unavailable.")
    if material.deleted_at or material.active_content_sha256 is None:
        _set_material_failure(material.id, "source_deleted")
        raise ChunkExtractionError("source_deleted", "Deleted material is ineligible for extraction.")
    if (
        material.chunk_status == UploadedLearningMaterial.CHUNK_STATUS_READY
        and material.chunk_transform_version == CHUNK_TRANSFORM_VERSION
    ):
        return ChunkBuildResult(
            material_id=material.id,
            transform_version=CHUNK_TRANSFORM_VERSION,
            chunk_count=material.knowledge_chunks.filter(
                transform_version=CHUNK_TRANSFORM_VERSION,
                lifecycle=KnowledgeChunk.LIFECYCLE_ACTIVE,
            ).count(),
            status=UploadedLearningMaterial.CHUNK_STATUS_READY,
        )

    UploadedLearningMaterial.objects.filter(id=material.id).update(
        chunk_status=UploadedLearningMaterial.CHUNK_STATUS_PROCESSING,
        chunk_error_code="",
        chunk_transform_version=CHUNK_TRANSFORM_VERSION,
        updated_at=timezone.now(),
    )
    try:
        units = _extract_units(material)
    except ChunkExtractionError as exc:
        _set_material_failure(material.id, exc.code)
        raise

    now = timezone.now()
    deleted_during_extraction = False
    non_deterministic_replay = False
    with transaction.atomic():
        material = (
            UploadedLearningMaterial.objects.select_for_update()
            .select_related("user", "learning_goal")
            .get(id=material.id, user__username=username)
        )
        if material.deleted_at or material.active_content_sha256 is None:
            material.chunk_status = UploadedLearningMaterial.CHUNK_STATUS_FAILED
            material.chunk_error_code = "source_deleted"
            material.chunk_transform_version = CHUNK_TRANSFORM_VERSION
            material.chunks_built_at = None
            material.save(
                update_fields=[
                    "chunk_status",
                    "chunk_error_code",
                    "chunk_transform_version",
                    "chunks_built_at",
                    "updated_at",
                ]
            )
            deleted_during_extraction = True
        else:
            existing = list(
                KnowledgeChunk.objects.filter(
                    material=material,
                    transform_version=CHUNK_TRANSFORM_VERSION,
                ).order_by("chunk_index")
            )
            if existing:
                expected = [_chunk_identity(material, unit)[0] for unit in units]
                if [chunk.chunk_id for chunk in existing] != expected:
                    material.chunk_status = UploadedLearningMaterial.CHUNK_STATUS_FAILED
                    material.chunk_error_code = "non_deterministic_replay"
                    material.chunks_built_at = None
                    material.save(
                        update_fields=[
                            "chunk_status",
                            "chunk_error_code",
                            "chunks_built_at",
                            "updated_at",
                        ]
                    )
                    non_deterministic_replay = True
            else:
                chunks = []
                for index, unit in enumerate(units, start=1):
                    chunk_id, content_hash = _chunk_identity(material, unit)
                    chunks.append(
                        KnowledgeChunk(
                            user=material.user,
                            learning_goal=material.learning_goal,
                            material=material,
                            source_key=material.source_key,
                            source_version=material.source_version,
                            source_content_sha256=material.content_sha256 or "",
                            chunk_id=chunk_id,
                            chunk_index=index,
                            content=unit.text,
                            content_sha256=content_hash,
                            transform_version=CHUNK_TRANSFORM_VERSION,
                            locator=unit.locator,
                            locator_type=unit.locator_type,
                            locator_start=unit.locator_start,
                            locator_end=unit.locator_end,
                            locator_part=unit.locator_part,
                            lifecycle=KnowledgeChunk.LIFECYCLE_ACTIVE,
                            trust_level="untrusted_retrieved_content",
                        )
                    )
                KnowledgeChunk.objects.bulk_create(chunks)

            if not non_deterministic_replay:
                latest_version = (
                    UploadedLearningMaterial.objects.filter(
                        user=material.user,
                        learning_goal=material.learning_goal,
                        source_key=material.source_key,
                    ).aggregate(max_version=Max("source_version"))["max_version"]
                    or material.source_version
                )
                current_lifecycle = (
                    KnowledgeChunk.LIFECYCLE_ACTIVE
                    if material.source_version == latest_version
                    else KnowledgeChunk.LIFECYCLE_SUPERSEDED
                )
                KnowledgeChunk.objects.filter(
                    material=material,
                    transform_version=CHUNK_TRANSFORM_VERSION,
                ).update(
                    lifecycle=current_lifecycle,
                    superseded_at=(
                        None if current_lifecycle == KnowledgeChunk.LIFECYCLE_ACTIVE else now
                    ),
                    deleted_at=None,
                    updated_at=now,
                )
                if current_lifecycle == KnowledgeChunk.LIFECYCLE_ACTIVE:
                    KnowledgeChunk.objects.filter(
                        user=material.user,
                        learning_goal=material.learning_goal,
                        source_key=material.source_key,
                        lifecycle=KnowledgeChunk.LIFECYCLE_ACTIVE,
                    ).exclude(material=material).update(
                        lifecycle=KnowledgeChunk.LIFECYCLE_SUPERSEDED,
                        superseded_at=now,
                        updated_at=now,
                    )
                material.chunk_status = UploadedLearningMaterial.CHUNK_STATUS_READY
                material.chunk_error_code = ""
                material.chunk_transform_version = CHUNK_TRANSFORM_VERSION
                material.chunks_built_at = now
                material.save(
                    update_fields=[
                        "chunk_status",
                        "chunk_error_code",
                        "chunk_transform_version",
                        "chunks_built_at",
                        "updated_at",
                    ]
                )

    if deleted_during_extraction:
        raise ChunkExtractionError("source_deleted", "Material was deleted during extraction.")
    if non_deterministic_replay:
        raise ChunkExtractionError(
            "non_deterministic_replay",
            "Frozen source produced a different deterministic chunk set.",
        )

    return ChunkBuildResult(
        material_id=material.id,
        transform_version=CHUNK_TRANSFORM_VERSION,
        chunk_count=len(units),
        status=UploadedLearningMaterial.CHUNK_STATUS_READY,
    )


__all__ = [
    "CHUNK_TRANSFORM_VERSION",
    "ChunkBuildResult",
    "ChunkExtractionError",
    "ExtractedUnit",
    "extract_and_persist_material_chunks",
]
