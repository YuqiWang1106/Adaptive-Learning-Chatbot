from __future__ import annotations

import asyncio
import json
import re
from unittest.mock import patch

from django.test import TransactionTestCase, override_settings

from agents.items import ModelResponse
from agents.models.interface import Model
from agents.usage import Usage
from openai.types.responses import ResponseFunctionToolCall, ResponseOutputMessage, ResponseOutputText

from learning_apps.persistence.models import LearningGoal, UserHistory, UserProfile

from learning_apps.adaptive_agent.models import AgentInterruption, LearningAgentRun
from learning_apps.application.models import LearningActionProposal
from learning_apps.adaptive_agent.run_service import create_agent_run, resolve_interruption
from learning_apps.adaptive_agent.runtime import execute_agent_run, llm_gateway
from learning_apps.adaptive_agent.sdk_adapter import build_sdk_agent
from learning_apps.adaptive_agent.skills import BUILTIN_SKILLS


class ScriptedModel(Model):
    def __init__(self, tool_name: str, tool_arguments: dict):
        self.tool_name = tool_name
        self.tool_arguments = tool_arguments
        self.calls = 0
        self.context_sha256 = ""

    async def get_response(self, system_instructions, input, model_settings, tools, output_schema,
                           handoffs, tracing, *, previous_response_id, conversation_id, prompt):
        self.calls += 1
        if not self.context_sha256:
            match = re.search(r"Context decision SHA-256:\s*([0-9a-f]{64})", json.dumps(input, default=str))
            self.context_sha256 = match.group(1) if match else "0" * 64
        if self.calls == 1:
            return ModelResponse(
                output=[
                    ResponseFunctionToolCall(
                        id="fc_scripted_1",
                        call_id="call_scripted_1",
                        name=self.tool_name,
                        arguments=json.dumps(self.tool_arguments),
                        type="function_call",
                        status="completed",
                    )
                ],
                usage=Usage(requests=1),
                response_id="resp_scripted_1",
            )
        output = {
            "answer_markdown": "Here is the bounded teaching response after your decision.",
            "teaching_strategy": "Clarified adaptive explanation",
            "citations": [],
            "used_evidence_categories": [],
            "next_action": "Try one focused example.",
            "confidence": "medium",
            "personalization_basis": {
                "status": "insufficient",
                "context_policy_version": "adaptive-teaching-context-v2.0.0",
                "context_decision_sha256": self.context_sha256,
                "adaptation_level": "none",
                "concept_keys": [],
                "evidence_categories": [],
                "limitation": "No formal Probe evidence was loaded.",
            },
        }
        return ModelResponse(
            output=[
                ResponseOutputMessage(
                    id="msg_scripted_2",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[ResponseOutputText(type="output_text", text=json.dumps(output), annotations=[])],
                )
            ],
            usage=Usage(requests=1),
            response_id="resp_scripted_2",
        )

    async def stream_response(self, *args, **kwargs):
        if False:
            yield None


class SlowModel(Model):
    async def get_response(self, *args, **kwargs):
        await asyncio.sleep(5)

    async def stream_response(self, *args, **kwargs):
        if False:
            yield None


