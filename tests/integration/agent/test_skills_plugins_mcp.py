from __future__ import annotations

from django.test import TestCase

from learning_apps.chat.services.conversation_repository_service import ensure_active_conversation
from learning_apps.persistence.models import LearningGoal, UserProfile

from learning_apps.adaptive_agent.mcp import authenticate_mcp_token, issue_mcp_token, process_mcp_request
from learning_apps.adaptive_agent.models import LearningAgentRun
from learning_apps.adaptive_agent.plugins import active_plugin_policy, disable_plugin
from learning_apps.adaptive_agent.release import current_release_manifest
from learning_apps.adaptive_agent.skills import BUILTIN_SKILLS, enabled_capabilities, select_skill


class SkillsPluginsMcpTests(TestCase):
    def setUp(self):
        self.user = UserProfile.objects.create(username="mcp-user", email="mcp@example.com", password_hash="unused")
        self.goal = LearningGoal.objects.create(
            user=self.user,
            title="History",
            preference_text="History",
            status=LearningGoal.STATUS_SELF_ASSESSMENT_COMPLETED,
        )
        self.conversation = ensure_active_conversation(self.user.username, self.goal.id)

    def test_skill_selection_is_version_pinned_and_enables_only_required_tools(self):
        release = current_release_manifest()
        run = LearningAgentRun.objects.create(
            user=self.user,
            learning_goal=self.goal,
            conversation=self.conversation,
            conversation_generation=self.conversation.generation,
            release=release,
            idempotency_key="skill-test",
            request_sha256="a" * 64,
            trace_id="b" * 32,
            model=release.model,
            reasoning_effort=release.reasoning_effort,
            expires_at=self.conversation.retention_expires_at,
        )
        instructions = select_skill(run.run_id, "source_grounded_explanation")
        run.refresh_from_db()
        self.assertEqual(run.selected_skills[0]["version"], BUILTIN_SKILLS["source_grounded_explanation"].version)
        self.assertIn("knowledge.retrieve_evidence", enabled_capabilities(run.selected_skills))
        self.assertNotIn("quiz.propose", enabled_capabilities(run.selected_skills))
        self.assertIn("Never invent evidence", " ".join(instructions["output_constraints"]))

    def test_private_mcp_is_a_read_only_capability_adapter_and_plugin_disable_revokes_token(self):
        grant, raw_token = issue_mcp_token(username=self.user.username, learning_goal_id=self.goal.id)
        authenticated = authenticate_mcp_token(raw_token)
        listed = process_mcp_request(authenticated, {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        names = {item["name"] for item in listed["result"]["tools"]}
        self.assertEqual(names, {"knowledge.retrieve_evidence", "learning.get_concept_map"})
        self.assertNotIn("memory.propose", names)
        called = process_mcp_request(
            authenticated,
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "learning.get_concept_map", "arguments": {}}},
        )
        self.assertFalse(called["result"]["isError"])
        self.assertIn("missing", called["result"]["content"][0]["text"])
        disabled = disable_plugin("curriculum_materials")
        self.assertEqual(disabled, 1)
        policy = active_plugin_policy()
        self.assertNotIn("source_grounded_explanation", policy.skill_ids)
        self.assertNotIn("knowledge.retrieve_evidence", policy.tool_names)
        grant.refresh_from_db()
        self.assertIsNotNone(grant.revoked_at)
        with self.assertRaisesRegex(Exception, "mcp_token_invalid"):
            authenticate_mcp_token(raw_token)
