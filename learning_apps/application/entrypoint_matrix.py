"""Versioned inventory of product entrypoints and their owning workflow.

The matrix is executable architecture evidence: tests compare it with Django
URL patterns and Celery task declarations so a new endpoint cannot silently
bypass an application boundary.
"""

from __future__ import annotations

from dataclasses import dataclass


MATRIX_VERSION = "product-entrypoints-v2.3.0"


@dataclass(frozen=True)
class EntrypointBinding:
    kind: str
    name: str
    workflow: str
    capability: str = ""
    status: str = "active"
    additional_capabilities: tuple[str, ...] = ()

    @property
    def capabilities(self) -> tuple[str, ...]:
        return tuple(item for item in (self.capability, *self.additional_capabilities) if item)


def _http(
    name: str,
    workflow: str,
    capability: str = "",
    status: str = "active",
    additional_capabilities: tuple[str, ...] = (),
):
    return EntrypointBinding("http", name, workflow, capability, status, additional_capabilities)


def _task(name: str, workflow: str, capability: str = "", status: str = "active"):
    return EntrypointBinding("celery", name, workflow, capability, status)


HTTP_BINDINGS = (
    _http("index", "public.landing", status="presentation"),
    _http("about", "public.landing", status="presentation"),
    _http("healthz", "infrastructure.liveness", status="infrastructure"),
    _http("readyz", "infrastructure.readiness", status="infrastructure"),
    _http("login", "accounts.login", status="auth_boundary"),
    _http("google_login", "accounts.google_oauth_start", status="auth_boundary"),
    _http("google_callback", "accounts.google_oauth_callback", status="auth_boundary"),
    _http("logout", "accounts.logout", status="auth_boundary"),
    _http("register", "accounts.register", status="auth_boundary"),
    _http("setup_preferences", "accounts.preferences", status="auth_boundary"),
    _http(
        "learning_goal",
        "learning.goal.index",
        "learning.list_goals",
        additional_capabilities=("learning.goal_creation.start",),
    ),
    _http("learning_goal_loading", "learning.goal.creation_page", "learning.goal_creation.status"),
    _http("learning_goal_creation_status", "learning.goal.creation_status", "learning.goal_creation.status"),
    _http("learning_goal_open", "learning.goal.open", "learning.open_goal"),
    _http("learning_goal_adaptive_progress", "adaptive.progress", "adaptive.get_progress"),
    _http(
        "learning_goal_dashboard",
        "adaptive.dashboard",
        "conversation.resolve_access",
        additional_capabilities=("learning.list_goals",),
    ),
    _http("learning_goal_adaptive_probe_submit", "probe.submit", "probe.submit_answer"),
    _http("learning_goal_adaptive_probe_skip", "probe.skip", "probe.skip"),
    _http("learning_goal_materials", "materials.list", "materials.list"),
    _http("learning_goal_material_upload", "materials.upload", "materials.upload"),
    _http("learning_goal_material_job_status", "materials.job_status", "materials.job_status"),
    _http("learning_goal_material_delete", "materials.delete", "materials.delete"),
    _http(
        "chat",
        "conversation.page",
        "conversation.resolve_access",
        additional_capabilities=("conversation.page_context",),
    ),
    _http("chat_history", "conversation.history", "conversation.history"),
    _http("learning_goal_memories", "memory.list", "memory.list_all"),
    _http("learning_goal_memory_proposal", "memory.propose", "memory.propose_direct"),
    _http("learning_goal_memory_confirm", "memory.confirm", "memory.confirm_direct"),
    _http("learning_goal_memory_revoke", "memory.revoke", "memory.revoke_direct"),
    _http("learning_goal_conversation_delete", "conversation.clear", "conversation.clear"),
    _http(
        "self_assessment",
        "assessment.page",
        "assessment.resolve_context",
        additional_capabilities=("assessment.page_context", "assessment.submission.start"),
    ),
    _http("self_assessment_target_scope_prepare", "assessment.target_scope", "assessment.target_scope.start"),
    _http("self_assessment_target_scope_status", "assessment.target_scope.status", "assessment.target_scope.status"),
    _http("self_assessment_loading", "assessment.loading", "assessment.submission.status"),
    _http("self_assessment_submission_status", "assessment.status", "assessment.submission.status"),
    _http("self_assessment_result", "assessment.result", status="presentation"),
    _http(
        "teacher_dashboard",
        "teacher.dashboard",
        "teacher.dashboard",
        additional_capabilities=("teacher.classroom_create",),
    ),
    _http(
        "teacher_classroom_detail",
        "teacher.classroom_detail",
        "teacher.classroom_detail",
        additional_capabilities=("teacher.invitation_create",),
    ),
    _http(
        "teacher_classroom_edit",
        "teacher.classroom.edit",
        "teacher.classroom_edit",
        additional_capabilities=("teacher.classroom_update",),
    ),
    _http("teacher_classroom_archive", "teacher.classroom.archive", "teacher.classroom_archive"),
    _http("teacher_classroom_delete", "teacher.classroom.delete", "teacher.classroom_delete"),
    _http("teacher_invitation_revoke", "teacher.invitation.revoke", "teacher.invitation_revoke"),
    _http("teacher_remove_student", "teacher.student.remove", "teacher.student_remove"),
    _http("teacher_student_report", "teacher.student_report", "teacher.student_report"),
    _http("teacher_student_goal_report", "teacher.student_goal_report", "teacher.student_goal_report"),
    _http("student_classrooms", "student.classrooms", "student.classrooms"),
    _http("student_invitation_accept", "student.invitation.accept", "student.invitation_accept"),
    _http("student_invitation_decline", "student.invitation.decline", "student.invitation_decline"),
    _http("adaptive_learning_mcp", "mcp.adaptive_learning", status="capability_adapter"),
    _http("create_run", "agent_run.create", status="agent_runtime"),
    _http("get_run", "agent_run.read", status="agent_runtime"),
    _http("run_events", "agent_run.events", status="agent_runtime"),
    _http("answer_clarification", "agent_run.clarification", status="agent_runtime"),
    _http("resolve_approval", "agent_run.approval", status="agent_runtime"),
    _http("cancel_run", "agent_run.cancel", status="agent_runtime"),
    _http("active_micro_check", "agent_run.micro_check.read", status="agent_runtime"),
    _http("answer_micro_check", "agent_run.micro_check.answer", status="agent_runtime"),
    _http("skip_micro_check", "agent_run.micro_check.skip", status="agent_runtime"),
    _http("active_probe_offer", "adaptive.probe_offer.read", status="agent_runtime"),
    _http("accept_probe_offer", "adaptive.probe_offer.accept", status="agent_runtime"),
    _http("snooze_probe_offer", "adaptive.probe_offer.snooze", status="agent_runtime"),
    _http("dismiss_probe_offer", "adaptive.probe_offer.dismiss", status="agent_runtime"),
)


