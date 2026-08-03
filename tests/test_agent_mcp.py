"""Tests for MCP toolset wiring in root agent assembly."""

from __future__ import annotations

import unittest


def _tool_names(tools: list[object]) -> set[str]:
    names: set[str] = set()
    for tool in tools:
        if hasattr(tool, "name") and isinstance(getattr(tool, "name"), str):
            names.add(getattr(tool, "name"))
            continue
        if hasattr(tool, "func"):
            names.add(getattr(getattr(tool, "func"), "__name__", str(tool)))
            continue
        names.add(getattr(tool, "__name__", str(tool)))
    return names


class AgentMcpTests(unittest.TestCase):
    def test_build_tools_appends_mcp_toolsets(self) -> None:
        from openppx import agent

        sentinel_toolset = object()
        tools = agent._build_tools(extension_tools=(sentinel_toolset,))

        self.assertIn(sentinel_toolset, tools)

    def test_build_tools_keeps_builtin_gui_tools_enabled_by_default(self) -> None:
        from openppx import agent
        from openppx.tooling.registry import computer_task, computer_use, glob, grep, start_gui_task

        tools = agent._build_tools()
        self.assertIn(start_gui_task, tools)
        self.assertIn(computer_task, tools)
        self.assertIn(computer_use, tools)
        self.assertIn(glob, tools)
        self.assertIn(grep, tools)

    def test_build_tools_can_disable_builtin_gui_tools(self) -> None:
        from openppx import agent
        from openppx.tooling.registry import computer_task, computer_use, start_gui_task

        tools = agent._build_tools(include_gui_tools=False)
        self.assertNotIn(start_gui_task, tools)
        self.assertNotIn(computer_task, tools)
        self.assertNotIn(computer_use, tools)

    def test_build_tools_limits_low_to_read_only_tools(self) -> None:
        from openppx import agent
        from openppx.tooling.registry import (
            exec_command,
            list_dir,
            list_skill_api_runners,
            read_file,
            task_control_snapshot,
            web_search,
            write_file,
        )

        tools = agent._build_tools(privilege_level="low", can_delegate=False)

        self.assertIn(read_file, tools)
        self.assertIn(list_dir, tools)
        self.assertIn(list_skill_api_runners, tools)
        self.assertIn(task_control_snapshot, tools)
        self.assertNotIn(write_file, tools)
        self.assertNotIn(exec_command, tools)
        self.assertNotIn(web_search, tools)

    def test_build_tools_keeps_medium_exec_and_web_without_transport_tools(self) -> None:
        from openppx import agent
        from openppx.tooling.registry import exec_command, web_search

        tools = agent._build_tools(privilege_level="medium", can_delegate=True)

        names = _tool_names(tools)
        self.assertIn("exec", names)
        self.assertIn("web_search", names)
        self.assertNotIn("message", names)
        self.assertNotIn("message_file", names)

    def test_build_tools_keeps_high_full_tool_access(self) -> None:
        from openppx import agent
        from openppx.tooling.registry import (
            check_browser_remote_job_protocol,
            dispatch_task_action,
            evaluate_staged_summary_quality_cases,
            exec_command,
            list_skill_api_runners,
            summarize_staged_summary_quality_log,
            task_control_snapshot,
            web_search,
        )

        tools = agent._build_tools(privilege_level="high", can_delegate=True)

        names = _tool_names(tools)
        self.assertIn("exec", names)
        self.assertIn("web_search", names)
        self.assertNotIn("message", names)
        self.assertNotIn("message_file", names)
        self.assertIn(check_browser_remote_job_protocol, tools)
        self.assertIn(dispatch_task_action, tools)
        self.assertIn(evaluate_staged_summary_quality_cases, tools)
        self.assertIn(list_skill_api_runners, tools)
        self.assertIn(summarize_staged_summary_quality_log, tools)
        self.assertIn(task_control_snapshot, tools)


if __name__ == "__main__":
    unittest.main()