class SdkPauseResumeTests(TransactionTestCase):
    def setUp(self):
        self.user = UserProfile.objects.create(username="sdk-user", email="sdk@example.com", password_hash="unused")
        self.goal = LearningGoal.objects.create(
            user=self.user,
            title="Chemistry",
            preference_text="Chemistry",
            status=LearningGoal.STATUS_SELF_ASSESSMENT_COMPLETED,
        )

    def _agent_builder(self, model):
        return lambda **_kwargs: build_sdk_agent(model=model, reasoning_effort="medium")

    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_sdk_clarification_serializes_and_resumes_the_same_run(self, _dispatch):
        run, _ = create_agent_run(
            username=self.user.username,
            learning_goal_id=self.goal.id,
            question="Compare them for me",
            idempotency_key="sdk-clarification",
        )
        model = ScriptedModel(
            "runtime_request_clarification",
            {"question": "Which two ideas?", "options": [], "why_needed": "The comparison target is missing."},
        )
        with patch("learning_apps.adaptive_agent.runtime.build_sdk_agent", side_effect=self._agent_builder(model)):
            execute_agent_run(run.run_id)
            run.refresh_from_db()
            self.assertEqual(run.status, LearningAgentRun.STATUS_WAITING_CLARIFICATION)
            interruption = run.interruptions.get()
            self.assertEqual(interruption.kind, AgentInterruption.KIND_CLARIFICATION)
            resolve_interruption(
                username=self.user.username,
                run_id=run.run_id,
                interruption_id=interruption.interruption_id,
                kind=AgentInterruption.KIND_CLARIFICATION,
                response="Ionic and covalent bonds",
            )
            execute_agent_run(run.run_id)
        run.refresh_from_db()
        self.assertEqual(run.status, LearningAgentRun.STATUS_COMPLETED, run.error_code)
        self.assertEqual(model.calls, 2)
        self.assertEqual(run.model_request_count, 2)
        self.assertGreaterEqual(run.elapsed_ms, 0)
        self.assertEqual(UserHistory.objects.filter(conversation=run.conversation).count(), 1)
        self.assertFalse(hasattr(run, "checkpoint"))

    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_sdk_approval_executes_proposal_exactly_once(self, _dispatch):
        run, _ = create_agent_run(
            username=self.user.username,
            learning_goal_id=self.goal.id,
            question="Please give me a short quiz on atoms",
            idempotency_key="sdk-approval",
        )
        run.selected_skills = [BUILTIN_SKILLS["adaptive_quiz_session"].public_summary()]
        run.skill_count = 1
        run.save(update_fields=["selected_skills", "skill_count"])
        model = ScriptedModel(
            "quiz_propose",
            {"concept_key": "atoms", "focus": "atomic structure", "question_count": 3},
        )
        with patch("learning_apps.adaptive_agent.runtime.build_sdk_agent", side_effect=self._agent_builder(model)):
            execute_agent_run(run.run_id)
            run.refresh_from_db()
            self.assertEqual(run.status, LearningAgentRun.STATUS_WAITING_APPROVAL)
            interruption = run.interruptions.get()
            resolve_interruption(
                username=self.user.username,
                run_id=run.run_id,
                interruption_id=interruption.interruption_id,
                kind=AgentInterruption.KIND_APPROVAL,
                response=True,
            )
            execute_agent_run(run.run_id)
            execute_agent_run(run.run_id)
        run.refresh_from_db()
        self.assertEqual(run.status, LearningAgentRun.STATUS_COMPLETED, run.error_code)
        proposal = LearningActionProposal.objects.get(proposal_type=LearningActionProposal.TYPE_QUIZ)
        self.assertEqual(proposal.status, LearningActionProposal.STATUS_APPROVED_DRAFT)
        self.assertFalse(proposal.external_action_executed)
        self.assertEqual(run.executions.filter(invocations__capability_name="quiz.propose").count(), 1)

    @override_settings(LEARNING_AGENT_TIMEOUT_SECONDS=1)
    @patch("learning_apps.adaptive_agent.run_service.dispatch_agent_run")
    def test_model_timeout_fails_closed_without_legacy_fallback(self, _dispatch):
        run, _ = create_agent_run(
            username=self.user.username,
            learning_goal_id=self.goal.id,
            question="Explain a reaction",
            idempotency_key="sdk-timeout",
        )
        model = SlowModel()
        with (
            patch("learning_apps.adaptive_agent.runtime.build_sdk_agent", side_effect=self._agent_builder(model)),
            patch(
                "learning_apps.adaptive_agent.runtime.llm_gateway.route_slot",
                wraps=llm_gateway.route_slot,
            ) as route_slot,
        ):
            execute_agent_run(run.run_id)
        route_slot.assert_called_once_with("agent.tutor_v2", include_global=False)
        run.refresh_from_db()
        self.assertEqual(run.status, LearningAgentRun.STATUS_FAILED)
        self.assertEqual(run.error_code, "agent_run_timeout")
        self.assertEqual(UserHistory.objects.filter(conversation=run.conversation).count(), 0)
