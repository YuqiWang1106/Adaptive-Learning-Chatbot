from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from learning_apps.infrastructure.services.html_sanitizer_service import sanitize_chat_html
from learning_apps.learning_goal.services import learning_goal_service
from learning_apps.chat.services.history_query_service import read_history_page


@dataclass(frozen=True)
class ChatAccessResult:
    ok: bool
    code: str = ""
    redirect_name: str = ""
    redirect_learning_goal_id: Optional[int] = None
    learning_goal_id: Optional[int] = None
    goal: Optional[Dict[str, Any]] = None


def resolve_chat_access(username: str, learning_goal_id: Optional[int]) -> ChatAccessResult:
    """Resolve chat access."""
    if not learning_goal_id:
        latest_goal_id = learning_goal_service.get_latest_goal_id(username)
        if not latest_goal_id:
            return ChatAccessResult(ok=False, code="no_learning_goal", redirect_name="learning_goal")
        return ChatAccessResult(
            ok=False,
            code="use_latest_goal",
            redirect_name="learning_goal_open",
            redirect_learning_goal_id=latest_goal_id,
        )

    goal = learning_goal_service.get_learning_goal(username, learning_goal_id)
    if not goal:
        return ChatAccessResult(ok=False, code="goal_not_found", redirect_name="learning_goal")

    if (goal.get("status") or "").strip() != "self_assessment_completed":
        return ChatAccessResult(
            ok=False,
            code="assessment_not_completed",
            redirect_name="self_assessment",
            redirect_learning_goal_id=learning_goal_id,
        )

    return ChatAccessResult(
        ok=True,
        code="ok",
        learning_goal_id=learning_goal_id,
        goal=goal,
    )


def _build_history_items(username: str, learning_goal_id: int) -> Dict[str, Any]:
    """Internal helper to build history items."""
    history_items: List[Dict[str, str]] = []
    history_cursor = None
    history_data = read_history_page(username, page=1, per_page=20, learning_goal_id=learning_goal_id)

    total = 0
    if isinstance(history_data, dict):
        total = history_data.get("total", 0) or 0
        records = history_data.get("history", []) or []
        records = list(reversed(records))
        for row in records:
            q = row.get("question")
            a = row.get("answer")
            if q:
                history_items.append({"role": "user", "content": q})
            if a:
                history_items.append({
                    "role": "assistant",
                    "content": sanitize_chat_html(a),
                    "grounding_status": row.get("grounding_status") or "legacy_unverified",
                    "citations": row.get("citations") or [],
                })

    if total > 20:
        history_cursor = 2

    return {"history_items": history_items, "history_cursor": history_cursor}


def build_chat_page_context(username: str, learning_goal_id: int, goal: Dict[str, Any]) -> Dict[str, Any]:
    """Build chat page context."""
    predefined_questions_by_category = {
        "Mathematics & Physics": [
            {"question": "What is math?", "mastery_level": "beginner"},
            {"question": "How to solve quadratic equations?", "mastery_level": "intermediate"},
            {"question": "What is gravity?", "mastery_level": "beginner"},
            {"question": "Explain the theory of relativity.", "mastery_level": "advanced"},
        ],
        "Technology & Computing": [
            {"question": "How does AI work?", "mastery_level": "intermediate"},
            {"question": "What is machine learning?", "mastery_level": "intermediate"},
            {"question": "How do computers work?", "mastery_level": "beginner"},
            {"question": "How does the internet work?", "mastery_level": "intermediate"},
            {"question": "What is cryptocurrency?", "mastery_level": "intermediate"},
            {"question": "How to improve my programming skills?", "mastery_level": "beginner"},
        ],
        "Biology & Life Sciences": [
            {"question": "What is photosynthesis?", "mastery_level": "beginner"},
            {"question": "What is DNA?", "mastery_level": "beginner"},
            {"question": "How do vaccines work?", "mastery_level": "intermediate"},
            {"question": "How does the human brain work?", "mastery_level": "advanced"},
            {"question": "How do plants grow?", "mastery_level": "beginner"},
            {"question": "How does the heart work?", "mastery_level": "intermediate"},
            {"question": "What is evolution?", "mastery_level": "intermediate"},
            {"question": "How do birds fly?", "mastery_level": "beginner"},
            {"question": "Explain the food chain.", "mastery_level": "beginner"},
        ],
        "Environmental Science": [
            {"question": "What is climate change?", "mastery_level": "beginner"},
            {"question": "What is global warming?", "mastery_level": "beginner"},
            {"question": "Explain the water cycle.", "mastery_level": "beginner"},
            {"question": "What is renewable energy?", "mastery_level": "intermediate"},
        ],
        "Space & Astronomy": [
            {"question": "What is the solar system?", "mastery_level": "beginner"},
            {"question": "Tell me about quantum computing.", "mastery_level": "advanced"},
        ],
        "Health & Medicine": [
            {"question": "What are the benefits of exercise?", "mastery_level": "beginner"},
            {"question": "How do we see colors?", "mastery_level": "intermediate"},
        ],
        "Social Studies & Culture": [
            {"question": "What is democracy?", "mastery_level": "beginner"},
            {"question": "What is the capital of France?", "mastery_level": "beginner"},
            {"question": "What is the best way to learn a new language?", "mastery_level": "beginner"},
        ],
    }

    all_questions: List[str] = []
    for category_questions in predefined_questions_by_category.values():
        for q in category_questions:
            all_questions.append(q["question"])

    category_counts = {category: len(questions) for category, questions in predefined_questions_by_category.items()}
    goal_label = str(goal.get("title") or goal.get("preference_text") or "this learning goal").strip()
    if len(goal_label) > 96:
        goal_label = goal_label[:93].rstrip() + "..."
    suggested_questions = [
        f"Explain the core ideas in {goal_label} at my current level.",
        f"Show me a worked example related to {goal_label}.",
        f"What prerequisite should I review next for {goal_label}?",
        f"Quiz me on {goal_label} and explain my mistakes.",
    ]
    history_payload = _build_history_items(username, learning_goal_id)
    return {
        "predefined_questions": all_questions,
        "predefined_questions_by_category": predefined_questions_by_category,
        "category_counts": category_counts,
        "suggested_questions": suggested_questions,
        "username": username,
        "history_items": history_payload["history_items"],
        "history_cursor": history_payload["history_cursor"],
        "learning_goal": goal,
        "learning_goal_id": learning_goal_id,
        "active_learning_goal_id": learning_goal_id,
        "learning_goals": learning_goal_service.list_learning_goals(username),
    }


def load_chat_history_page(username: str, learning_goal_id: Optional[int], before_cursor: Optional[str]) -> Dict[str, Any]:
    """Load chat history page."""
    page = 1
    if before_cursor:
        try:
            page = int(before_cursor)
        except ValueError:
            page = 1

    result = read_history_page(username, page=page, per_page=20, learning_goal_id=int(learning_goal_id or 0))
    history: List[Dict[str, str]] = []
    total = 0

    if isinstance(result, dict):
        total = result.get("total", 0) or 0
        records = result.get("history", []) or []
        records = list(reversed(records))
        for row in records:
            q = row.get("question")
            a = row.get("answer")
            if q:
                history.append({"role": "user", "content": q})
            if a:
                history.append({
                    "role": "assistant",
                    "content": sanitize_chat_html(a),
                    "grounding_status": row.get("grounding_status") or "legacy_unverified",
                    "citations": row.get("citations") or [],
                })

    next_cursor = None
    if page * 20 < total:
        next_cursor = page + 1

    return {"history": history, "next_cursor": next_cursor}
