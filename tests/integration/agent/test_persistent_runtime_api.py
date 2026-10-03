from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from learning_apps.chat.services.conversation_repository_service import clear_conversation
from learning_apps.persistence.models import LearningGoal, UserProfile

from learning_apps.adaptive_agent.checkpoints import load_checkpoint
from learning_apps.adaptive_agent.models import AgentInterruption, AgentRunCheckpoint, LearningAgentRun
from learning_apps.adaptive_agent.run_service import create_agent_run, recover_stale_agent_runs, resolve_interruption
from learning_apps.adaptive_agent.runtime import execute_agent_run


class PersistentAgentRuntimeApiTests(TestCase):
    def setUp(self):
        self.auth_user = get_user_model().objects.create_user(username="agent-api", password="Pass!23456789")
        self.user = UserProfile.objects.create(
            username="agent-api",
            email="agent-api@example.com",
            password_hash="unused",
            preferences_completed=True,
        )
        self.goal = LearningGoal.objects.create(
            user=self.user,
            title="Biology",
            preference_text="Learn cells",
            domain="biology",
            status=LearningGoal.STATUS_SELF_ASSESSMENT_COMPLETED,
        )
        self.client.force_login(self.auth_user)

    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_create_get_and_cancel_run_are_owner_scoped(self, dispatch):
        with self.captureOnCommitCallbacks(execute=True):
            response = self.client.post(
                reverse("adaptive_agent:create_run", kwargs={"goal_id": self.goal.id}),
                data='{"question":"Why do cells need membranes?","idempotency_key":"api-test-1"}',
                content_type="application/json",
            )
        self.assertEqual(response.status_code, 202)
        run_id = response.json()["run_id"]
        dispatch.assert_called_once_with(run_id)
        run = LearningAgentRun.objects.get(run_id=run_id)
        self.assertEqual(run.status, LearningAgentRun.STATUS_QUEUED)
        self.assertNotIn("Why do cells", repr(run.__dict__))
        self.assertEqual(load_checkpoint(run)["question"], "Why do cells need membranes?")

        get_response = self.client.get(reverse("adaptive_agent:get_run", kwargs={"run_id": run_id}))
        self.assertEqual(get_response.status_code, 200)
        cancel = self.client.post(reverse("adaptive_agent:cancel_run", kwargs={"run_id": run_id}))
        self.assertEqual(cancel.status_code, 200)
        self.assertEqual(cancel.json()["status"], "cancelled")

        other_auth = get_user_model().objects.create_user(username="agent-other", password="Pass!23456789")
        self.client.force_login(other_auth)
        self.assertEqual(self.client.get(reverse("adaptive_agent:get_run", kwargs={"run_id": run_id})).status_code, 404)

    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_http_create_run_requires_completed_self_assessment(self, dispatch) -> None:
        self.goal.status = LearningGoal.STATUS_GOAL_SUBMITTED
        self.goal.save(update_fields=["status"])

        response = self.client.post(
            reverse("adaptive_agent:create_run", kwargs={"goal_id": self.goal.id}),
            data='{"question":"Skip the assessment","idempotency_key":"assessment-bypass"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.json()["error"], "self_assessment_required")
        self.assertFalse(LearningAgentRun.objects.filter(idempotency_key="assessment-bypass").exists())
        dispatch.assert_not_called()

    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_clarification_resume_is_idempotent_and_checkpoint_stays_encrypted(self, dispatch):
        run, _ = create_agent_run(
            username=self.user.username,
            learning_goal_id=self.goal.id,
            question="Compare these two things",
            idempotency_key="clarify-test",
        )
        run.status = LearningAgentRun.STATUS_WAITING_CLARIFICATION
        run.save(update_fields=["status"])
        interruption = AgentInterruption.objects.create(
            run=run,
            kind=AgentInterruption.KIND_CLARIFICATION,
            public_payload={"question": "Which two things?"},
            idempotency_key="clarify-once",
            expires_at=timezone.now() + timedelta(hours=1),
        )
        with self.captureOnCommitCallbacks(execute=True):
            first = resolve_interruption(
                username=self.user.username,
                run_id=run.run_id,
                interruption_id=interruption.interruption_id,
                kind=AgentInterruption.KIND_CLARIFICATION,
                response="Plant and animal cells",
            )
        self.assertEqual(first.status, LearningAgentRun.STATUS_RESUMING)
        interruption.refresh_from_db()
        self.assertEqual(interruption.status, AgentInterruption.STATUS_ANSWERED)
        self.assertNotIn("Plant and animal", interruption.encrypted_response)
        with self.captureOnCommitCallbacks(execute=True):
            second = resolve_interruption(
                username=self.user.username,
                run_id=run.run_id,
                interruption_id=interruption.interruption_id,
                kind=AgentInterruption.KIND_CLARIFICATION,
                response="Different replay",
            )
        self.assertEqual(second.run_id, run.run_id)

    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_conversation_clear_expires_all_unfinished_runs(self, _dispatch):
        run, _ = create_agent_run(
            username=self.user.username,
            learning_goal_id=self.goal.id,
            question="Teach me cells",
            idempotency_key="clear-fence",
        )
        clear_conversation(self.user.username, self.goal.id)
        run.refresh_from_db()
        self.assertEqual(run.status, LearningAgentRun.STATUS_EXPIRED)
        self.assertEqual(run.error_code, "conversation_cleared")
        self.assertFalse(AgentRunCheckpoint.objects.filter(run=run).exists())

    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_stale_running_worker_is_requeued_for_same_run(self, dispatch):
        run, _ = create_agent_run(
            username=self.user.username,
            learning_goal_id=self.goal.id,
            question="Recover this run",
            idempotency_key="worker-crash-recovery",
        )
        dispatch.reset_mock()
        LearningAgentRun.objects.filter(run_id=run.run_id).update(
            status=LearningAgentRun.STATUS_RUNNING,
            updated_at=timezone.now() - timedelta(minutes=10),
        )
        recovered = recover_stale_agent_runs(limit=10)
        run.refresh_from_db()
        self.assertEqual(recovered, 1)
        self.assertEqual(run.status, LearningAgentRun.STATUS_QUEUED)
        self.assertEqual(run.error_code, "worker_recovery_queued")
        dispatch.assert_called_once_with(run.run_id)

    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_corrupt_checkpoint_fails_run_and_emits_terminal_trace(self, _dispatch):
        run, _ = create_agent_run(
            username=self.user.username,
            learning_goal_id=self.goal.id,
            question="Corrupt checkpoint fixture",
            idempotency_key="corrupt-checkpoint",
        )
        checkpoint = AgentRunCheckpoint.objects.get(run=run)
        checkpoint.encrypted_state = "not-a-valid-fernet-token"
        checkpoint.save(update_fields=["encrypted_state"])
        execute_agent_run(run.run_id)
        run.refresh_from_db()
        self.assertEqual(run.status, LearningAgentRun.STATUS_FAILED)
        self.assertEqual(run.error_code, "checkpoint_decryption_failed")
        self.assertTrue(run.events.filter(event_type="run_failed").exists())

    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_release_artifact_drift_fails_closed_before_model_execution(self, _dispatch):
        run, _ = create_agent_run(
            username=self.user.username,
            learning_goal_id=self.goal.id,
            question="Release drift fixture",
            idempotency_key="release-drift",
        )
        manifest = dict(run.release.manifest)
        manifest["prompt_sha256"] = "0" * 64
        run.release.manifest = manifest
        run.release.save(update_fields=["manifest"])
        execute_agent_run(run.run_id)
        run.refresh_from_db()
        self.assertEqual(run.status, LearningAgentRun.STATUS_FAILED)
        self.assertEqual(run.error_code, "agent_release_artifact_unavailable")
        self.assertEqual(run.model_request_count, 0)
