"""Product Plugin projection through shared Skill and MCP Runtime adapters."""

from __future__ import annotations

import asyncio
import json
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


def test_plugin_standard_disabled_tools_is_applied_by_shared_mcp_runtime(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    source = _write_plugin(tmp_path / "source")
    config_path = source / ".mcp.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["mcpServers"]["echo"]["disabled_tools"] = ["echo_context"]
    config_path.write_text(json.dumps(config), encoding="utf-8")
    manager = PluginManager(tmp_path / "node", secrets)
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )
    manager.enable("plugin-fixture", "writer", expected_revision=installed.revision)
    build = McpRuntimeAdapter(secrets).build(manager.snapshot_for_agent("writer").mcp)

    async def _discover() -> list[str]:
        try:
            return [tool.name for tool in await build.toolsets[0].get_tools_with_prefix()]
        finally:
            await build.toolsets[0].close()

    assert asyncio.run(_discover()) == []
