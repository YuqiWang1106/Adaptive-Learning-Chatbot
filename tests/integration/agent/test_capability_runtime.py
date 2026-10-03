from __future__ import annotations

from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from learning_apps.chat.services.conversation_repository_service import ensure_active_conversation
from learning_apps.persistence.models import LearningGoal, UserProfile

from learning_apps.adaptive_agent.tool_catalog import capability_registry
from learning_apps.application.contracts import CapabilityContext, CapabilityEntrypoint, CapabilityError, stable_sha256
from learning_apps.application.executor import CapabilityExecutor, create_execution_run
from learning_apps.application.models import CapabilityInvocation


class CapabilityRuntimeTests(TestCase):
    def setUp(self):
        self.user = UserProfile.objects.create(
            username="cap-student",
            email="cap-student@example.com",
            password_hash="unused",
            preferences_completed=True,
        )
        self.goal = LearningGoal.objects.create(
            user=self.user,
            title="Algebra",
            preference_text="Learn algebra",
            domain="mathematics",
            branch="algebra",
            status=LearningGoal.STATUS_SELF_ASSESSMENT_COMPLETED,
        )
        self.conversation = ensure_active_conversation(self.user.username, self.goal.id)
        self.registry = capability_registry()
        self.execution = create_execution_run(
            user=self.user,
            goal=self.goal,
            conversation=self.conversation,
            entrypoint=CapabilityEntrypoint.HTTP,
            workflow="test",
            trace_id="trace-capability",
        )
        self.context = CapabilityContext(
            execution_id=self.execution.execution_id,
            user_id=self.user.user_id,
            username=self.user.username,
            learning_goal_id=self.goal.id,
            conversation_key=self.conversation.conversation_key,
            conversation_generation=self.conversation.generation,
            trace_id="trace-capability",
            entrypoint=CapabilityEntrypoint.HTTP,
            request_sha256=stable_sha256("request"),
        )

    def test_read_capability_has_strict_server_injected_scope_and_audit(self):
        result = CapabilityExecutor(self.registry).execute(
            "learning.get_goal_context",
            {},
            context=self.context,
            idempotency_key="cap-read-1",
        )
        self.assertEqual(result.payload["title"], "Algebra")
        self.assertEqual(result.payload["conversation_generation"], self.conversation.generation)
        invocation = self.execution.invocations.get()
        self.assertEqual(invocation.authority, "read")
        self.assertFalse(invocation.mastery_write_authorized)
        self.assertEqual(len(invocation.input_sha256), 64)

        with self.assertRaisesRegex(CapabilityError, "server_scope_argument_forbidden"):
            CapabilityExecutor(self.registry).execute(
                "learning.get_goal_context",
                {"user_id": self.user.user_id},
                context=self.context,
                idempotency_key="cap-read-injection",
            )

    def test_agent_cannot_call_command_or_bypass_proposal_approval(self):
        agent_context = CapabilityContext(
            **{
                **self.context.__dict__,
                "entrypoint": CapabilityEntrypoint.AGENT,
                "agent_run_id": "missing-run",
            }
        )
        with self.assertRaisesRegex(CapabilityError, "approval_required_before_execution"):
            CapabilityExecutor(self.registry).execute(
                "quiz.propose",
                {"focus": "linear equations", "question_count": 2, "concept_key": ""},
                context=agent_context,
                idempotency_key="cap-proposal-no-approval",
            )
        with self.assertRaisesRegex(CapabilityError, "capability_not_found"):
            CapabilityExecutor(self.registry).execute(
                "mastery.update",
                {},
                context=self.context,
                idempotency_key="cap-master-invalid",
            )

    def test_goal_and_conversation_fencing_prevents_cross_scope_reads(self):
        other = UserProfile.objects.create(username="cap-other", email="cap-other@example.com", password_hash="unused")
        other_goal = LearningGoal.objects.create(user=other, preference_text="Private")
        bad_context = CapabilityContext(
            **{**self.context.__dict__, "learning_goal_id": other_goal.id}
        )
        with self.assertRaisesRegex(CapabilityError, "scope_fenced"):
            CapabilityExecutor(self.registry).execute(
                "learning.get_goal_context", {}, context=bad_context, idempotency_key="cross-scope"
            )

    def test_idempotent_result_replays_across_execution_runs_without_second_handler_call(self):
        executor = CapabilityExecutor(self.registry)
        first = executor.execute(
            "learning.get_goal_context", {}, context=self.context, idempotency_key="global-read-replay"
        )
        second_execution = create_execution_run(
            user=self.user,
            goal=self.goal,
            conversation=self.conversation,
            entrypoint=CapabilityEntrypoint.HTTP,
            workflow="retry",
            trace_id="trace-retry",
        )
        second_context = CapabilityContext(
            **{
                **self.context.__dict__,
                "execution_id": second_execution.execution_id,
                "trace_id": "trace-retry",
            }
        )
        replay = executor.execute(
            "learning.get_goal_context", {}, context=second_context, idempotency_key="global-read-replay"
        )
        self.assertTrue(replay.replayed)
        self.assertEqual(replay.invocation_id, first.invocation_id)
        self.assertEqual(replay.payload, first.payload)
        self.assertEqual(self.execution.invocations.count(), 1)
        self.assertEqual(second_execution.invocations.count(), 0)

    def test_expired_pending_invocation_lease_is_reclaimed_after_worker_crash(self):
        executor = CapabilityExecutor(self.registry)
        first = executor.execute(
            "learning.get_goal_context", {}, context=self.context, idempotency_key="crash-reclaim"
        )
        CapabilityInvocation.objects.filter(invocation_id=first.invocation_id).update(
            status=CapabilityInvocation.STATUS_PENDING,
            reason_code="simulated_worker_crash",
            lease_expires_at=timezone.now() - timedelta(seconds=1),
        )
        retry_execution = create_execution_run(
            user=self.user,
            goal=self.goal,
            conversation=self.conversation,
            entrypoint=CapabilityEntrypoint.HTTP,
            workflow="crash-retry",
            trace_id="trace-crash-retry",
        )
        retry_context = CapabilityContext(
            **{**self.context.__dict__, "execution_id": retry_execution.execution_id}
        )
        result = executor.execute(
            "learning.get_goal_context", {}, context=retry_context, idempotency_key="crash-reclaim"
        )
        invocation = CapabilityInvocation.objects.get(invocation_id=first.invocation_id)
        self.assertFalse(result.replayed)
        self.assertEqual(invocation.status, CapabilityInvocation.STATUS_SUCCEEDED)
        self.assertEqual(invocation.attempt_count, 2)
        self.assertEqual(invocation.execution_id, retry_execution.execution_id)
