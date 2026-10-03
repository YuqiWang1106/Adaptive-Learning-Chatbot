from __future__ import annotations

import json
import time
from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from learning_apps.chat.services.conversation_repository_service import ensure_active_conversation
from learning_apps.persistence.models import (
    AdaptiveInteractionEvent,
    ChatConceptSignal,
    ConceptRegistryEntry,
    LearnerMasteryState,
    LearnerPerceivedState,
    LearningGoal,
    SelfAssessment,
    SelfAssessmentEvidenceDecision,
    UserProfile,
)

from learning_apps.adaptive_agent.adaptive_context import ADAPTIVE_CONTEXT_MAX_CHARS, assemble_adaptive_context
from learning_apps.adaptive_agent.models import AgentReleaseManifest, LearningAgentRun


ADMITTED = {
    "mastery_evidence_admission": "accepted",
    "mastery_policy_version": "mastery_evidence_policy_v2",
    "mastery_update_accepted": True,
}


class AdaptiveContextContractTests(TestCase):
    def setUp(self) -> None:
        self.now = timezone.now()
        self.user = UserProfile.objects.create(
            username="context-owner",
            email="context-owner@example.com",
            password_hash="unused",
        )
        self.goal = LearningGoal.objects.create(
            user=self.user,
            title="Middle School Mathematics",
            preference_text="Learn fractions",
            domain="mathematics",
            branch="number",
            status=LearningGoal.STATUS_SELF_ASSESSMENT_COMPLETED,
        )
        self.conversation = ensure_active_conversation(self.user.username, self.goal.id)
        self.release = AgentReleaseManifest.objects.create(
            release_name="context-tests",
            model="test-model",
            prompt_version="test-prompt",
            runtime_version="test-runtime",
            capability_catalog_version="test-catalog",
            manifest={},
            manifest_sha256="c" * 64,
            active=True,
        )
        self.run = LearningAgentRun.objects.create(
            user=self.user,
            learning_goal=self.goal,
            conversation=self.conversation,
            conversation_generation=self.conversation.generation,
            release=self.release,
            status=LearningAgentRun.STATUS_QUEUED,
            idempotency_key="context-run",
            request_sha256="d" * 64,
            trace_id="context-trace",
            model="test-model",
            expires_at=self.now + timedelta(days=1),
        )

    def _concept(self, key: str, label: str, *, aliases=None, verified=True) -> ConceptRegistryEntry:
        return ConceptRegistryEntry.objects.create(
            user=self.user,
            learning_goal=self.goal,
            concept_key=key,
            concept_label=label,
            aliases=aliases or [],
            status=(ConceptRegistryEntry.STATUS_VERIFIED if verified else ConceptRegistryEntry.STATUS_PROVISIONAL),
        )

    def _state(self, key: str, *, quality=0.3, weakest="procedures") -> LearnerMasteryState:
        return LearnerMasteryState.objects.create(
            user=self.user,
            learning_goal=self.goal,
            concept_key=key,
            facts_mastery=0.55,
            procedures_mastery=0.25,
            strategies_mastery=0.35,
            rationales_mastery=0.45,
            dimension_mastery_score=0.4,
            quality_score=quality,
            weakest_dimension=weakest,
            mastery_confidence=0.72,
            eligible_evidence_count=2,
        )

    def _probe(self, key: str, probe_id: int, *, target="procedures", score=0.35):
        return AdaptiveInteractionEvent.objects.create(
            user=self.user,
            learning_goal=self.goal,
            concept_key=key,
            source=AdaptiveInteractionEvent.SOURCE_PROBE_RESPONSE,
            accuracy_score=score,
            dimension_scores={dimension: score for dimension in ("facts", "procedures", "strategies", "rationales")},
            confidence=0.9,
            metadata={**ADMITTED, "probe_id": probe_id, "target_dimension": target},
        )

    def _perceived(self, *, scores=None) -> LearnerPerceivedState:
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
            assessment_sha256="a" * 64,
            structured_report_sha256="b" * 64,
            retrieval_bundle_sha256="c" * 64,
            scope_sha256="d" * 64,
            taxonomy_sha256="e" * 64,
            lifecycle_bundle_sha256="f" * 64,
            decision_sha256="1" * 64,
            prompt_version="test",
            idempotency_key="2" * 64,
            mastery_write_authorized=False,
        )
        return LearnerPerceivedState.objects.create(
            user=self.user,
            learning_goal=self.goal,
            source_assessment=assessment,
            evidence_decision=decision,
            dimension_scores=scores or {"facts": 0.4, "procedures": 0.3, "strategies": 0.6, "rationales": 0.7},
            taxonomy_sha256="e" * 64,
            authority=LearnerPerceivedState.AUTHORITY_GUIDANCE_ONLY,
            mastery_write_authorized=False,
        )

    def test_no_probe_is_explicitly_none_without_raw_metrics(self) -> None:
        self._concept("fraction_division", "Fraction Division")
        context = assemble_adaptive_context(self.run, "Help me with fraction division", now=self.now)
        self.assertEqual(context.envelope["focus"]["status"], "matched")
        self.assertEqual(context.envelope["learner_evidence"]["observed_status"], "none")
        self.assertEqual(context.envelope["adaptation"]["level"], "none")
        self.assertEqual(context.manifest["remote_llm_calls"], 0)
        rendered = json.dumps(context.envelope, sort_keys=True)
        for forbidden in ("mastery", "quality", "confidence", "global_weakest", "review_status"):
            self.assertNotIn(f'"{forbidden}"', rendered)

    def test_perceived_only_is_soft_guidance_not_observed_weakness(self) -> None:
        self._concept("fraction_division", "Fraction Division")
        self._perceived()
        context = assemble_adaptive_context(self.run, "Explain fraction division", now=self.now)
        self.assertEqual(context.envelope["learner_evidence"]["observed_status"], "none")
        self.assertEqual(context.envelope["learner_evidence"]["perceived_status"], "available")
        self.assertEqual(context.envelope["adaptation"]["level"], "soft")
        self.assertEqual(context.envelope["adaptation"]["target_dimension"], "procedures")
        self.assertTrue(
            any(
                "Do not describe self-assessment as observed mastery" in claim
                for claim in context.envelope["prohibited_claims"]
            )
        )

    def test_one_probe_is_emerging_and_never_a_long_term_claim(self) -> None:
        self._concept("fraction_division", "Fraction Division")
        self._state("fraction_division")
        self._probe("fraction_division", 1)
        context = assemble_adaptive_context(self.run, "Explain fraction division", now=self.now)
        self.assertEqual(context.envelope["learner_evidence"]["observed_status"], "emerging")
        self.assertEqual(context.envelope["adaptation"]["level"], "soft")
        self.assertIn("one recent Probe", context.envelope["adaptation"]["uncertainty_action"])

    def test_two_distinct_probes_enable_evidence_backed_adaptation(self) -> None:
        self._concept("fraction_division", "Fraction Division", aliases=["dividing fractions"])
        self._state("fraction_division", quality=0.3)
        self._probe("fraction_division", 1)
        self._probe("fraction_division", 2)
        context = assemble_adaptive_context(self.run, "Why does dividing fractions flip the divisor?", now=self.now)
        self.assertEqual(context.envelope["learner_evidence"]["observed_status"], "supported")
        self.assertEqual(context.envelope["adaptation"]["level"], "evidence_backed")
        self.assertEqual(context.envelope["adaptation"]["difficulty"], "reduce")
        self.assertEqual(context.envelope["adaptation"]["target_dimension"], "procedures")

    def test_same_dimension_material_conflict_blocks_aggressive_adaptation(self) -> None:
        self._concept("fraction_division", "Fraction Division")
        self._state("fraction_division")
        self._probe("fraction_division", 1, score=0.2)
        self._probe("fraction_division", 2, score=0.8)
        context = assemble_adaptive_context(self.run, "Explain fraction division", now=self.now)
        self.assertEqual(context.envelope["learner_evidence"]["observed_status"], "conflicted")
        self.assertEqual(context.envelope["adaptation"]["difficulty"], "maintain")
        self.assertEqual(context.envelope["adaptation"]["level"], "soft")

    def test_ambiguous_full_phrase_matches_never_guess(self) -> None:
        self._concept("cell_structure", "Cell Structure", aliases=["cell"])
        self._concept("cellular_network", "Cellular Network", aliases=["cell"])
        context = assemble_adaptive_context(self.run, "What does cell mean here?", now=self.now)
        self.assertEqual(context.envelope["focus"]["status"], "ambiguous")
        self.assertEqual(context.envelope["focus"]["concept_key"], "")

    def test_recent_chat_signal_is_not_used_as_automatic_focus_fallback(self) -> None:
        self._concept("fraction_division", "Fraction Division")
        ChatConceptSignal.objects.create(
            user=self.user,
            learning_goal=self.goal,
            concept_key="fraction_division",
            concept_label="Fraction Division",
            source=ChatConceptSignal.SOURCE_CONCEPT_MAP,
            confidence=1.0,
        )
        context = assemble_adaptive_context(self.run, "Can we continue?", now=self.now)
        self.assertEqual(context.envelope["focus"]["status"], "unavailable")
        self.assertEqual(context.manifest["concept_keys"], [])

    def test_provisional_concepts_and_cross_user_state_are_never_loaded(self) -> None:
        self._concept("unverified_topic", "Unverified Topic", verified=False)
        other = UserProfile.objects.create(username="context-other", email="other@example.com", password_hash="unused")
        other_goal = LearningGoal.objects.create(user=other, title="Private Biology", preference_text="private")
        ConceptRegistryEntry.objects.create(
            user=other,
            learning_goal=other_goal,
            concept_key="private_weakness",
            concept_label="Private Weakness",
            status=ConceptRegistryEntry.STATUS_VERIFIED,
        )
        context = assemble_adaptive_context(self.run, "Explain unverified topic", now=self.now)
        rendered = json.dumps(context.envelope)
        self.assertEqual(context.envelope["focus"]["status"], "unavailable")
        self.assertNotIn("private_weakness", rendered)

    def test_context_is_bounded_and_contains_no_unrelated_global_state(self) -> None:
        for index in range(12):
            self._concept(f"concept_{index}", f"Concept {index} " + ("x" * 500))
        context = assemble_adaptive_context(self.run, "What should I study?", now=self.now)
        self.assertLessEqual(context.manifest["serialized_chars"], ADAPTIVE_CONTEXT_MAX_CHARS)
        self.assertNotIn("global_weakest", context.envelope)
        self.assertNotIn("review_status", context.envelope)

    def test_same_state_question_and_clock_have_identical_hash(self) -> None:
        self._concept("plate_tectonics", "Plate Tectonics")
        self._state("plate_tectonics")
        self._probe("plate_tectonics", 1)
        first = assemble_adaptive_context(self.run, "Explain plate tectonics", now=self.now)
        second = assemble_adaptive_context(self.run, "Explain plate tectonics", now=self.now)
        self.assertEqual(first.manifest["context_decision_sha256"], second.manifest["context_decision_sha256"])
        self.assertEqual(first.envelope, second.envelope)

    def test_local_context_construction_p95_is_below_150ms(self) -> None:
        self._concept("fraction_division", "Fraction Division")
        self._state("fraction_division")
        self._probe("fraction_division", 1)
        durations = []
        for _index in range(30):
            started = time.perf_counter()
            assemble_adaptive_context(self.run, "Explain fraction division", now=self.now)
            durations.append((time.perf_counter() - started) * 1000)
        p95 = sorted(durations)[int(len(durations) * 0.95) - 1]
        print(f"P6.8 Adaptive Context local p95={p95:.3f}ms")
        self.assertLess(p95, 150.0)
