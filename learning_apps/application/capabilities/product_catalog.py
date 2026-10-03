from __future__ import annotations

from dataclasses import asdict, is_dataclass
from enum import Enum
from typing import Any

from django.core.cache import cache
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError
from django.http import Http404
from django.utils import timezone

from learning_apps.adaptive_learning.context_service import build_progress_payload
from learning_apps.adaptive_learning.probe_service import skip_probe, submit_probe_answer
from learning_apps.adaptive_learning.review_scheduler import expire_stale_pending_probes, expire_stale_probe_offers, scan_review_goal_page
from learning_apps.chat.services.page_service import (
    build_chat_page_context,
    load_chat_history_page,
    resolve_chat_access,
)
from learning_apps.chat.services.conversation_repository_service import (
    ConversationScopeError,
    clear_conversation,
    reconcile_conversation_cleanup,
)
from learning_apps.chat.services.learner_memory_repository_service import (
    LearnerMemoryScopeError,
    confirm_learner_memory,
    expire_due_memories,
    list_learner_memories,
    propose_learner_memory,
    revoke_learner_memory,
)
from learning_apps.accounts.repository import profile_repository
from learning_apps.application.learning_goals.concept_map_jobs import (
    _run_learning_goal_concept_map_job,
)
from learning_apps.application.learning_goals.creation_jobs import (
    _run_learning_goal_creation_job,
    get_learning_goal_creation_job,
    start_learning_goal_creation_job,
)
from learning_apps.application.learning_goals.flow import list_user_learning_goals, resolve_open_learning_goal
from learning_apps.learning_goal.services import learning_goal_service
from learning_apps.application.materials.jobs import (
    MaterialUploadError,
    delete_learning_material,
    get_material_job,
    list_learning_materials,
    run_material_deletion_job,
    run_material_ingestion_job,
    upload_learning_material,
)
from learning_apps.application.learning_goals.title_jobs import _run_learning_goal_title_refine_job
from learning_apps.self_assessment.flow_service import build_template_context
from learning_apps.self_assessment.goal_context_service import GoalContextResult, resolve_goal_context
from learning_apps.application.assessments.submission_jobs import (
    _run_self_assessment_submission_job,
    get_self_assessment_submission_job,
    start_self_assessment_submission_job,
)
from learning_apps.application.assessments.target_scope_jobs import (
    _run_target_scope_job,
    get_target_scope_job,
    start_target_scope_job,
)
from learning_apps.teacher_portal.services.report_service import build_student_goal_report, build_student_report
from learning_apps.teacher_portal.services.classroom_service import (
    accept_invitation,
    archive_classroom,
    create_classroom,
    delete_classroom,
    decline_invitation,
    invite_student_by_username,
    remove_student_from_classroom,
    revoke_invitation,
    update_classroom,
)
from learning_apps.teacher_portal.services.portal_query_service import (
    classroom_detail_dto,
    classroom_edit_dto,
    student_invitation_notification_dto,
    student_classrooms_dto,
    teacher_dashboard_dto,
)
from learning_apps.persistence.models import UserProfile
from learning_apps.application.adaptive.jobs import (
    run_grading,
    run_probe_generation,
    run_probe_offer,
)

from ..contracts import (
    CapabilityAuthority,
    CapabilityContext,
    CapabilityEntrypoint,
    CapabilityError,
    CapabilityScope,
    CapabilitySpec,
)
from .schemas import EmptyInput, FlexibleOutput
from .product_schemas import (
    AdaptiveGradeInput,
    AssessmentSubmissionInput,
    AssessmentWorkerInput,
    TargetScopeInput,
    TargetScopeWorkerInput,
    ChatHistoryInput,
    CleanupInput,
    ConceptMapWorkerInput,
    DirectMemoryProposalInput,
    ExpireMemoryInput,
    FileDescriptorInput,
    GoalContextInput,
    GoalContextPayloadInput,
    GoalCreationWorkerInput,
    GoalPreferenceInput,
    JobInput,
    MaterialInput,
    MaterialJobInput,
    MaterialWorkerInput,
    MemoryIdentityInput,
    ProbeAnswerInput,
    ProbeOfferGenerationInput,
    ProbeIdentityInput,
    ReviewScanInput,
    TeacherStudentGoalInput,
    TeacherStudentInput,
    ClassroomDetailInput,
    ClassroomIdentityInput,
    ClassroomInput,
    ClassroomInvitationInput,
    ClassroomInviteInput,
    ClassroomStudentInput,
    ClassroomUpdateInput,
    InvitationIdentityInput,
)


