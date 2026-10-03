from __future__ import annotations

from typing import Any

from pydantic import Field

from .schemas import StrictInput


class GoalPreferenceInput(StrictInput):
    preference: str = Field(min_length=1, max_length=2000)


class JobInput(StrictInput):
    job_id: str = Field(min_length=1, max_length=96)


class ChatHistoryInput(StrictInput):
    before_cursor: str = Field(default="", max_length=32)


class FileDescriptorInput(StrictInput):
    filename: str = Field(min_length=1, max_length=255)
    content_type: str = Field(default="", max_length=128)
    size: int = Field(default=0, ge=0)


class MaterialInput(StrictInput):
    material_id: int = Field(gt=0)


class MaterialJobInput(StrictInput):
    job_id: str = Field(min_length=1, max_length=96)


class ProbeAnswerInput(StrictInput):
    probe_id: int = Field(gt=0)
    answer: Any


class ProbeIdentityInput(StrictInput):
    probe_id: int = Field(gt=0)


class DirectMemoryProposalInput(StrictInput):
    kind: str = Field(min_length=1, max_length=64)
    memory_key: str = Field(min_length=1, max_length=64)
    value_payload: dict[str, Any]
    source: str = Field(default="explicit_user_statement", max_length=64)
    idempotency_key: str = Field(default="", max_length=96)


class MemoryIdentityInput(StrictInput):
    memory_id: str = Field(min_length=1, max_length=96)
    idempotency_key: str = Field(default="", max_length=96)


class GoalContextInput(StrictInput):
    domain: str = Field(default="", max_length=160)
    branch: str = Field(default="", max_length=160)
    preference: str = Field(default="", max_length=2000)


class GoalContextPayloadInput(StrictInput):
    goal_context: dict[str, Any]


class AssessmentSubmissionInput(StrictInput):
    post_data: dict[str, Any]
    goal_context: dict[str, Any]


class GoalCreationWorkerInput(StrictInput):
    job_id: str = Field(min_length=1, max_length=96)
    raw_preference_text: str = Field(min_length=1, max_length=2000)


class ConceptMapWorkerInput(StrictInput):
    job_id: str = Field(min_length=1, max_length=96)
    force_refresh: bool = False


class MaterialWorkerInput(StrictInput):
    job_id: str = Field(min_length=1, max_length=96)
    material_id: int = Field(gt=0)


class AssessmentWorkerInput(StrictInput):
    job_id: str = Field(min_length=1, max_length=96)
    post_data: dict[str, Any]
    goal_context: dict[str, Any]


class TargetScopeInput(StrictInput):
    target_task: str = Field(min_length=1, max_length=1000)
    goal_context: dict[str, Any]


class TargetScopeWorkerInput(TargetScopeInput):
    job_id: str = Field(min_length=1, max_length=96)


class AdaptiveGradeInput(StrictInput):
    payload: dict[str, Any]


class ProbeOfferGenerationInput(StrictInput):
    offer_id: str = Field(min_length=1, max_length=48)


class ReviewScanInput(StrictInput):
    limit: int = Field(default=100, ge=1, le=1000)
    cursor_id: int | None = Field(default=None, ge=0)


class CleanupInput(StrictInput):
    cleanup_limit: int = Field(default=100, ge=1, le=1000)
    retention_limit: int = Field(default=500, ge=1, le=5000)


class ExpireMemoryInput(StrictInput):
    limit: int = Field(default=500, ge=1, le=5000)


class TeacherStudentInput(StrictInput):
    classroom_id: int = Field(gt=0)
    student_id: int = Field(gt=0)


class TeacherStudentGoalInput(TeacherStudentInput):
    target_goal_id: int = Field(gt=0)


class ClassroomInput(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    subject: str = Field(default="", max_length=120)
    term: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)


class ClassroomIdentityInput(StrictInput):
    classroom_id: int = Field(gt=0)


class ClassroomDetailInput(ClassroomIdentityInput):
    query: str = Field(default="", max_length=160)


class ClassroomUpdateInput(StrictInput):
    classroom_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=120)
    subject: str = Field(default="", max_length=120)
    term: str = Field(default="", max_length=120)
    description: str = Field(default="", max_length=1000)


class ClassroomInviteInput(ClassroomIdentityInput):
    student_username: str = Field(min_length=1, max_length=150)
    message: str = Field(default="", max_length=500)


class ClassroomInvitationInput(ClassroomIdentityInput):
    invitation_id: int = Field(gt=0)


class ClassroomStudentInput(ClassroomIdentityInput):
    student_id: int = Field(gt=0)


class InvitationIdentityInput(StrictInput):
    invitation_id: int = Field(gt=0)
