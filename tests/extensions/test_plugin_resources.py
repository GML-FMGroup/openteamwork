"""Declarative Product Plugin staging, lifecycle, projection, and security tests."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from openppx.config import InMemorySecretStore
from openppx.extensions import ExtensionError, McpManager, McpServer
from openppx.extensions.app_models import AppConnection
from openppx.extensions.apps import AppManager
from openppx.extensions.indexes import ExtensionReferenceIndex, ResourceIdentityIndex
from openppx.extensions.models import ExtensionSourceRef
from openppx.extensions.plugins import PluginManager
from openppx.extensions.prefixes import ToolPrefixIndex


_DIGEST = "sha256:" + "d" * 64


def _write_plugin(
    root: Path,
    *,
    version: str = "1.0.0",
    skill_body: str = "# Plugin research\n",
    risk: str = "medium",
) -> Path:
    """Create one deterministic, fully declarative Plugin fixture."""
    manifest_dir = root / ".openppx-plugin"
    skill_dir = root / "skills" / "plugin-fixture--research"
    mcp_dir = root / "mcp"
    app_dir = root / "apps"
    agent_dir = root / "agents"
    schema_dir = root / "schemas"
    docs_dir = root / "docs"
    for directory in (manifest_dir, skill_dir, mcp_dir, app_dir, agent_dir, schema_dir, docs_dir):
        directory.mkdir(parents=True, exist_ok=True)
    manifest = {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "PluginManifest",
        "metadata": {"name": "plugin-fixture"},
        "spec": {
            "displayName": "Plugin Fixture",
            "description": "A deterministic declarative Plugin fixture.",
            "version": version,
            "developer": "OpenPPX",
            "risk": risk,
            "runtimeCapabilities": ["runtime.task-observability"],
            "resources": {
                "skills": [
                    {
                        "name": "plugin-fixture--research",
                        "path": "skills/plugin-fixture--research",
                    }
                ],
                "appDefinitions": [
                    {"name": "plugin-fixture--echo-app", "path": "apps/echo.json"}
                ],
                "mcpServers": [
                    {"name": "plugin-fixture--echo", "path": "mcp/echo.json"}
                ],
                "agentTemplates": [
                    {"name": "plugin-fixture--researcher", "path": "agents/researcher.json"}
                ],
                "configSchemas": [
                    {"name": "plugin-fixture--settings", "path": "schemas/settings.json"}
                ],
                "documentation": [
                    {"name": "plugin-fixture--guide", "path": "docs/README.md"}
                ],
            },
        },
    }
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: plugin-fixture--research\n"
        "description: Research using the fixture Plugin.\n"
        "metadata:\n"
        "  openppx:\n"
        f"    version: {version}\n"
        "    risk: low\n"
        "---\n\n"
        f"{skill_body}",
        encoding="utf-8",
    )
    mcp = {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "McpServer",
        "metadata": {"name": "plugin-fixture--echo"},
        "spec": {
            "displayName": "Plugin Echo",
            "description": "Local Plugin MCP fixture.",
            "transport": {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(Path("tests/eval/mock_mcp_server.py").resolve())],
                "environment": {},
            },
            "policy": {"toolNamePrefix": "plugin_fixture_echo"},
            "risk": "low",
            "enabledAgentIds": [],
            "managedBy": {"kind": "plugin", "name": "plugin-fixture"},
        },
    }
    (mcp_dir / "echo.json").write_text(json.dumps(mcp), encoding="utf-8")
    app = {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "AppDefinition",
        "metadata": {"name": "plugin-fixture--echo-app"},
        "spec": {
            "displayName": "Plugin Echo App",
            "description": "Plugin-owned App definition.",
            "version": version,
            "category": "testing",
            "developer": "OpenPPX",
            "source": {
                "type": "builtin",
                "locator": "plugin-fixture",
                "version": version,
                "revision": f"builtin:{version}",
                "digest": _DIGEST,
            },
            "auth": {"type": "none", "credentials": []},
            "mcp": {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(Path("tests/eval/mock_mcp_server.py").resolve())],
                "environment": {},
            },
            "tools": [
                {
                    "name": "echo_context",
                    "title": "Echo context",
                    "description": "Echo one safe token.",
                    "access": "read",
                    "risk": "low",
                }
            ],
            "policy": {},
            "managedBy": {"kind": "plugin", "name": "plugin-fixture"},
        },
    }
    (app_dir / "echo.json").write_text(json.dumps(app), encoding="utf-8")
    (agent_dir / "researcher.json").write_text(
        json.dumps({"apiVersion": "openppx.io/v1alpha1", "kind": "AgentTemplate", "spec": {}}),
        encoding="utf-8",
    )
    (schema_dir / "settings.json").write_text(
        json.dumps({"type": "object", "additionalProperties": False}),
        encoding="utf-8",
    )
    (docs_dir / "README.md").write_text("# Plugin Fixture\n", encoding="utf-8")
    return root


def _manager(
    root: Path,
    *,
    identities: ResourceIdentityIndex | None = None,
    references: ExtensionReferenceIndex | None = None,
    prefixes: ToolPrefixIndex | None = None,
    allowed_runtime_capabilities: frozenset[str] = frozenset(
        {"runtime.task-observability"}
    ),
) -> PluginManager:
    return PluginManager(
        root,
        InMemorySecretStore(),
        allowed_runtime_capabilities=allowed_runtime_capabilities,
        identity_index=identities,
        reference_index=references,
        prefix_index=prefixes,
    )


def test_plugin_stage_preview_install_enable_update_snapshot_disable_remove(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source")
    manager = _manager(tmp_path / "node")
    staged = manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    preview = manager.preview(staged)

    assert preview.plugin_id == "plugin-fixture"
    assert preview.resource_counts == {
        "appDefinitions": 1,
        "agentTemplates": 1,
        "configSchemas": 1,
        "documentation": 1,
        "mcpServers": 1,
        "skills": 1,
    }
    installed = manager.install(staged, expected_revision=None)
    assert installed.status == "disabled"
    assert installed.content_root.is_relative_to((tmp_path / "node").resolve())

    enabled = manager.enable(
        "plugin-fixture",
        "writer",
        expected_revision=installed.revision,
    )
    first = manager.snapshot_for_agent("writer")
    assert enabled.status == "enabled"
    assert first.plugin_ids == ("plugin-fixture",)
    assert first.skills.names == ("plugin-fixture--research",)
    assert first.mcp.names == ("plugin-fixture--echo",)
    assert "# Plugin research" in first.skills.read_skill("plugin-fixture--research")

    _write_plugin(source, version="1.1.0", skill_body="# Updated research\n")
    updated = manager.update(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=enabled.revision,
    )
    second = manager.snapshot_for_agent("writer")
    assert updated.record.spec.enabled_agent_ids == ["writer"]
    assert first.revision != second.revision
    assert "# Plugin research" in first.skills.read_skill("plugin-fixture--research")
    assert "# Updated research" in second.skills.read_skill("plugin-fixture--research")

    disabled = manager.disable(
        "plugin-fixture",
        "writer",
        expected_revision=updated.revision,
    )
    assert manager.snapshot_for_agent("writer").plugin_ids == ()
    manager.remove("plugin-fixture", expected_revision=disabled.revision)
    assert manager.list() == ()


def test_plugin_directory_and_archive_sources_share_the_same_manifest_contract(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source")
    archive = tmp_path / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as output:
        for path in sorted(source.rglob("*")):
            if path.is_file():
                output.write(path, path.relative_to(source).as_posix())
    manager = _manager(tmp_path / "node")

    directory = manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    archived = manager.stage(ExtensionSourceRef(type="local_archive", locator=str(archive)))
    try:
        assert directory.manifest == archived.manifest
        assert directory.extension.digest == archived.extension.digest
    finally:
        directory.extension.cleanup()
        archived.extension.cleanup()


def test_plugin_rejects_host_code_secret_bindings_unsafe_paths_and_wrong_namespace(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source")
    manager = _manager(tmp_path / "node")
    manifest_path = source / ".openppx-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["spec"]["entryPoint"] = "plugin.py"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExtensionError) as host_code:
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    assert host_code.value.code == "invalid_manifest"

    _write_plugin(source)
    mcp_path = source / "mcp" / "echo.json"
    mcp = json.loads(mcp_path.read_text(encoding="utf-8"))
    mcp["spec"]["transport"]["environment"] = {
        "TOKEN": {"kind": "secret", "secretRef": {"store": "system", "name": "forbidden"}}
    }
    mcp_path.write_text(json.dumps(mcp), encoding="utf-8")
    with pytest.raises(ExtensionError) as secret:
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    assert secret.value.code == "invalid_manifest"

    _write_plugin(source)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["spec"]["resources"]["documentation"][0]["path"] = "../README.md"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExtensionError) as path:
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    assert path.value.code == "invalid_manifest"

    _write_plugin(source)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["spec"]["resources"]["skills"][0]["name"] = "unscoped"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExtensionError) as namespace:
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    assert namespace.value.code == "invalid_manifest"


def test_plugin_risk_capability_revision_and_dependency_gates(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source", risk="high")
    manager = _manager(tmp_path / "node")
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )
    with pytest.raises(ExtensionError) as confirmation:
        manager.enable("plugin-fixture", "writer", expected_revision=installed.revision)
    assert confirmation.value.code == "confirmation_required"
    with pytest.raises(ExtensionError) as revision:
        manager.enable(
            "plugin-fixture",
            "writer",
            expected_revision="sha256:" + "0" * 64,
            confirmed=True,
        )
    assert revision.value.code == "revision_conflict"

    untrusted = _manager(
        tmp_path / "other-node",
        allowed_runtime_capabilities=frozenset(),
    )
    staged = untrusted.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    installed_untrusted = untrusted.install(staged, expected_revision=None)
    with pytest.raises(ExtensionError) as capability:
        untrusted.enable(
            "plugin-fixture",
            "writer",
            expected_revision=installed_untrusted.revision,
            confirmed=True,
        )
    assert capability.value.code == "dependency_missing"


def test_plugin_owned_app_requires_owner_enablement_and_blocks_plugin_removal(tmp_path: Path) -> None:
    node = tmp_path / "node"
    identities = ResourceIdentityIndex()
    references = ExtensionReferenceIndex()
    prefixes = ToolPrefixIndex()
    plugins = _manager(
        node,
        identities=identities,
        references=references,
        prefixes=prefixes,
    )
    apps = AppManager(
        node,
        InMemorySecretStore(),
        identity_index=identities,
        reference_index=references,
        owner_enabled=plugins.is_enabled,
        prefix_index=prefixes,
    )
    apps.register_definition_provider("plugins", plugins.app_definitions)
    installed = plugins.install(
        plugins.stage(
            ExtensionSourceRef(
                type="local_directory",
                locator=str(_write_plugin(tmp_path / "source")),
            )
        ),
        expected_revision=None,
    )
    definitions = apps.list_definitions()
    assert [item.record.metadata.name for item in definitions] == ["plugin-fixture--echo-app"]
    connection = apps.create_connection(
        AppConnection.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AppConnection",
                "metadata": {"name": "plugin-account"},
                "spec": {
                    "appId": "plugin-fixture--echo-app",
                    "displayName": "Plugin account",
                },
            }
        ),
        expected_revision=None,
    )
    with pytest.raises(ExtensionError) as owner_disabled:
        apps.enable_connection(
            "plugin-account",
            "writer",
            expected_revision=connection.revision,
        )
    assert owner_disabled.value.code == "dependency_missing"

    enabled = plugins.enable(
        "plugin-fixture",
        "writer",
        expected_revision=installed.revision,
    )
    app_enabled = apps.enable_connection(
        "plugin-account",
        "writer",
        expected_revision=connection.revision,
    )
    app_disabled = apps.disable_connection(
        "plugin-account",
        "writer",
        expected_revision=app_enabled.revision,
    )
    plugin_disabled = plugins.disable(
        "plugin-fixture",
        "writer",
        expected_revision=enabled.revision,
    )
    with pytest.raises(ExtensionError) as referenced:
        plugins.remove("plugin-fixture", expected_revision=plugin_disabled.revision)
    assert referenced.value.code == "extension_in_use"

    apps.remove_connection("plugin-account", expected_revision=app_disabled.revision)
    plugins.remove("plugin-fixture", expected_revision=plugin_disabled.revision)


def test_plugin_mcp_identity_and_prefix_conflict_with_direct_mcp(tmp_path: Path) -> None:
    node = tmp_path / "node"
    identities = ResourceIdentityIndex()
    prefixes = ToolPrefixIndex()
    plugins = _manager(node, identities=identities, prefixes=prefixes)
    direct = McpManager(
        node,
        InMemorySecretStore(),
        identity_index=identities,
        prefix_index=prefixes,
    )
    source = _write_plugin(tmp_path / "source")
    installed = plugins.install(
        plugins.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )
    enabled = plugins.enable(
        "plugin-fixture",
        "writer",
        expected_revision=installed.revision,
    )
    template = json.loads((source / "mcp" / "echo.json").read_text(encoding="utf-8"))
    template["metadata"]["name"] = "direct-conflict"
    template["spec"].pop("managedBy")
    direct_created = direct.create(McpServer.model_validate(template), expected_revision=None)
    with pytest.raises(ExtensionError) as prefix_conflict:
        direct.enable(
            "direct-conflict",
            "writer",
            expected_revision=direct_created.revision,
        )
    assert prefix_conflict.value.code == "extension_conflict"

    template["metadata"]["name"] = "plugin-fixture--echo"
    template["spec"]["policy"]["toolNamePrefix"] = "different_prefix"
    with pytest.raises(ExtensionError) as identity_conflict:
        direct.create(McpServer.model_validate(template), expected_revision=None)
    assert identity_conflict.value.code == "extension_conflict"
    assert enabled.status == "enabled"


def test_installed_plugin_manifest_must_match_authoritative_record(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source")
    manager = _manager(tmp_path / "node")
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )

    manifest_path = installed.content_root / ".openppx-plugin" / "plugin.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["spec"]["version"] = "9.9.9"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ExtensionError, match="metadata is inconsistent"):
        manager.readiness("plugin-fixture")