REVIEW_SCAN_CURSOR_CACHE_KEY = "adaptive_learning:review_scan:cursor:v2"
REVIEW_SCAN_LOCK_CACHE_KEY = "adaptive_learning:review_scan:lock:v2"


def _json_value(value: Any) -> Any:
    if hasattr(value, "to_metadata"):
        return dict(value.to_metadata())
    if hasattr(value, "to_dict"):
        return dict(value.to_dict())
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


def _profile(context: CapabilityContext, _value: EmptyInput):
    return {"profile": profile_repository.by_username(context.username) or {}}


def _list_goals(context: CapabilityContext, _value: EmptyInput):
    return {"learning_goals": list_user_learning_goals(context.username)}


def _start_goal_creation(context: CapabilityContext, value: GoalPreferenceInput):
    return {"job_id": start_learning_goal_creation_job(context.username, value.preference)}


def _goal_creation_status(context: CapabilityContext, value: JobInput):
    return {"job": get_learning_goal_creation_job(value.job_id, context.username)}


def _goal_record(context: CapabilityContext, _value: EmptyInput):
    return {"goal": learning_goal_service.get_learning_goal(context.username, context.learning_goal_id)}


def _open_goal(context: CapabilityContext, _value: EmptyInput):
    return _json_value(resolve_open_learning_goal(context.username, context.learning_goal_id))


def _list_materials(context: CapabilityContext, _value: EmptyInput):
    return {
        "goal": learning_goal_service.get_learning_goal(context.username, context.learning_goal_id),
        "materials": list_learning_materials(context.username, context.learning_goal_id),
    }


def _upload_material(context: CapabilityContext, _value: FileDescriptorInput):
    upload = context.metadata.get("uploaded_file")
    if upload is None:
        raise CapabilityError("uploaded_file_required")
    try:
        return {"material": upload_learning_material(context.username, context.learning_goal_id, upload)}
    except MaterialUploadError as exc:
        raise CapabilityError("material_upload_error", str(exc)[:240]) from exc


def _material_job(context: CapabilityContext, value: MaterialJobInput):
    return {"job": get_material_job(context.username, context.learning_goal_id, value.job_id)}


def _delete_material(context: CapabilityContext, value: MaterialInput):
    try:
        return {
            "material": delete_learning_material(
                context.username,
                context.learning_goal_id,
                value.material_id,
            )
        }
    except MaterialUploadError as exc:
        raise CapabilityError("material_not_found", str(exc)[:240]) from exc


def _chat_access(context: CapabilityContext, _value: EmptyInput):
    requested_goal_id = int(context.metadata.get("requested_learning_goal_id") or 0) or None
    return _json_value(resolve_chat_access(context.username, requested_goal_id))


def _chat_page(context: CapabilityContext, _value: EmptyInput):
    goal = learning_goal_service.get_learning_goal(context.username, context.learning_goal_id) or {}
    return build_chat_page_context(context.username, context.learning_goal_id, goal)


def _chat_history(context: CapabilityContext, value: ChatHistoryInput):
    return load_chat_history_page(context.username, context.learning_goal_id, value.before_cursor or None)


def _progress(context: CapabilityContext, _value: EmptyInput):
    return build_progress_payload(context.username, context.learning_goal_id)


def _submit_probe(context: CapabilityContext, value: ProbeAnswerInput):
    return submit_probe_answer(context.username, context.learning_goal_id, value.probe_id, value.answer)


def _skip_probe(context: CapabilityContext, value: ProbeIdentityInput):
    return skip_probe(context.username, context.learning_goal_id, value.probe_id)


