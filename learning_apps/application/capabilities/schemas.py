from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(StrictInput):
    pass


class RetrievalInput(StrictInput):
    query: str = Field(min_length=1, max_length=1200)
    max_results: int = Field(default=6, ge=1, le=8)


class RecentInput(StrictInput):
    limit: int = Field(default=5, ge=1, le=10)


class ConceptStateInput(StrictInput):
    concept_key: str = Field(min_length=1, max_length=160)


class MemoryProposalInput(StrictInput):
    memory_key: Literal[
        "response_style",
        "explanation_depth",
        "preferred_language",
        "practice_format",
        "learning_pace",
        "learning_strategy",
    ]
    value: str = Field(min_length=1, max_length=120)
    reason: str = Field(min_length=1, max_length=240)


class QuizProposalInput(StrictInput):
    concept_key: str = Field(default="", max_length=160)
    focus: str = Field(min_length=1, max_length=240)
    question_count: int = Field(default=3, ge=1, le=5)


class ReviewPlanProposalInput(StrictInput):
    concept_keys: list[str] = Field(min_length=1, max_length=5)
    cadence: Literal["tomorrow", "three_days", "one_week"]
    reason: str = Field(min_length=1, max_length=240)


class StudySessionProposalInput(StrictInput):
    title: str = Field(min_length=1, max_length=120)
    duration_minutes: int = Field(ge=10, le=120)
    timing: str = Field(min_length=1, max_length=120)


class FlexibleOutput(BaseModel):
    model_config = ConfigDict(extra="allow")


class TutorPlanStep(StrictInput):
    title: str = Field(min_length=1, max_length=120)
    status: Literal["pending", "in_progress", "completed"]


class UpdatePlanInput(StrictInput):
    steps: list[TutorPlanStep] = Field(min_length=1, max_length=5)


class ClarificationInput(StrictInput):
    question: str = Field(min_length=1, max_length=400)
    options: list[str] = Field(default_factory=list, max_length=4)
    why_needed: str = Field(min_length=1, max_length=240)


def clean_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").replace("\x00", " ").split())[:limit]
