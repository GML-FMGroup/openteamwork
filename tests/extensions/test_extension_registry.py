"""Unified read-only Extension inventory tests."""

from __future__ import annotations

from pathlib import Path

from openppx.config import InMemorySecretStore
from openppx.extensions import (
    AppManager,
    ExtensionRegistry,
    ExtensionSourceRef,
    McpManager,
    PluginManager,
    SkillManager,
)
from tests.extensions.test_app_resources import _connection, _definition
from tests.extensions.test_direct_mcp_registry import _server
from tests.extensions.test_plugin_resources import _write_plugin
from tests.extensions.test_skill_registry import _skill


def _registry(tmp_path: Path) -> tuple[ExtensionRegistry, SkillManager, McpManager, AppManager, PluginManager]:
    node = tmp_path / "node"
    secrets = InMemorySecretStore()
    skills = SkillManager(node)
    mcp = McpManager(node, secrets)
    apps = AppManager(node, secrets)
    plugins = PluginManager(
        node,
        secrets,
    )
    return ExtensionRegistry(skills=skills, mcp=mcp, apps=apps, plugins=plugins), skills, mcp, apps, plugins


def test_registry_lists_four_domains_without_leaking_source_paths_or_secret_refs(tmp_path: Path) -> None:
    registry, skills, mcp, apps, plugins = _registry(tmp_path)
    source = _skill(tmp_path / "private-source")
    skills.install(
        skills.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )
    mcp.create(_server(), expected_revision=None)
    apps.install_definition(_definition(auth_type="none"), expected_revision=None)
    apps.create_connection(_connection(credential_refs={}), expected_revision=None)
    plugin_source = _write_plugin(tmp_path / "plugin-source")
    plugins.install(
        plugins.stage(ExtensionSourceRef(type="local_directory", locator=str(plugin_source))),
        expected_revision=None,
    )

    payload = [item.to_payload() for item in registry.list()]

    assert [item["kind"] for item in payload] == ["plugin", "app", "mcp", "skill"]
    assert str(tmp_path) not in str(payload)
    assert "secretRef" not in str(payload)
    assert registry.get("app", "fixture-app").details["connections"][0]["id"] == "fixture-account"
    assert registry.get("plugin", "plugin-fixture").details["resourceCounts"]["skills"] == 1


def test_registry_filters_agent_enablement_and_projects_readiness(tmp_path: Path) -> None:
    registry, skills, mcp, _apps, plugins = _registry(tmp_path)
    skill_source = _skill(tmp_path / "skill")
    skill = skills.install(
        skills.stage(ExtensionSourceRef(type="local_directory", locator=str(skill_source))),
        expected_revision=None,
    )
    skills.enable("demo", "writer", expected_revision=skill.revision)
    server = mcp.create(_server(), expected_revision=None)
    mcp.enable("docs", "reviewer", expected_revision=server.revision)
    plugin_source = _write_plugin(tmp_path / "plugin")
    plugin = plugins.install(
        plugins.stage(ExtensionSourceRef(type="local_directory", locator=str(plugin_source))),
        expected_revision=None,
    )
    plugins.enable("plugin-fixture", "writer", expected_revision=plugin.revision)

    writer = registry.list(agent_id="writer")

    assert [(item.kind, item.extension_id) for item in writer] == [
        ("plugin", "plugin-fixture"),
        ("skill", "demo"),
    ]
    assert registry.readiness("skill", "demo")["ready"] is True
    assert registry.readiness("mcp", "docs")["status"] == "enabled"


def test_registry_projects_safe_editor_details_for_mcp_and_app_connections(tmp_path: Path) -> None:
    registry, _skills, mcp, apps, _plugins = _registry(tmp_path)
    mcp.create(_server(), expected_revision=None)
    apps.install_definition(_definition(), expected_revision=None)
    apps.create_connection(
        _connection(
            credential_refs={
                "api-token": {"store": "system", "name": "fixture-token"}
            }
        ),
        expected_revision=None,
    )

    mcp_detail = registry.get("mcp", "docs").details
    app_detail = registry.get("app", "fixture-app").details

    assert mcp_detail["resource"]["metadata"]["name"] == "docs"
    assert mcp_detail["resource"]["spec"]["transport"]["type"] == "stdio"
    assert app_detail["credentials"] == [
        {"name": "api-token", "label": "API token", "required": True}
    ]
    assert app_detail["tools"][0]["name"] == "echo_context"
    assert app_detail["connections"][0]["credentialRefs"] == {
        "api-token": {"store": "system", "name": "fixture-token"}
    }
    assert "SecretValue" not in str(app_detail)