def _list_memories(context: CapabilityContext, _value: EmptyInput):
    try:
        return {"memories": list_learner_memories(context.username, context.learning_goal_id)}
    except LearnerMemoryScopeError as exc:
        raise CapabilityError("learner_memory_scope_error") from exc


def _propose_memory(context: CapabilityContext, value: DirectMemoryProposalInput):
    try:
        decision = propose_learner_memory(
            username=context.username,
            learning_goal_id=context.learning_goal_id,
            kind=value.kind,
            memory_key=value.memory_key,
            value_payload=value.value_payload,
            source=value.source,
            idempotency_key=value.idempotency_key or None,
        )
    except LearnerMemoryScopeError as exc:
        raise CapabilityError("learner_memory_scope_error") from exc
    return {"decision": dict(decision.to_metadata())}


def _confirm_memory(context: CapabilityContext, value: MemoryIdentityInput):
    try:
        decision = confirm_learner_memory(
            username=context.username,
            learning_goal_id=context.learning_goal_id,
            memory_id=value.memory_id,
            idempotency_key=value.idempotency_key or None,
        )
    except LearnerMemoryScopeError as exc:
        raise CapabilityError("learner_memory_scope_error") from exc
    return {"decision": dict(decision.to_metadata())}


def _revoke_memory(context: CapabilityContext, value: MemoryIdentityInput):
    try:
        decision = revoke_learner_memory(
            username=context.username,
            learning_goal_id=context.learning_goal_id,
            memory_id=value.memory_id,
            idempotency_key=value.idempotency_key or None,
        )
    except LearnerMemoryScopeError as exc:
        raise CapabilityError("learner_memory_scope_error") from exc
    return {"decision": dict(decision.to_metadata())}


def _clear_conversation(context: CapabilityContext, _value: EmptyInput):
    try:
        return clear_conversation(context.username, context.learning_goal_id).to_dict()
    except ConversationScopeError as exc:
        raise CapabilityError("conversation_scope_error") from exc


def _assessment_context(context: CapabilityContext, value: GoalContextInput):
    goal_id = int(context.metadata.get("requested_learning_goal_id") or 0) or None
    return _json_value(
        resolve_goal_context(
            context.username,
            goal_id,
            value.domain,
            value.branch,
            value.preference,
        )
    )


def _goal_context_from_payload(payload: dict[str, Any]) -> GoalContextResult:
    return GoalContextResult(
        ok=bool(payload.get("ok")),
        code=str(payload.get("code") or ""),
        learning_goal_id=payload.get("learning_goal_id"),
        goal=payload.get("goal") if isinstance(payload.get("goal"), dict) else None,
        domain=str(payload.get("domain") or ""),
        branch=str(payload.get("branch") or ""),
        preference=str(payload.get("preference") or ""),
    )


def _verified_goal_context(context: CapabilityContext, payload: dict[str, Any]) -> GoalContextResult:
    """Re-resolve client/job context against the authenticated owner scope."""

    candidate = _goal_context_from_payload(payload)
    verified = resolve_goal_context(
        context.username,
        int(candidate.learning_goal_id) if candidate.learning_goal_id else None,
        candidate.domain,
        candidate.branch,
        candidate.preference,
    )
    if not verified.ok:
        raise CapabilityError("assessment_goal_scope_fenced")
    return verified


def _assessment_page(context: CapabilityContext, value: GoalContextPayloadInput):
    return build_template_context(context.username, _verified_goal_context(context, value.goal_context))


def _start_assessment(context: CapabilityContext, value: AssessmentSubmissionInput):
    job_id = start_self_assessment_submission_job(
        username=context.username,
        post_data=value.post_data,
        goal_context=_verified_goal_context(context, value.goal_context),
    )
    return {"job_id": job_id}


def _assessment_status(context: CapabilityContext, value: JobInput):
    return {"job": get_self_assessment_submission_job(value.job_id, context.username)}


def _start_target_scope(context: CapabilityContext, value: TargetScopeInput):
    return {
        "job_id": start_target_scope_job(
            username=context.username,
            target_task=value.target_task,
            goal_context=_verified_goal_context(context, value.goal_context),
        )
    }


