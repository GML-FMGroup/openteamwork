"""Product Plugin projection through shared Skill and MCP Runtime adapters."""

from __future__ import annotations

import asyncio
from pathlib import Path

from openppx.config import InMemorySecretStore
from openppx.extensions.models import ExtensionSourceRef
from openppx.extensions.plugins import PluginManager
from openppx.runtime.mcp_adapter import McpRuntimeAdapter

from tests.extensions.test_plugin_resources import _write_plugin


def test_plugin_fixture_projects_skill_and_real_mcp_without_loading_host_code(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    manager = PluginManager(
        tmp_path / "node",
        secrets,
        allowed_runtime_capabilities=frozenset({"runtime.task-observability"}),
    )
    installed = manager.install(
        manager.stage(
            ExtensionSourceRef(
                type="local_directory",
                locator=str(_write_plugin(tmp_path / "source")),
            )
        ),
        expected_revision=None,
    )
    manager.enable("plugin-fixture", "writer", expected_revision=installed.revision)
    snapshot = manager.snapshot_for_agent("writer")

    async def _discover() -> list[str]:
        build = McpRuntimeAdapter(secrets).build(snapshot.mcp)
        try:
            tools = await build.toolsets[0].get_tools_with_prefix()
            return [tool.name for tool in tools]
        finally:
            await build.toolsets[0].close()

    assert "# Plugin research" in snapshot.skills.read_skill("plugin-fixture--research")
    assert asyncio.run(_discover()) == ["plugin_fixture_echo_echo_context"]
