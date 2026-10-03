from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from learning_apps.adaptive_learning.mastery_evidence_policy import evaluate_mastery_evidence
from learning_apps.adaptive_learning.probe_offer_service import (
    accept_probe_offer,
    create_initial_calibration_offer,
)
from learning_apps.chat.services.conversation_repository_service import ensure_active_conversation
from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    AdaptiveProbe,
    AdaptiveProbeOffer,
    ConceptRegistryEntry,
    LearnerMasteryState,
    LearnerPerceivedState,
    LearningGoal,
    SelfAssessment,
    SelfAssessmentEvidenceDecision,
    UserProfile,
)

from learning_apps.adaptive_agent.adaptive_context import ADAPTIVE_CONTEXT_POLICY_VERSION, concept_teaching_state
from learning_apps.adaptive_agent.tool_catalog import capability_registry
from learning_apps.adaptive_agent.tool_repositories import learning_priorities, resolve_current_concept
from learning_apps.application.contracts import CapabilityContext, CapabilityEntrypoint, CapabilityError
from learning_apps.adaptive_agent.models import AgentInterruption, AgentReleaseManifest, AgentRunCheckpoint, LearningAgentRun
from learning_apps.adaptive_agent.output import PersonalizationBasis, TutorTurnOutput
from learning_apps.adaptive_agent.release import current_release_manifest
from learning_apps.adaptive_agent.sdk_adapter import _record_evidence
from learning_apps.adaptive_agent.turn_commit import _validate_personalization_and_skill_evidence


ADMITTED = {
    "mastery_evidence_admission": "accepted",
    "mastery_policy_version": "mastery_evidence_policy_v2",
    "mastery_update_accepted": True,
}


