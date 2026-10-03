from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from django.conf import settings

from learning_apps.persistence.models import ConceptRegistryEntry, LearningGoal, UserProfile
from .evaluation_service import build_assessment_payload, run_self_assessment_evaluation
from .goal_context_service import GoalContextResult, list_learning_goals_for_sidebar, resolve_goal_context
from .services.guide_service import get_or_create_self_assessment_guides
from .evidence_repository_service import SelfAssessmentAdmissionError
from .persistence_service import SelfAssessmentProjectionError, save_evaluation_record
from .submission_validation_service import (
    DIMENSIONS,
    validate_self_assessment_submission,
)


logger = logging.getLogger(__name__)


def _emit(message: str) -> None:
    try:
        print(message, flush=True)
    except Exception:
        logger.warning(message)


def _report_trace_enabled() -> bool:
    return bool(settings.LEARNING_SELF_ASSESSMENT_REPORT_TRACE)


def _emit_self_assessment_report(
    *,
    username: str,
    learning_goal_id: int | None,
    evaluation: Dict[str, Any],
) -> None:
    if not _report_trace_enabled():
        return
    report = str(evaluation.get("report") or "").strip()
    structured = evaluation.get("structured_report") if isinstance(evaluation.get("structured_report"), dict) else {}
    dimension_names = ", ".join(str(name) for name in structured.keys()) if structured else "none"
    _emit(
        "[SelfAssessmentReport] begin "
        f"user={username} goal_id={learning_goal_id or ''} "
        f"is_partial={bool(evaluation.get('is_partial'))} "
        f"report_chars={len(report)} structured_dimensions={dimension_names}"
    )
    if report:
        _emit(report)
    else:
        _emit("[SelfAssessmentReport] empty_report")
    _emit(f"[SelfAssessmentReport] end user={username} goal_id={learning_goal_id or ''}")


