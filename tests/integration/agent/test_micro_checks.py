from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from learning_apps.chat.services.conversation_repository_service import ensure_active_conversation
from learning_apps.persistence.models import (
    ConceptRegistryEntry,
    LearnerBehaviorEvidence,
    LearnerMasteryState,
    LearningGoal,
    UserProfile,
)

from learning_apps.application.contracts import CapabilityError
from learning_apps.adaptive_agent.crypto import seal_json
from learning_apps.adaptive_agent.micro_checks import admit_micro_check, answer_micro_check, skip_micro_check
from learning_apps.adaptive_agent.models import AgentMicroCheck, AgentReleaseManifest, LearningAgentRun
from learning_apps.adaptive_agent.output import OptionalLearningCheck, PersonalizationBasis, TutorTurnOutput


class MicroCheckLifecycleTests(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()
        self.user = UserProfile.objects.create(
            username="micro-owner",
            email="micro-owner@example.com",
            password_hash="unused",
        )
        self.goal = LearningGoal.objects.create(
            user=self.user,
            title="Fractions",
            preference_text="Learn fractions",
            status=LearningGoal.STATUS_SELF_ASSESSMENT_COMPLETED,
        )
        self.conversation = ensure_active_conversation(self.user.username, self.goal.id)
        self.release = AgentReleaseManifest.objects.create(
            release_name="micro-tests",
            model="test-model",
            prompt_version="test-prompt",
            runtime_version="test-runtime",
            capability_catalog_version="test-catalog",
            manifest={},
            manifest_sha256="f" * 64,
            active=True,
        )
        ConceptRegistryEntry.objects.create(
            user=self.user,
            learning_goal=self.goal,
            concept_key="fraction_magnitude",
            concept_label="Fraction Magnitude",
            status=ConceptRegistryEntry.STATUS_VERIFIED,
        )
        self.mastery = LearnerMasteryState.objects.create(
            user=self.user,
            learning_goal=self.goal,
            concept_key="fraction_magnitude",
            quality_score=0.4,
            mastery_confidence=0.7,
            eligible_evidence_count=2,
        )

    def _run(self, index: int, *, skill="worked_example", origin=LearningAgentRun.ORIGIN_NORMAL) -> LearningAgentRun:
        return LearningAgentRun.objects.create(
            user=self.user,
            learning_goal=self.goal,
            conversation=self.conversation,
            conversation_generation=self.conversation.generation,
            release=self.release,
            origin=origin,
            status=LearningAgentRun.STATUS_COMPLETED,
            idempotency_key=f"micro-run-{index}-{origin}",
            request_sha256=f"{index + 1:064d}"[-64:],
            trace_id=f"micro-trace-{index}-{origin}",
            model="test-model",
            selected_skills=[{"skill_id": skill, "version": "1.1.0"}],
            adaptive_context_manifest={"concept_keys": ["fraction_magnitude"]},
            completed_at=self.now + timedelta(seconds=index),
            expires_at=self.now + timedelta(days=1),
        )

    @staticmethod
    def _output(*, prompt="If the denominator doubles, what happens to the fraction?", choice=False) -> TutorTurnOutput:
        check = OptionalLearningCheck(
            kind="choice_with_reason" if choice else "near_transfer",
            prompt=prompt,
            options=["It gets smaller", "It gets larger"] if choice else [],
            response_format="single_choice" if choice else "short_text",
            concept_key="fraction_magnitude",
            target_dimension="rationales",
            success_criteria=["States that the value becomes smaller"],
            accepted_option="It gets smaller" if choice else "",
            offer_reason="verify_transfer_after_worked_example",
        )
        return TutorTurnOutput(
            answer_markdown="A worked example.",
            teaching_strategy="Worked example",
            personalization_basis=PersonalizationBasis(
                context_policy_version="adaptive-teaching-context-v2.0.0",
                context_decision_sha256="0" * 64,
            ),
            optional_learning_check=check,
        )

    def _eligible_run(self) -> LearningAgentRun:
        runs = [self._run(index) for index in range(4)]
        return runs[-1]

    def test_frequency_requires_four_substantive_completed_runs(self) -> None:
        runs = [self._run(index) for index in range(3)]
        blocked = admit_micro_check(runs[-1], self._output())
        self.assertFalse(blocked.accepted)
        self.assertEqual(blocked.reason, "frequency_interval_not_reached")
        fourth = self._run(3)
        accepted = admit_micro_check(fourth, self._output())
        self.assertTrue(accepted.accepted)

    def test_forbidden_summary_question_is_removed_without_failing_answer(self) -> None:
        run = self._eligible_run()
        result = admit_micro_check(run, self._output(prompt="Summarize the entire answer?"))
        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "forbidden_summary_or_self_report_check")
        self.assertTrue(run.events.filter(event_type="micro_check_blocked").exists())

    def test_unverified_or_out_of_evidence_concept_is_blocked(self) -> None:
        run = self._eligible_run()
        run.adaptive_context_manifest = {"concept_keys": []}
        run.save(update_fields=["adaptive_context_manifest"])
        result = admit_micro_check(run, self._output())
        self.assertEqual(result.reason, "micro_check_concept_outside_run_evidence")

    def test_skip_suppresses_future_offers_in_same_generation(self) -> None:
        run = self._eligible_run()
        admitted = admit_micro_check(run, self._output())
        skip_micro_check(username=self.user.username, check_id=admitted.check.check_id)
        for index in range(4, 8):
            later = self._run(index)
        result = admit_micro_check(later, self._output())
        self.assertEqual(result.reason, "conversation_suppressed_after_skip")

    def test_single_choice_grading_is_deterministic_and_never_updates_mastery(self) -> None:
        run = self._eligible_run()
        admitted = admit_micro_check(run, self._output(choice=True))
        before = LearnerMasteryState.objects.get(pk=self.mastery.pk)
        with patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run"):
            check, response_run = answer_micro_check(
                username=self.user.username,
                check_id=admitted.check.check_id,
                answer="It gets smaller",
                idempotency_key="client-tab-a",
            )
        after = LearnerMasteryState.objects.get(pk=self.mastery.pk)
        self.assertEqual(check.evaluation_outcome, "correct")
        self.assertEqual(response_run.origin, LearningAgentRun.ORIGIN_MICRO_CHECK_RESPONSE)
        self.assertEqual(before.eligible_evidence_count, after.eligible_evidence_count)
        self.assertEqual(before.quality_score, after.quality_score)
        evidence = LearnerBehaviorEvidence.objects.get(event_type=LearnerBehaviorEvidence.EVENT_MICRO_CHECK_RESPONSE)
        self.assertFalse(evidence.payload["updates_long_term_mastery"])

    def test_repeat_submission_with_different_client_key_returns_same_feedback_run(self) -> None:
        run = self._eligible_run()
        admitted = admit_micro_check(run, self._output(choice=True))
        with patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run"):
            first_check, first_run = answer_micro_check(
                username=self.user.username,
                check_id=admitted.check.check_id,
                answer="It gets smaller",
                idempotency_key="tab-a",
            )
            second_check, second_run = answer_micro_check(
                username=self.user.username,
                check_id=admitted.check.check_id,
                answer="It gets smaller",
                idempotency_key="tab-b",
            )
        self.assertEqual(first_check.check_id, second_check.check_id)
        self.assertEqual(first_run.run_id, second_run.run_id)
        self.assertEqual(LearningAgentRun.objects.filter(origin=LearningAgentRun.ORIGIN_MICRO_CHECK_RESPONSE).count(), 1)

    def test_grader_exception_fails_closed_as_unclear(self) -> None:
        run = self._eligible_run()
        admitted = admit_micro_check(run, self._output())
        with patch("learning_apps.adaptive_agent.micro_checks.llm_gateway.chat_completion", side_effect=TimeoutError("timeout")), patch(
            "learning_apps.adaptive_agent.run_service.dispatch_agent_run"
        ):
            check, _response_run = answer_micro_check(
                username=self.user.username,
                check_id=admitted.check.check_id,
                answer="I think it gets smaller.",
                idempotency_key="timeout-retry",
            )
        self.assertEqual(check.evaluation_outcome, "unclear")
        self.assertEqual(check.evaluation_confidence, 0.0)

    def test_evaluating_state_can_resume_but_cannot_change_answer(self) -> None:
        run = self._eligible_run()
        admitted = admit_micro_check(run, self._output(choice=True))
        encrypted, digest = seal_json({"answer": "It gets smaller"})
        AgentMicroCheck.objects.filter(check_id=admitted.check.check_id).update(
            status=AgentMicroCheck.STATUS_EVALUATING,
            open_scope_key=None,
            encrypted_submitted_response=encrypted,
            submitted_response_sha256=digest,
        )
        with patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run"):
            check, response_run = answer_micro_check(
                username=self.user.username,
                check_id=admitted.check.check_id,
                answer="It gets smaller",
                idempotency_key="resume",
            )
        self.assertEqual(check.status, AgentMicroCheck.STATUS_ANSWERED)
        self.assertIsNotNone(response_run)
        with self.assertRaises(CapabilityError):
            answer_micro_check(
                username=self.user.username,
                check_id=check.check_id,
                answer="It gets larger",
                idempotency_key="conflict",
            )
    def test_other_user_cannot_read_answer_or_skip_check(self) -> None:
        other = UserProfile.objects.create(username="micro-other", email="micro-other@example.com", password_hash="unused")
        run = self._eligible_run()
        admitted = admit_micro_check(run, self._output())
        with self.assertRaises(CapabilityError):
            skip_micro_check(username=other.username, check_id=admitted.check.check_id)
        with self.assertRaises(CapabilityError):
            answer_micro_check(
                username=other.username,
                check_id=admitted.check.check_id,
                answer="private answer",
                idempotency_key="scope-attack",
            )
