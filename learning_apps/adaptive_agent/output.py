from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class TutorCitation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    evidence_ref: str = Field(min_length=1, max_length=40)
    claim: str = Field(min_length=1, max_length=800)
    supporting_quote: str = Field(min_length=1, max_length=800)


class PersonalizationBasis(BaseModel):
    model_config = ConfigDict(extra="forbid")
    status: Literal["observed_evidence", "perceived_guidance", "goal_only", "insufficient"] = "insufficient"
    context_policy_version: str = Field(min_length=1, max_length=64)
    context_decision_sha256: str = Field(min_length=64, max_length=64)
    adaptation_level: Literal["none", "soft", "evidence_backed"] = "none"
    concept_keys: list[str] = Field(default_factory=list, max_length=8)
    evidence_categories: list[str] = Field(default_factory=list, max_length=12)
    limitation: str = Field(default="", max_length=400)


class OptionalLearningCheck(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["near_transfer", "one_step", "choice_with_reason", "error_diagnosis", "short_recall"]
    prompt: str = Field(min_length=1, max_length=1200)
    options: list[str] = Field(default_factory=list, max_length=6)
    response_format: Literal["short_text", "single_choice"]
    concept_key: str = Field(min_length=1, max_length=160)
    target_dimension: Literal["facts", "procedures", "strategies", "rationales"]
    success_criteria: list[str] = Field(min_length=1, max_length=4)
    accepted_option: str = Field(default="", max_length=240)
    offer_reason: str = Field(min_length=1, max_length=120)


class TutorTurnOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    answer_markdown: str = Field(min_length=1, max_length=12000)
    teaching_strategy: str = Field(min_length=1, max_length=240)
    citations: list[TutorCitation] = Field(default_factory=list, max_length=12)
    used_evidence_categories: list[str] = Field(default_factory=list, max_length=12)
    next_action: str = Field(default="", max_length=400)
    confidence: Literal["high", "medium", "low"] = "medium"
    personalization_basis: PersonalizationBasis = Field(default_factory=PersonalizationBasis)
    optional_learning_check: OptionalLearningCheck | None = None