def _safe_int(value: Any) -> Optional[int]:
    """Internal helper to handle safe int."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class SubmitResult:
    ok: bool
    code: str = ""
    learning_goal_id: Optional[int] = None
    assessment_id: Optional[int] = None
    needs_revision: bool = False


def resolve_request_goal_context(request, username: str) -> GoalContextResult:
    """Resolve request goal context."""
    learning_goal_id = _safe_int(request.GET.get("learning_goal_id"))
    if request.method == "POST" and not learning_goal_id:
        learning_goal_id = _safe_int(request.POST.get("learning_goal_id"))

    return resolve_goal_context(
        username=username,
        learning_goal_id=learning_goal_id,
        domain=request.GET.get("domain", ""),
        branch=request.GET.get("branch", ""),
        preference=request.GET.get("preference", ""),
    )


def submit_self_assessment(
    request,
    username: str,
    goal_context: GoalContextResult,
    *,
    progress: Callable[[str, int, str], None] | None = None,
) -> SubmitResult:
    """Submit self assessment."""
    _emit(
        f"[SelfAssessmentFlow] submit_started user={username} "
        f"goal_id={goal_context.learning_goal_id or ''} "
        f"domain={goal_context.domain or ''} branch={goal_context.branch or ''}"
    )
    # Authentication scope is authoritative; a hidden/request student_id must
    # never redirect an assessment or its adaptive baseline to another learner.
    student_id = username
    if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED:
        validation = validate_self_assessment_submission(request.POST)
        if not validation.valid:
            return SubmitResult(
                ok=False,
                code="submission_validation_failed",
                learning_goal_id=goal_context.learning_goal_id,
                needs_revision=True,
            )
        snapshot = validation.snapshot
        problem = snapshot["context"]
        knowledge_types = [
            {
                "type": dimension,
                **snapshot["dimensions"][dimension],
            }
            for dimension in DIMENSIONS
        ]
        evidence_sufficiency = snapshot["evidence_sufficiency"]
        reference_blueprint_id = snapshot["reference_blueprint_id"]
        reference_blueprint_sha256 = snapshot["reference_blueprint_sha256"]
        target_task_sha256 = snapshot["target_task_sha256"]
        scope_confirmed = snapshot["scope_confirmed"]
    else:
        problem = request.POST.get("problem", "").strip()
        snapshot = {}
        evidence_sufficiency = {}
        knowledge_types = [
            {
                "type": dimension,
                "statement": request.POST.get(f"{dimension}_statement", "").strip(),
                "uncertainties": request.POST.get(f"{dimension}_uncertainties", "").strip(),
            }
            for dimension in DIMENSIONS
        ]
        reference_blueprint_id = None
        reference_blueprint_sha256 = ""
        target_task_sha256 = ""
        scope_confirmed = False

    assessment_payload = build_assessment_payload(
        domain=goal_context.domain,
        branch=goal_context.branch,
        preference=goal_context.preference,
        problem=problem,
        knowledge_types=knowledge_types,
        learning_goal_id=goal_context.learning_goal_id,
        username=username,
        context=problem,
        evidence_sufficiency=evidence_sufficiency,
        reference_blueprint_id=reference_blueprint_id,
        reference_blueprint_sha256=reference_blueprint_sha256,
        target_task_sha256=target_task_sha256,
        scope_confirmed=scope_confirmed,
    )
    _emit(
        f"[SelfAssessmentFlow] payload_built user={username} goal_id={goal_context.learning_goal_id or ''} "
        f"knowledge_types={len(knowledge_types)} problem_len={len(problem)}"
    )

    try:
        _emit(f"[SelfAssessmentFlow] evaluation_started user={username} goal_id={goal_context.learning_goal_id or ''}")
        evaluation = run_self_assessment_evaluation(
            student_id,
            assessment_payload,
            progress=progress,
        )
        _emit(
            f"[SelfAssessmentFlow] evaluation_completed user={username} goal_id={goal_context.learning_goal_id or ''} "
            f"is_partial={bool(evaluation.get('is_partial'))} "
            f"dimension_errors={len(evaluation.get('dimension_errors') or {})}"
        )
        _emit_self_assessment_report(
            username=username,
            learning_goal_id=goal_context.learning_goal_id,
            evaluation=evaluation,
        )
    except Exception as exc:
        if settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED:
            logger.exception(
                "Self Assessment V2 evaluation failed for user=%s goal=%s",
                username,
                goal_context.learning_goal_id,
            )
            return SubmitResult(
                ok=False,
                code=f"evaluation_failed:{str(exc)[:96]}",
                learning_goal_id=goal_context.learning_goal_id,
            )
        _emit(
            f"[SelfAssessmentFlow] evaluation_failed user={username} "
            f"goal_id={goal_context.learning_goal_id or ''} fallback=lightweight_strategy"
        )
        evaluation = {
            "student_id": student_id,
            "report": "A lightweight learning strategy was created while the full diagnostic report is still unavailable.",
            "student_text": problem,
            "structured_report": {},
            "domain": goal_context.domain,
            "branch": goal_context.branch,
            "dimension_errors": {"full_report": "evaluation_failed"},
            "is_partial": True,
        }
        _emit_self_assessment_report(
            username=username,
            learning_goal_id=goal_context.learning_goal_id,
            evaluation=evaluation,
        )

    if not goal_context.learning_goal_id:
        return SubmitResult(ok=False, code="learning_goal_required")
    try:
        saved = save_evaluation_record(
            username,
            goal_context.learning_goal_id,
            evaluation,
            assessment_payload=assessment_payload,
            submission_snapshot=snapshot,
        )
    except SelfAssessmentProjectionError as exc:
        _emit(
            f"[SelfAssessmentFlow] perceived_state_blocked user={username} "
            f"goal_id={goal_context.learning_goal_id} reason={exc}"
        )
        return SubmitResult(
            ok=False,
            code=f"perceived_state_blocked:{exc}",
            learning_goal_id=goal_context.learning_goal_id,
        )
    except SelfAssessmentAdmissionError as exc:
        _emit(
            f"[SelfAssessmentFlow] evidence_changed user={username} "
            f"goal_id={goal_context.learning_goal_id} reason={exc}"
        )
        return SubmitResult(
            ok=False,
            code=f"evidence_blocked:{exc}",
            learning_goal_id=goal_context.learning_goal_id,
        )
    _emit(
        f"[SelfAssessmentFlow] record_saved user={username} goal_id={goal_context.learning_goal_id or ''}"
    )
    if not saved.accepted:
        _emit(
            f"[SelfAssessmentFlow] admission_blocked user={username} "
            f"goal_id={goal_context.learning_goal_id} reason={saved.decision.reason_code}"
        )
        return SubmitResult(
            ok=False,
            code=f"evidence_blocked:{saved.decision.reason_code}",
            learning_goal_id=goal_context.learning_goal_id,
            assessment_id=saved.assessment.id,
            needs_revision=True,
        )
    if not saved.perceived_state_persisted or not saved.goal_completed:
        return SubmitResult(
            ok=False,
            code="perceived_state_blocked:completion_not_committed",
            learning_goal_id=goal_context.learning_goal_id,
        )
    _emit(
        f"[SelfAssessmentFlow] goal_marked_completed user={username} goal_id={goal_context.learning_goal_id or ''}"
    )

    return SubmitResult(
        ok=True,
        code="ok",
        learning_goal_id=goal_context.learning_goal_id,
        assessment_id=saved.assessment.id,
    )


def build_template_context(username: str, goal_context: GoalContextResult) -> Dict[str, Any]:
    """Build template context."""
    user = UserProfile.objects.filter(username=username).first()
    goal_model = (
        LearningGoal.objects.filter(id=goal_context.learning_goal_id, user=user).first()
        if user and goal_context.learning_goal_id
        else None
    )
    self_assessment_guides = (
        get_or_create_self_assessment_guides(user=user, goal=goal_model)
        if user and goal_model
        else {}
    )
    concept_entries = (
        list(
            ConceptRegistryEntry.objects.filter(
                user=user,
                learning_goal=goal_model,
                status=ConceptRegistryEntry.STATUS_VERIFIED,
            ).order_by("id")[:5]
        )
        if user and goal_model
        else []
    )

    return {
        "learning_goal_id": goal_context.learning_goal_id,
        "active_learning_goal_id": goal_context.learning_goal_id,
        "learning_goals": list_learning_goals_for_sidebar(username),
        "domain": goal_context.domain,
        "branch": goal_context.branch,
        "preference": goal_context.preference,
        "self_assessment_guides": self_assessment_guides,
        "self_assessment_v2_enabled": settings.LEARNING_SELF_ASSESSMENT_V2_ENABLED,
        "goal_title": (
            str((goal_context.goal or {}).get("title") or goal_context.preference)
        ),
        "goal_scope": goal_context.preference,
        "core_concepts": [
            entry.concept_label or entry.concept_key.replace("_", " ").title()
            for entry in concept_entries
        ],
    }