CELERY_BINDINGS = (
    _task("learning_goal.run_creation_job", "jobs.learning_goal.create", "jobs.learning_goal.create"),
    _task("learning_goal.run_concept_map_job", "jobs.concept_map.generate", "jobs.concept_map.generate"),
    _task("learning_goal.run_title_refine_job", "jobs.learning_goal.refine_title", "jobs.learning_goal.refine_title"),
    _task("learning_goal.run_material_ingestion_job", "jobs.material.ingest", "jobs.material.ingest"),
    _task("learning_goal.run_material_deletion_job", "jobs.material.delete", "jobs.material.delete"),
    _task("self_assessment.run_submission_job", "jobs.assessment.evaluate", "jobs.assessment.evaluate"),
    _task("self_assessment.run_target_scope_job", "jobs.assessment.prepare_target_scope", "jobs.assessment.prepare_target_scope"),
    _task("adaptive_learning.grade_interaction", "jobs.adaptive.grade", "jobs.adaptive.grade"),
    _task("adaptive_learning.create_probe_offer", "jobs.probe.offer", "jobs.probe.offer"),
    _task(
        "adaptive_learning.generate_probe_from_offer",
        "jobs.probe.generate_from_offer",
        "jobs.probe.generate_from_offer",
    ),
    _task("adaptive_learning.scan_reviews", "jobs.review.scan", "jobs.review.scan"),
    _task("chat.reconcile_conversation_cleanup", "jobs.conversation.cleanup", "jobs.conversation.cleanup"),
    _task("chat.expire_learner_memories", "jobs.memory.expire", "jobs.memory.expire"),
    _task("adaptive_agent.execute_run", "agent_run.execute", status="agent_runtime"),
    _task("adaptive_agent.expire_stale_runs", "agent_run.expire", status="agent_runtime"),
    _task("adaptive_agent.recover_stale_runs", "agent_run.recover", status="agent_runtime"),
)


ALL_BINDINGS = (*HTTP_BINDINGS, *CELERY_BINDINGS)
