"""Tests for GUI MCP routing helpers."""

from __future__ import annotations

import unittest

from openppx.core.gui_mcp import resolve_gui_mcp_from_summaries


class GuiMcpRoutingTests(unittest.TestCase):
    def test_resolve_gui_mcp_from_summaries_with_default_server_name(self) -> None:
        routing = resolve_gui_mcp_from_summaries(
            [{"name": "openppx_gui", "prefix": "mcp_openppx_gui", "transport": "stdio"}]
        )
        self.assertIsNotNone(routing)
        assert routing is not None
        self.assertEqual(routing.tool_prefix, "mcp_openppx_gui")
        self.assertEqual(routing.task_tool_name, "mcp_openppx_gui_gui_task")
        self.assertEqual(routing.action_tool_name, "mcp_openppx_gui_gui_action")

    def test_resolve_gui_mcp_from_summaries_with_custom_prefix(self) -> None:
        routing = resolve_gui_mcp_from_summaries(
            [{"name": "gui_remote", "prefix": "mcp_gui_desktop", "transport": "stdio"}]
        )
        self.assertIsNotNone(routing)
        assert routing is not None
        self.assertEqual(routing.task_tool_name, "mcp_gui_desktop_gui_task")
        self.assertEqual(routing.action_tool_name, "mcp_gui_desktop_gui_action")

    def test_resolve_gui_mcp_from_summaries_fallback(self) -> None:
        routing = resolve_gui_mcp_from_summaries(
            [{"name": "custom", "prefix": "mcp_gui", "transport": "stdio"}]
        )
        self.assertIsNotNone(routing)
        assert routing is not None
        self.assertEqual(routing.task_tool_name, "mcp_gui_gui_task")


if __name__ == "__main__":
    unittest.main()