def _target_scope_status(context: CapabilityContext, value: JobInput):
    return {"job": get_target_scope_job(value.job_id, context.username)}


def _run_goal_creation(context: CapabilityContext, value: GoalCreationWorkerInput):
    _run_learning_goal_creation_job(value.job_id, context.username, value.raw_preference_text)
    return {"status": "completed"}


def _run_concept_map(context: CapabilityContext, value: ConceptMapWorkerInput):
    _run_learning_goal_concept_map_job(
        value.job_id,
        context.username,
        context.learning_goal_id,
        value.force_refresh,
    )
    return {"status": "completed"}


def _run_title_refine(context: CapabilityContext, _value: EmptyInput):
    _run_learning_goal_title_refine_job(context.username, context.learning_goal_id)
    return {"status": "completed"}


def _run_material_ingest(context: CapabilityContext, value: MaterialWorkerInput):
    run_material_ingestion_job(value.job_id, context.username, value.material_id)
    return {"status": "completed"}


def _run_material_delete(context: CapabilityContext, value: MaterialWorkerInput):
    run_material_deletion_job(value.job_id, context.username, value.material_id)
    return {"status": "completed"}


def _run_assessment(context: CapabilityContext, value: AssessmentWorkerInput):
    _run_self_assessment_submission_job(
        value.job_id,
        context.username,
        value.post_data,
        _verified_goal_context(context, value.goal_context),
    )
    return {"status": "completed"}


def _run_target_scope(context: CapabilityContext, value: TargetScopeWorkerInput):
    _run_target_scope_job(
        value.job_id,
        context.username,
        value.target_task,
        _verified_goal_context(context, value.goal_context),
    )
    return {"status": "completed"}


def _run_grading(context: CapabilityContext, value: AdaptiveGradeInput):
    if (
        str(value.payload.get("username") or "") != context.username
        or int(value.payload.get("learning_goal_id") or 0) != context.learning_goal_id
    ):
        raise CapabilityError("adaptive_grading_scope_mismatch")
    run_grading(value.payload)
    return {"status": "completed"}


def _run_probe_offer(context: CapabilityContext, _value: EmptyInput):
    offer = run_probe_offer(context.username, context.learning_goal_id)
    return {"status": "completed", "offer_id": getattr(offer, "offer_id", None)}


def _run_probe_generation_from_offer(_context: CapabilityContext, value: ProbeOfferGenerationInput):
    probe = run_probe_generation(value.offer_id)
    return {"status": "completed", "probe_id": getattr(probe, "id", None)}


def _scan_reviews(_context: CapabilityContext, value: ReviewScanInput):
    lock_acquired = cache.add(REVIEW_SCAN_LOCK_CACHE_KEY, "running", timeout=240)
    if not lock_acquired:
        return {
            "scheduler_version": "review_scheduler_v2",
            "status": "already_running",
            "scanned_goals": 0,
            "due_goals": 0,
            "created": 0,
            "reused": 0,
            "skipped": 0,
            "failed": 0,
        }
    try:
        now = timezone.now()
        expired = expire_stale_pending_probes(now=now)
        expired_offers = expire_stale_probe_offers(now=now)
        current_cursor = (
            max(0, int(value.cursor_id))
            if value.cursor_id is not None
            else max(0, int(cache.get(REVIEW_SCAN_CURSOR_CACHE_KEY, 0) or 0))
        )
        page = scan_review_goal_page(now=now, cursor_id=current_cursor, limit=value.limit)
        if value.cursor_id is None:
            cache.set(REVIEW_SCAN_CURSOR_CACHE_KEY, page.next_cursor_id, timeout=None)
        created = reused = skipped = failed = 0
        for user, goal, _decision in page.due_goals:
            try:
                offer = run_probe_offer(
                    user.username,
                    goal.id,
                    now=now,
                    source="review_scheduler",
                )
                if offer is None:
                    skipped += 1
                elif offer.offered_at and abs((offer.offered_at - now).total_seconds()) < 2:
                    created += 1
                else:
                    reused += 1
            except Exception:
                failed += 1
        return {
            "scheduler_version": "review_scheduler_v2",
            "status": "completed",
            "expired_pending": expired,
            "expired_offers": expired_offers,
            "scanned_goals": page.scanned_goal_count,
            "scan_first_goal_id": page.first_goal_id,
            "scan_last_goal_id": page.last_goal_id,
            "scan_cursor_before": current_cursor,
            "scan_cursor_after": page.next_cursor_id,
            "scan_cycle_complete": page.cycle_complete,
            "due_goals": len(page.due_goals),
            "created": created,
            "reused": reused,
            "skipped": skipped,
            "failed": failed,
        }
    finally:
        cache.delete(REVIEW_SCAN_LOCK_CACHE_KEY)


