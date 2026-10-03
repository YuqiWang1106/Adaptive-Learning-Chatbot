"""Versioned Cognitive Structure Analysis contract for Self Assessment V2.

The operational definitions are faithful paraphrases of the INKS/CSA model
described by Cynkin and Leddo (2023).  Keeping the contract in code makes the
diagnostic reproducible and avoids asking a model to reinterpret the paper on
every assessment.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any


CSA_FRAMEWORK_VERSION = "leddo-inks-csa-self-assessment-2023-v1"
CSA_BLUEPRINT_PROMPT_VERSION = "csa-target-scope-blueprint-v4-2026-07"
CSA_COMPARATOR_PROMPT_VERSION = "csa-target-scope-comparator-v4-2026-07"
# Compatibility alias for audit consumers that previously expected one prompt
# version. New V2 assessments persist both stage-specific prompt versions.
CSA_PROMPT_VERSION = CSA_COMPARATOR_PROMPT_VERSION

CSA_SOURCE = {
    "title": (
        "Teaching Students to Self-Assess Using Cognitive Structure Analysis: "
        "Helping Students Determine What They Do and Do Not Know"
    ),
    "authors": ["Celeste Cynkin", "John Leddo"],
    "year": 2023,
    "doi": "10.46609/IJSSER.2023.v08i09.040",
    "framework": "Integrated Knowledge Structure (INKS) / Cognitive Structure Analysis (CSA)",
}

CSA_DIMENSIONS = {
    "Facts": {
        "cognitive_formalism": "semantic networks",
        "definition": (
            "Factual and semantic concepts that describe the relevant objects, "
            "elements, properties, definitions, and relationships: the what."
        ),
        "include": (
            "Terms, meanings, object or element descriptions, properties, "
            "examples, and factual relationships."
        ),
        "exclude": (
            "Do not classify a goal-level approach, an ordered execution step, "
            "or a causal explanation as a Fact merely because it contains facts."
        ),
    },
    "Strategies": {
        "cognitive_formalism": "scripts and goal-plan structures",
        "definition": (
            "General goal-directed problem-solving plans or approaches used to "
            "select and organize a route to a solution."
        ),
        "include": (
            "Which approach to use, the goal or condition it serves, decision "
            "points, and when one approach is preferable to another."
        ),
        "exclude": (
            "Do not classify the concrete ordered actions that execute an "
            "approach as the Strategy itself."
        ),
    },
    "Procedures": {
        "cognitive_formalism": "production rules",
        "definition": (
            "Concrete operations or condition-action steps used to carry out a "
            "strategy: the executable how."
        ),
        "include": (
            "Specific ordered actions, transformations, calculations, checks, "
            "and condition-action rules."
        ),
        "exclude": (
            "Do not classify a broad choice of approach or a causal reason for "
            "why a step works as a Procedure."
        ),
    },
    "Rationales": {
        "cognitive_formalism": "mental models",
        "definition": (
            "Causal principles and explanations for why facts, strategies, or "
            "procedures work, including principles that support prediction or "
            "transfer to a novel situation."
        ),
        "include": (
            "Mechanisms, causal relationships, governing principles, "
            "justifications, predictions, and explanations of why."
        ),
        "exclude": (
            "Do not treat a restated fact or a repeated step as a Rationale "
            "unless it actually supplies a causal or principled explanation."
        ),
    },
}

CSA_CATEGORIES = {
    "Irrelevant Knowledge": {
        "paper_category": "false alarm",
        "definition": (
            "The learner mentions an item they believe is relevant, but the item "
            "is not required for the scoped goal or reference blueprint."
        ),
    },
    "Know-Know": {
        "paper_category": "hit / knowing what you know",
        "definition": (
            "The item is required and relevant; the learner represents that they "
            "know it and the supplied information is correct."
        ),
    },
    "Know-Don't Know": {
        "paper_category": "knowing what you do not know",
        "definition": (
            "The item is required and relevant, and the learner explicitly "
            "identifies that the knowledge is missing, uncertain, or not learned."
        ),
    },
    "False Knowledge": {
        "paper_category": "believing one knows but giving wrong information",
        "definition": (
            "The item is required and relevant; the learner represents that they "
            "know it, but the supplied information is materially incorrect."
        ),
    },
    "Omission": {
        "paper_category": "required item not mentioned",
        "definition": (
            "A required reference item is absent and the learner gives no signal "
            "that they know it, do not know it, or are uncertain about it."
        ),
    },
}

CSA_CLASSIFICATION_RULES = (
    "Classify knowledge items, not the learner as a whole.",
    "Assign exactly one of the five categories to each reported diagnostic item.",
    "First decide whether the item belongs to the scoped goal and knowledge type; then assess the learner's metacognitive claim and correctness.",
    "A selected not_started state is explicit awareness of missing knowledge and maps to Know-Don't Know, never Omission.",
    "Uncertainty or an explicit gap maps to Know-Don't Know for that item, not False Knowledge.",
    "Use False Knowledge only when the learner supplies a materially incorrect claim about a required item.",
    "Use Omission only against an established required reference item and only when the learner gave no statement or awareness signal about that item.",
    "Do not infer observed mastery, test performance, or a mastery percentage from a self-report.",
)


def csa_framework_contract() -> dict[str, Any]:
    """Return an isolated JSON-safe contract for a provider request."""

    return deepcopy(
        {
            "framework_version": CSA_FRAMEWORK_VERSION,
            "source": CSA_SOURCE,
            "unit_of_analysis": "knowledge_item",
            "knowledge_types": CSA_DIMENSIONS,
            "classification_categories": CSA_CATEGORIES,
            "classification_rules": list(CSA_CLASSIFICATION_RULES),
        }
    )


def csa_blueprint_framework_contract() -> dict[str, Any]:
    """Return only the four fixed knowledge-type definitions used by stage one."""

    return deepcopy(CSA_DIMENSIONS)


__all__ = [
    "CSA_CATEGORIES",
    "CSA_BLUEPRINT_PROMPT_VERSION",
    "CSA_CLASSIFICATION_RULES",
    "CSA_COMPARATOR_PROMPT_VERSION",
    "CSA_DIMENSIONS",
    "CSA_FRAMEWORK_VERSION",
    "CSA_PROMPT_VERSION",
    "CSA_SOURCE",
    "csa_blueprint_framework_contract",
    "csa_framework_contract",
]
