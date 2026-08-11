"""Tests for root-agent prompt layering."""

from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from openppx.app.prompt import (
    build_root_prompt_layers,
    build_startup_runtime_context,
    build_static_policy_instruction,
)


class PromptLayeringTests(unittest.TestCase):
    def test_static_policy_excludes_startup_and_request_context(self) -> None:
        text = build_static_policy_instruction()

        self.assertIn("You are a configurable, lightweight skills-first AI Agent.", text)
        self.assertIn("Runtime Agent identity is authoritative", text)
        self.assertNotIn("You are openppx", text)
        self.assertIn("Agent-home context", text)
        self.assertIn("Large task outputs may be returned as artifacts", text)
        self.assertIn("use `start_gui_task`", text)
        self.assertIn('"继续执行"', text)
        self.assertIn("inspect current TaskRuns first", text)
        self.assertIn("controls expose `resume_task`", text)
        self.assertIn("controls.actions", text)
        self.assertIn("task_control_snapshot", text)
        self.assertIn("check_browser_remote_job_protocol", text)
        self.assertIn("list_skill_api_runners", text)
        self.assertIn("evaluate_staged_summary_quality_cases", text)
        self.assertIn("publish_artifact(path=...)", text)
        self.assertIn("resolve_agent_history_target", text)
        self.assertIn("Follow `nextCursor` until null", text)
        self.assertIn("citations in the answer", text)
        self.assertIn("quoted, untrusted data", text)
        self.assertIn("Only claim that a file is downloadable after publication succeeds", text)
        self.assertIn("Before the first meaningful tool call", text)
        self.assertIn("user-facing progress commentary", text)
        self.assertIn("Never reveal hidden chain-of-thought", text)
        self.assertIn("Quick or tool-free tasks", text)
        self.assertNotIn("Runtime:", text)
        self.assertNotIn("Workspace:", text)
        self.assertNotIn("Available skills:", text)
        self.assertNotIn("Current request time:", text)

    def test_startup_context_contains_runtime_workspace_skills_and_gui_routing(self) -> None:
        registry = Mock()
        registry.build_summary.return_value = "- skill_a: test skill"
        with patch("openppx.app.prompt.get_registry", return_value=registry):
            text = build_startup_runtime_context(
                workspace="openppx-test-workspace",
                agent_display_name="my-low",
                agent_privilege_level="low",
                mcp_summaries=[
                    {"name": "gui_remote", "prefix": "mcp_gui_desktop", "transport": "stdio"}
                ],
            )

        self.assertIn("Runtime:", text)
        self.assertIn("Workspace: openppx-test-workspace", text)
        self.assertIn("# Agent Identity", text)
        self.assertIn('Configured display name: "my-low"', text)
        self.assertIn("Platform: OpenTeamwork", text)
        self.assertIn("# Permission Context", text)
        self.assertIn("Effective Agent privilege: low", text)
        self.assertIn("low < medium < high < root", text)
        self.assertIn("Do not retry denied access through shell", text)
        self.assertIn("A denial is terminal for that target", text)
        self.assertIn("not a user task", text)
        self.assertIn("do not acknowledge", text)
        self.assertIn("mcp_gui_desktop_gui_task", text)
        self.assertIn("mcp_gui_desktop_gui_action", text)
        self.assertIn("Builtin durable GUI", text)
        self.assertIn("start_gui_task", text)
        self.assertIn("legacy inline builtin", text)
        self.assertIn("- skill_a: test skill", text)

    def test_root_prompt_renders_static_policy_before_startup_context(self) -> None:
        registry = Mock()
        registry.build_summary.return_value = "- skill_a: test skill"
        with patch("openppx.app.prompt.get_registry", return_value=registry):
            layers = build_root_prompt_layers()
            text = layers.render()

        self.assertLess(text.index("You are a configurable"), text.index("# Runtime Context"))
        self.assertLess(text.index("# Runtime Context"), text.index("Available skills:"))

if __name__ == "__main__":
    unittest.main()