def _cleanup_conversations(_context: CapabilityContext, value: CleanupInput):
    return reconcile_conversation_cleanup(
        cleanup_limit=value.cleanup_limit,
        retention_limit=value.retention_limit,
    )


def _expire_memories(_context: CapabilityContext, value: ExpireMemoryInput):
    return {"expired": expire_due_memories(limit=value.limit)}


def _teacher_profile(context: CapabilityContext) -> UserProfile:
    teacher = UserProfile.objects.get(user_id=context.user_id)
    if teacher.role != UserProfile.ROLE_TEACHER:
        raise CapabilityError("teacher_authority_required")
    return teacher


def _student_profile(context: CapabilityContext) -> UserProfile:
    student = UserProfile.objects.get(user_id=context.user_id)
    if student.role != UserProfile.ROLE_STUDENT:
        raise CapabilityError("student_authority_required")
    return student


def _translate_teacher_domain_error(exc: Exception):
    if isinstance(exc, Http404):
        raise CapabilityError("teacher_resource_not_found", str(exc)) from exc
    if isinstance(exc, PermissionDenied):
        raise CapabilityError("teacher_authority_required", str(exc)) from exc
    if isinstance(exc, IntegrityError):
        raise CapabilityError("teacher_duplicate_resource") from exc
    if isinstance(exc, ValidationError):
        messages = getattr(exc, "messages", None) or [str(exc)]
        raise CapabilityError("teacher_validation_error", " ".join(str(item) for item in messages)) from exc
    raise exc


def _teacher_dashboard(context: CapabilityContext, _value: EmptyInput):
    return teacher_dashboard_dto(_teacher_profile(context))


def _teacher_classroom_detail(context: CapabilityContext, value: ClassroomDetailInput):
    try:
        return classroom_detail_dto(_teacher_profile(context), value.classroom_id, value.query)
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _teacher_classroom_edit(context: CapabilityContext, value: ClassroomIdentityInput):
    try:
        return {"classroom": classroom_edit_dto(_teacher_profile(context), value.classroom_id)}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _teacher_classroom_create(context: CapabilityContext, value: ClassroomInput):
    try:
        classroom = create_classroom(_teacher_profile(context), value.model_dump())
        return {"classroom_id": classroom.id}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _teacher_classroom_update(context: CapabilityContext, value: ClassroomUpdateInput):
    try:
        update_classroom(
            _teacher_profile(context),
            value.classroom_id,
            value.model_dump(exclude={"classroom_id"}),
        )
        return {"status": "updated"}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _teacher_classroom_archive(context: CapabilityContext, value: ClassroomIdentityInput):
    try:
        archive_classroom(_teacher_profile(context), value.classroom_id)
        return {"status": "archived"}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _teacher_classroom_delete(context: CapabilityContext, value: ClassroomIdentityInput):
    try:
        delete_classroom(_teacher_profile(context), value.classroom_id)
        return {"status": "deleted"}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _teacher_invite(context: CapabilityContext, value: ClassroomInviteInput):
    try:
        result = invite_student_by_username(
            _teacher_profile(context),
            value.classroom_id,
            value.student_username,
            value.message,
        )
        return {"created": result.created, "code": result.code}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _teacher_revoke_invitation(context: CapabilityContext, value: ClassroomInvitationInput):
    try:
        revoke_invitation(_teacher_profile(context), value.classroom_id, value.invitation_id)
        return {"status": "revoked"}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _teacher_remove_student(context: CapabilityContext, value: ClassroomStudentInput):
    try:
        remove_student_from_classroom(_teacher_profile(context), value.classroom_id, value.student_id)
        return {"status": "removed"}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _student_classrooms(context: CapabilityContext, _value: EmptyInput):
    return student_classrooms_dto(_student_profile(context))


