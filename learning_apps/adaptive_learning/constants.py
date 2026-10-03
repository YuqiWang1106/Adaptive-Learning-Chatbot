from __future__ import annotations

DIMENSIONS = ("facts", "procedures", "strategies", "rationales")

DIMENSION_LABELS = {
    "facts": "Facts",
    "procedures": "Procedures",
    "strategies": "Strategies",
    "rationales": "Rationales",
}

DIMENSION_WEIGHTS = {
    "facts": 0.35,
    "procedures": 0.30,
    "strategies": 0.25,
    "rationales": 0.10,
}

FUSION_WEIGHTS = {
    "local_recent": 0.50,
    "global_history": 0.25,
    "current_score": 0.15,
    "time_decay": 0.10,
}

DIMENSION_POLICY = {
    "facts": "Clarify definitions, vocabulary, and core examples before adding complexity.",
    "procedures": "Break the solution into small ordered steps and ask the learner to fill the missing operation.",
    "strategies": "Compare alternative methods and explain when each method should be used.",
    "rationales": "Ask why-questions and require the learner to explain the principle behind the method.",
}

TIER_POLICY = {
    "REINFORCE": "Confirm what is correct, then add a deeper challenge.",
    "CONSOLIDATE": "Acknowledge correct parts and repair the weakest relevant dimension.",
    "SCAFFOLD": "Reduce cognitive load and guide from fundamentals with short prompts.",
    "PLATEAU": "Change explanation mode using analogy, contrast, visual framing, or counterexample.",
    "REVIEW": "Review prerequisites and use spaced repetition before advancing.",
}