class P68AdaptiveTeachingContractTests(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()
        self.user = UserProfile.objects.create(
            username="p68-owner",
            email="p68-owner@example.com",
            password_hash="unused",
        )
        self.goal = LearningGoal.objects.create(
            user=self.user,
            title="Fractions",
            preference_text="Learn fraction operations",
            domain="mathematics",
            branch="number",
            status=LearningGoal.STATUS_SELF_ASSESSMENT_COMPLETED,
        )
        self.conversation = ensure_active_conversation(self.user.username, self.goal.id)
        self.release = AgentReleaseManifest.objects.create(
            release_name="p68-contract",
            model="test-model",
            prompt_version="test-prompt",
            runtime_version="test-runtime",
            capability_catalog_version="test-catalog",
            manifest={},
            manifest_sha256="9" * 64,
            active=True,
        )
        self.concept = self._concept("fraction_division", "Fraction Division")
        self.run = self._run(status=LearningAgentRun.STATUS_COMPLETED)

    def _concept(self, key: str, label: str) -> ConceptRegistryEntry:
        return ConceptRegistryEntry.objects.create(
            user=self.user,
            learning_goal=self.goal,
            concept_key=key,
            concept_label=label,
            status=ConceptRegistryEntry.STATUS_VERIFIED,
        )

    def _run(self, *, status: str, index: int = 1, focus: str = "fraction_division") -> LearningAgentRun:
        return LearningAgentRun.objects.create(
            user=self.user,
            learning_goal=self.goal,
            conversation=self.conversation,
            conversation_generation=self.conversation.generation,
            release=self.release,
            origin=LearningAgentRun.ORIGIN_NORMAL,
            status=status,
            idempotency_key=f"p68-run-{index}-{status}",
            request_sha256=f"{index:064d}"[-64:],
            trace_id=f"p68-trace-{index}",
            model="test-model",
            adaptive_context_manifest={
                "policy_version": ADAPTIVE_CONTEXT_POLICY_VERSION,
                "context_decision_sha256": "a" * 64,
                "focus_concept_key": focus,
                "concept_keys": [focus] if focus else [],
                "observed_status": "none",
                "perceived_status": "available",
                "adaptation_level": "soft",
            },
            completed_at=self.now if status == LearningAgentRun.STATUS_COMPLETED else None,
            expires_at=self.now + timedelta(days=1),
        )

    def _perceived(self, scores=None) -> LearnerPerceivedState:
        assessment = SelfAssessment.objects.create(
            username=self.user.username,
            learning_goal=self.goal,
            structured_report={},
        )
        decision = SelfAssessmentEvidenceDecision.objects.create(
            user=self.user,
            learning_goal=self.goal,
            self_assessment=assessment,
            status=SelfAssessmentEvidenceDecision.STATUS_ACCEPTED,
            reason_code="accepted",
            policy_version="test",
            assessment_sha256="1" * 64,
            structured_report_sha256="2" * 64,
            retrieval_bundle_sha256="3" * 64,
            scope_sha256="4" * 64,
            taxonomy_sha256="5" * 64,
            lifecycle_bundle_sha256="6" * 64,
            decision_sha256="7" * 64,
            prompt_version="test",
            idempotency_key="8" * 64,
            mastery_write_authorized=False,
        )
        return LearnerPerceivedState.objects.create(
            user=self.user,
            learning_goal=self.goal,
            source_assessment=assessment,
            evidence_decision=decision,
            dimension_scores=scores or {
                "facts": 0.5,
                "procedures": 0.2,
                "strategies": 0.7,
                "rationales": 0.6,
            },
            taxonomy_sha256="5" * 64,
            authority=LearnerPerceivedState.AUTHORITY_GUIDANCE_ONLY,
            mastery_write_authorized=False,
        )

    def _state(self, key="fraction_division", quality=0.35) -> LearnerMasteryState:
        return LearnerMasteryState.objects.create(
            user=self.user,
            learning_goal=self.goal,
            concept_key=key,
            quality_score=quality,
            weakest_dimension="procedures",
            eligible_evidence_count=2,
            policy_version="mastery_evidence_policy_v2",
        )

    def _probe(self, probe_id: int, *, key="fraction_division", target="procedures"):
        return AdaptiveInteractionEvent.objects.create(
            user=self.user,
            learning_goal=self.goal,
            concept_key=key,
            source=AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE,
            accuracy_score=0.4,
            dimension_scores={dimension: 0.4 for dimension in ("facts", "procedures", "strategies", "rationales")},
            confidence=0.9,
            metadata={**ADMITTED, "probe_id": probe_id, "target_dimension": target},
        )

    def _capability_context(self) -> CapabilityContext:
        return CapabilityContext(
            execution_id="p68-execution",
            user_id=self.user.user_id,
            username=self.user.username,
            learning_goal_id=self.goal.id,
            conversation_key=self.conversation.conversation_key,
            conversation_generation=self.conversation.generation,
            trace_id="p68-trace",
            entrypoint=CapabilityEntrypoint.AGENT,
            request_sha256="f" * 64,
            trusted_user_text="Explain fraction division",
            agent_run_id=self.run.run_id,
        )

    def test_self_assessment_source_is_never_admitted_to_mastery(self) -> None:
        decision = evaluate_mastery_evidence(
            source=AdaptiveInteractionEvent.SOURCE_SELF_ASSESSMENT_BASELINE,
            confidence=1.0,
            dimension_scores={dimension: 1.0 for dimension in ("facts", "procedures", "strategies", "rationales")},
        )
        self.assertFalse(decision.accepted)
        self.assertEqual(decision.reason_code, "source_not_admitted")

    def test_agent_catalog_exposes_semantic_tools_not_raw_snapshot(self) -> None:
        names = {spec.name for spec in capability_registry().agent_tools()}
        self.assertNotIn("adaptive.get_learner_snapshot", names)
        self.assertTrue(
            {
                "learning.resolve_current_concept",
                "adaptive.get_teaching_state",
                "adaptive.list_learning_priorities",
            }.issubset(names)
        )

    def test_teaching_state_marks_metrics_as_non_probability(self) -> None:
        self._state()
        self._probe(1)
        payload = concept_teaching_state(
            user_id=self.user.user_id,
            learning_goal_id=self.goal.id,
            concept_key="fraction_division",
            now=self.now,
        )
        self.assertEqual(payload["observed_status"], "emerging")
        self.assertEqual(payload["diagnostic_metrics"]["semantics"], "uncalibrated_diagnostics_not_probability")
        self.assertEqual(payload["adaptation"]["level"], "soft")

    def test_concept_resolution_abstains_for_non_registry_key_without_creating_it(self) -> None:
        resolution = SimpleNamespace(
            decision_status="accepted",
            admission_status="accepted",
            relation="same",
            mastery_eligible=True,
            concept_key="invented_key",
            concept_label="Invented Key",
            decision_id="decision-test",
            taxonomy_fingerprint="f" * 64,
            admission_reason="",
        )
        with patch(
            "learning_apps.adaptive_agent.tool_repositories.resolve_concept_identity",
            return_value=resolution,
        ):
            payload = resolve_current_concept(self._capability_context())
        self.assertEqual(payload["status"], "abstained")
        self.assertFalse(
            ConceptRegistryEntry.objects.filter(
                user=self.user,
                learning_goal=self.goal,
                concept_key="invented_key",
            ).exists()
        )

    def test_learning_priorities_returns_at_most_three_probe_backed_items(self) -> None:
        self._state()
        self._probe(1)
        for index in range(1, 5):
            key = f"verified_{index}"
            self._concept(key, f"Verified {index}")
            self._state(key, quality=0.1 + index / 100)
            self._probe(100 + index, key=key)
        payload = learning_priorities(self._capability_context())
        self.assertEqual(len(payload["priorities"]), 3)
        self.assertTrue(all(item["observed_status"] == "emerging" for item in payload["priorities"]))

    def test_calibration_offer_requires_completed_relevant_turn_and_perceived_state(self) -> None:
        queued = self._run(status=LearningAgentRun.STATUS_QUEUED, index=2)
        self._perceived()
        self.assertIsNone(create_initial_calibration_offer(queued))
        self.assertIsNotNone(create_initial_calibration_offer(self.run))

    def test_calibration_offer_uses_lowest_perceived_dimension_with_fixed_tie_order(self) -> None:
        self._perceived({"facts": 0.2, "procedures": 0.2, "strategies": 0.5, "rationales": 0.6})
        offer = create_initial_calibration_offer(self.run)
        self.assertEqual(offer.due_trigger, "initial_calibration")
        self.assertEqual(offer.target_concept_key, "fraction_division")
        self.assertEqual(offer.target_dimension, "facts")
        self.assertEqual(AdaptiveProbe.objects.count(), 0)

    def test_calibration_offer_can_use_single_teaching_state_tool_focus(self) -> None:
        self._perceived()
        self.run.adaptive_context_manifest = {
            **self.run.adaptive_context_manifest,
            "focus_concept_key": "",
            "concept_keys": [],
        }
        self.run.evidence_manifest = [
            {"category": "teaching_state", "concept_keys": ["fraction_division"], "observed_status": "none"}
        ]
        self.run.save(update_fields=["adaptive_context_manifest", "evidence_manifest"])
        offer = create_initial_calibration_offer(self.run)
        self.assertIsNotNone(offer)
        self.assertEqual(offer.target_concept_key, "fraction_division")

    def test_calibration_offer_is_idempotent_and_not_created_after_probe(self) -> None:
        self._perceived()
        first = create_initial_calibration_offer(self.run)
        second = create_initial_calibration_offer(self.run)
        self.assertEqual(first.offer_id, second.offer_id)
        self.assertEqual(AdaptiveProbeOffer.objects.count(), 1)
        first.status = AdaptiveProbeOffer.STATUS_DISMISSED
        first.open_scope_key = None
        first.save(update_fields=["status", "open_scope_key", "updated_at"])
        self._probe(1)
        self.assertIsNone(create_initial_calibration_offer(self.run))

    @patch("learning_apps.adaptive_learning.probe_offer_service._dispatch_offer_generation")
    def test_initial_calibration_generates_nothing_until_explicit_accept(self, dispatch) -> None:
        self._perceived()
        offer = create_initial_calibration_offer(self.run)
        self.assertEqual(AdaptiveProbe.objects.count(), 0)
        accepted = accept_probe_offer(username=self.user.username, offer_id=offer.offer_id)
        self.assertEqual(accepted.status, AdaptiveProbeOffer.STATUS_ACCEPTED)
        dispatch.assert_called_once_with(offer.offer_id)

    @patch("learning_apps.adaptive_learning.probe_offer_service._dispatch_offer_generation")
    def test_initial_calibration_revalidates_latest_perceived_dimension_on_accept(self, dispatch) -> None:
        perceived = self._perceived(
            {"facts": 0.6, "procedures": 0.2, "strategies": 0.7, "rationales": 0.8}
        )
        offer = create_initial_calibration_offer(self.run)
        self.assertEqual(offer.target_dimension, "procedures")
        perceived.dimension_scores = {
            "facts": 0.1,
            "procedures": 0.5,
            "strategies": 0.7,
            "rationales": 0.8,
        }
        perceived.save(update_fields=["dimension_scores", "updated_at"])

        accepted = accept_probe_offer(username=self.user.username, offer_id=offer.offer_id)
        self.assertEqual(accepted.status, AdaptiveProbeOffer.STATUS_ACCEPTED)
        self.assertEqual(accepted.target_dimension, "facts")
        dispatch.assert_called_once_with(offer.offer_id)
        self.assertEqual(AdaptiveProbe.objects.count(), 0)

    def test_perceived_guidance_rejects_definitive_claim_and_requires_limitation(self) -> None:
        basis = PersonalizationBasis(
            status="perceived_guidance",
            context_policy_version=ADAPTIVE_CONTEXT_POLICY_VERSION,
            context_decision_sha256="a" * 64,
            adaptation_level="soft",
            concept_keys=["fraction_division"],
            evidence_categories=["teaching_context"],
            limitation="This is self-reported guidance, not observed mastery.",
        )
        output = TutorTurnOutput(
            answer_markdown="Your self-assessment proves a confirmed weakness in fraction division.",
            teaching_strategy="Scaffolded explanation",
            personalization_basis=basis,
        )
        with self.assertRaisesRegex(CapabilityError, "unsupported_definitive_personalization"):
            _validate_personalization_and_skill_evidence(self.run, output)
        output.answer_markdown = "We can use your self-report as a tentative starting point."
        output.personalization_basis.limitation = ""
        with self.assertRaisesRegex(CapabilityError, "personalization_limitation_required"):
            _validate_personalization_and_skill_evidence(self.run, output)

    def test_generic_tool_evidence_uses_declared_category_at_commit(self) -> None:
        _record_evidence(
            self.run.run_id,
            "learning.get_goal_context",
            "goal_context",
            {"title": self.goal.title},
        )
        self.run.refresh_from_db()
        self.assertEqual(
            self.run.evidence_manifest,
            [
                {
                    "category": "goal_context",
                    "tool": "learning.get_goal_context",
                }
            ],
        )
        output = TutorTurnOutput(
            answer_markdown="Let's work through fraction division.",
            teaching_strategy="Goal-aligned explanation",
            personalization_basis=PersonalizationBasis(
                status="goal_only",
                context_policy_version=ADAPTIVE_CONTEXT_POLICY_VERSION,
                context_decision_sha256="a" * 64,
                adaptation_level="none",
                evidence_categories=["goal_context"],
                limitation="No observed learner evidence is available yet.",
            ),
        )
        _validate_personalization_and_skill_evidence(self.run, output)

    def test_emerging_probe_rejects_stable_long_term_claim(self) -> None:
        self.run.adaptive_context_manifest = {
            **self.run.adaptive_context_manifest,
            "observed_status": "emerging",
            "perceived_status": "none",
            "adaptation_level": "soft",
        }
        self.run.save(update_fields=["adaptive_context_manifest"])
        output = TutorTurnOutput(
            answer_markdown="This shows a stable weakness in fraction division.",
            teaching_strategy="Cautious explanation",
            personalization_basis=PersonalizationBasis(
                status="observed_evidence",
                context_policy_version=ADAPTIVE_CONTEXT_POLICY_VERSION,
                context_decision_sha256="a" * 64,
                adaptation_level="soft",
                concept_keys=["fraction_division"],
                evidence_categories=["teaching_context"],
                limitation="Only one recent Probe is available.",
            ),
        )
        with self.assertRaisesRegex(CapabilityError, "unsupported_long_term_personalization"):
            _validate_personalization_and_skill_evidence(self.run, output)

    def test_new_release_expires_old_interrupted_run_and_deletes_checkpoint(self) -> None:
        old_run = self._run(status=LearningAgentRun.STATUS_WAITING_CLARIFICATION, index=9)
        AgentRunCheckpoint.objects.create(
            run=old_run,
            encrypted_state="encrypted",
            state_sha256="b" * 64,
            expires_at=self.now + timedelta(hours=1),
        )
        interruption = AgentInterruption.objects.create(
            run=old_run,
            kind=AgentInterruption.KIND_CLARIFICATION,
            status=AgentInterruption.STATUS_PENDING,
            public_payload={"question": "Which concept?"},
            idempotency_key="p68-old-interruption",
            expires_at=self.now + timedelta(hours=1),
        )
        current_release_manifest()
        old_run.refresh_from_db()
        interruption.refresh_from_db()
        self.assertEqual(old_run.status, LearningAgentRun.STATUS_EXPIRED)
        self.assertEqual(old_run.error_code, "agent_release_retired")
        self.assertEqual(interruption.status, AgentInterruption.STATUS_EXPIRED)
        self.assertFalse(AgentRunCheckpoint.objects.filter(run=old_run).exists())