def _student_invitation_notifications(context: CapabilityContext, _value: EmptyInput):
    student = UserProfile.objects.filter(
        user_id=context.user_id,
        role=UserProfile.ROLE_STUDENT,
    ).first()
    return student_invitation_notification_dto(student) if student else {"pending_count": 0}


def _student_accept_invitation(context: CapabilityContext, value: InvitationIdentityInput):
    try:
        accept_invitation(_student_profile(context), value.invitation_id)
        return {"status": "accepted"}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _student_decline_invitation(context: CapabilityContext, value: InvitationIdentityInput):
    try:
        decline_invitation(_student_profile(context), value.invitation_id)
        return {"status": "declined"}
    except Exception as exc:
        _translate_teacher_domain_error(exc)


def _teacher_student_report(context: CapabilityContext, value: TeacherStudentInput):
    try:
        report = build_student_report(_teacher_profile(context), value.classroom_id, value.student_id)
    except Http404 as exc:
        raise CapabilityError("teacher_resource_not_found") from exc
    return {"report": report}


def _teacher_student_goal_report(context: CapabilityContext, value: TeacherStudentGoalInput):
    try:
        report = build_student_goal_report(
            _teacher_profile(context),
            value.classroom_id,
            value.student_id,
            value.target_goal_id,
        )
    except Http404 as exc:
        raise CapabilityError("teacher_resource_not_found") from exc
    return {"report": report}


def _spec(
    name: str,
    description: str,
    handler,
    *,
    input_model=EmptyInput,
    scope: CapabilityScope,
    entrypoint: CapabilityEntrypoint,
    authority: CapabilityAuthority = CapabilityAuthority.READ,
    timeout: float = 30.0,
) -> CapabilitySpec:
    return CapabilitySpec(
        name=name,
        version="2.1.0",
        description=description,
        authority=authority,
        input_model=input_model,
        output_model=FlexibleOutput,
        handler=handler,
        allowed_entrypoints=frozenset({entrypoint}),
        agent_visible=False,
        requires_approval=False,
        timeout_seconds=timeout,
        display_name=name.replace(".", " · "),
        required_scope=scope,
        persist_output_payload=entrypoint is CapabilityEntrypoint.CELERY,
        retry_failed_invocations=entrypoint is CapabilityEntrypoint.CELERY,
    )


def product_capability_specs() -> list[CapabilitySpec]:
    http = CapabilityEntrypoint.HTTP
    celery = CapabilityEntrypoint.CELERY
    user = CapabilityScope.USER
    goal = CapabilityScope.GOAL
    system = CapabilityScope.SYSTEM
    command = CapabilityAuthority.COMMAND
    return [
        _spec("profile.get_current", "Load the authenticated product profile.", _profile, scope=user, entrypoint=http),
        _spec("learning.list_goals", "List the actor's learning goals.", _list_goals, scope=user, entrypoint=http),
        _spec("learning.goal_creation.start", "Start goal creation.", _start_goal_creation, input_model=GoalPreferenceInput, scope=user, entrypoint=http, authority=command),
        _spec("learning.goal_creation.status", "Read goal creation status.", _goal_creation_status, input_model=JobInput, scope=user, entrypoint=http),
        _spec("learning.get_goal_record", "Read one owned learning goal.", _goal_record, scope=goal, entrypoint=http),
        _spec("learning.open_goal", "Resolve the next route for one goal.", _open_goal, scope=goal, entrypoint=http),
        _spec("materials.list", "List goal materials.", _list_materials, scope=goal, entrypoint=http),
        _spec("materials.upload", "Upload one goal material.", _upload_material, input_model=FileDescriptorInput, scope=goal, entrypoint=http, authority=command, timeout=60),
        _spec("materials.job_status", "Read material job status.", _material_job, input_model=MaterialJobInput, scope=goal, entrypoint=http),
        _spec("materials.delete", "Tombstone one material.", _delete_material, input_model=MaterialInput, scope=goal, entrypoint=http, authority=command),
        _spec("conversation.resolve_access", "Resolve student chat access.", _chat_access, scope=user, entrypoint=http),
        _spec("conversation.page_context", "Build one goal-scoped chat page.", _chat_page, scope=goal, entrypoint=http),
        _spec("conversation.history", "Load visible goal history.", _chat_history, input_model=ChatHistoryInput, scope=goal, entrypoint=http),
        _spec("adaptive.get_progress", "Read adaptive progress.", _progress, scope=goal, entrypoint=http),
        _spec("probe.submit_answer", "Submit a diagnostic probe answer.", _submit_probe, input_model=ProbeAnswerInput, scope=goal, entrypoint=http, authority=command),
        _spec("probe.skip", "Skip a diagnostic probe.", _skip_probe, input_model=ProbeIdentityInput, scope=goal, entrypoint=http, authority=command),
        _spec("memory.list_all", "List owner-scoped learner memory.", _list_memories, scope=goal, entrypoint=http),
        _spec("memory.propose_direct", "Create an explicit memory proposal.", _propose_memory, input_model=DirectMemoryProposalInput, scope=goal, entrypoint=http, authority=command),
        _spec("memory.confirm_direct", "Confirm a memory proposal.", _confirm_memory, input_model=MemoryIdentityInput, scope=goal, entrypoint=http, authority=command),
        _spec("memory.revoke_direct", "Revoke confirmed memory.", _revoke_memory, input_model=MemoryIdentityInput, scope=goal, entrypoint=http, authority=command),
        _spec("conversation.clear", "Clear and fence one conversation.", _clear_conversation, scope=goal, entrypoint=http, authority=command),
        _spec("assessment.resolve_context", "Resolve self-assessment context.", _assessment_context, input_model=GoalContextInput, scope=user, entrypoint=http),
        _spec("assessment.page_context", "Build self-assessment page context.", _assessment_page, input_model=GoalContextPayloadInput, scope=user, entrypoint=http),
        _spec("assessment.submission.start", "Start self-assessment evaluation.", _start_assessment, input_model=AssessmentSubmissionInput, scope=user, entrypoint=http, authority=command),
        _spec("assessment.submission.status", "Read self-assessment job status.", _assessment_status, input_model=JobInput, scope=user, entrypoint=http),
        _spec("assessment.target_scope.start", "Prepare a target-task assessment scope.", _start_target_scope, input_model=TargetScopeInput, scope=user, entrypoint=http, authority=command, timeout=15),
        _spec("assessment.target_scope.status", "Read target-scope preparation status.", _target_scope_status, input_model=JobInput, scope=user, entrypoint=http),
        _spec("teacher.student_report", "Build one authorized student report.", _teacher_student_report, input_model=TeacherStudentInput, scope=user, entrypoint=http),
        _spec("teacher.student_goal_report", "Build one authorized student goal report.", _teacher_student_goal_report, input_model=TeacherStudentGoalInput, scope=user, entrypoint=http),
        _spec("teacher.dashboard", "Build the teacher dashboard DTO.", _teacher_dashboard, scope=user, entrypoint=http),
        _spec("teacher.classroom_detail", "Build one classroom DTO.", _teacher_classroom_detail, input_model=ClassroomDetailInput, scope=user, entrypoint=http),
        _spec("teacher.classroom_edit", "Read one classroom edit DTO.", _teacher_classroom_edit, input_model=ClassroomIdentityInput, scope=user, entrypoint=http),
        _spec("teacher.classroom_create", "Create one teacher-owned classroom.", _teacher_classroom_create, input_model=ClassroomInput, scope=user, entrypoint=http, authority=command),
        _spec("teacher.classroom_update", "Update one teacher-owned classroom.", _teacher_classroom_update, input_model=ClassroomUpdateInput, scope=user, entrypoint=http, authority=command),
        _spec("teacher.classroom_archive", "Archive one teacher-owned classroom.", _teacher_classroom_archive, input_model=ClassroomIdentityInput, scope=user, entrypoint=http, authority=command),
        _spec("teacher.classroom_delete", "Delete one teacher-owned classroom.", _teacher_classroom_delete, input_model=ClassroomIdentityInput, scope=user, entrypoint=http, authority=command),
        _spec("teacher.invitation_create", "Invite one student to a classroom.", _teacher_invite, input_model=ClassroomInviteInput, scope=user, entrypoint=http, authority=command),
        _spec("teacher.invitation_revoke", "Revoke one classroom invitation.", _teacher_revoke_invitation, input_model=ClassroomInvitationInput, scope=user, entrypoint=http, authority=command),
        _spec("teacher.student_remove", "Remove one accepted student membership.", _teacher_remove_student, input_model=ClassroomStudentInput, scope=user, entrypoint=http, authority=command),
        _spec("student.classrooms", "List student classes and invitations.", _student_classrooms, scope=user, entrypoint=http),
        _spec(
            "student.invitation_notifications",
            "Read the authenticated student's pending classroom invitation count.",
            _student_invitation_notifications,
            scope=user,
            entrypoint=http,
        ),
        _spec("student.invitation_accept", "Accept one classroom invitation.", _student_accept_invitation, input_model=InvitationIdentityInput, scope=user, entrypoint=http, authority=command),
        _spec("student.invitation_decline", "Decline one classroom invitation.", _student_decline_invitation, input_model=InvitationIdentityInput, scope=user, entrypoint=http, authority=command),
        _spec("jobs.learning_goal.create", "Execute goal creation job.", _run_goal_creation, input_model=GoalCreationWorkerInput, scope=user, entrypoint=celery, authority=command, timeout=180),
        _spec("jobs.concept_map.generate", "Execute concept map job.", _run_concept_map, input_model=ConceptMapWorkerInput, scope=goal, entrypoint=celery, authority=command, timeout=180),
        _spec("jobs.learning_goal.refine_title", "Execute title refinement.", _run_title_refine, scope=goal, entrypoint=celery, authority=command, timeout=60),
        _spec("jobs.material.ingest", "Execute material ingestion.", _run_material_ingest, input_model=MaterialWorkerInput, scope=goal, entrypoint=celery, authority=command, timeout=180),
        _spec("jobs.material.delete", "Execute material deletion.", _run_material_delete, input_model=MaterialWorkerInput, scope=goal, entrypoint=celery, authority=command, timeout=180),
        _spec("jobs.assessment.evaluate", "Execute self-assessment job.", _run_assessment, input_model=AssessmentWorkerInput, scope=user, entrypoint=celery, authority=command, timeout=180),
        _spec("jobs.assessment.prepare_target_scope", "Validate target scope and freeze CSA criteria.", _run_target_scope, input_model=TargetScopeWorkerInput, scope=user, entrypoint=celery, authority=command, timeout=180),
        _spec("jobs.adaptive.grade", "Execute admitted adaptive grading.", _run_grading, input_model=AdaptiveGradeInput, scope=goal, entrypoint=celery, authority=command, timeout=60),
        _spec("jobs.probe.offer", "Create a deterministic Probe Offer without generating a question.", _run_probe_offer, scope=goal, entrypoint=celery, authority=command),
        _spec("jobs.probe.generate_from_offer", "Generate one formal Probe after an accepted Offer.", _run_probe_generation_from_offer, input_model=ProbeOfferGenerationInput, scope=system, entrypoint=celery, authority=command, timeout=60),
        _spec("jobs.review.scan", "Scan due adaptive reviews.", _scan_reviews, input_model=ReviewScanInput, scope=system, entrypoint=celery, authority=command, timeout=180),
        _spec("jobs.conversation.cleanup", "Reconcile conversation cleanup.", _cleanup_conversations, input_model=CleanupInput, scope=system, entrypoint=celery, authority=command, timeout=180),
        _spec("jobs.memory.expire", "Expire due learner memories.", _expire_memories, input_model=ExpireMemoryInput, scope=system, entrypoint=celery, authority=command, timeout=60),
    ]
