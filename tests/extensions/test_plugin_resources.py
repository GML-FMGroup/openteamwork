"""Portable Plugin staging, lifecycle, projection, and security tests."""

from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

import pytest

from openppx.config import InMemorySecretStore
from openppx.config.models import ResourceMetadata
from openppx.extensions import ExtensionError, McpManager, McpServer
from openppx.extensions.indexes import ResourceIdentityIndex
from openppx.extensions.mcp_models import McpServerSpec, McpStdioTransport, McpToolPolicy
from openppx.extensions.models import ExtensionSourceRef
from openppx.extensions.plugins import PluginManager
from openppx.extensions.prefixes import ToolPrefixIndex


def _write_plugin(
    root: Path,
    *,
    version: str = "1.0.0",
    skill_body: str = "# Plugin research\n",
    risk: str = "medium",
    required_app: bool = False,
    include_hooks: bool = False,
    env_vars: list[str] | None = None,
) -> Path:
    """Create one deterministic Plugin using the shared `.agent-plugin` format."""
    manifest_dir = root / ".agent-plugin"
    skill_dir = root / "skills" / "research"
    manifest_dir.mkdir(parents=True, exist_ok=True)
    skill_dir.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, object] = {
        "name": "plugin-fixture",
        "version": version,
        "description": "A deterministic portable Plugin fixture.",
        "author": {"name": "OpenPPX", "url": "https://openppx.dev"},
        "keywords": ["fixture", "research"],
        "skills": "./skills/",
        "mcpServers": "./.mcp.json",
        "apps": "./.app.json",
        "interface": {
            "displayName": "Plugin Fixture",
            "shortDescription": "Portable Plugin fixture",
            "developerName": "OpenPPX",
            "category": "Engineering",
            "capabilities": ["Read"],
        },
    }
    if include_hooks:
        hooks_dir = root / "hooks"
        hooks_dir.mkdir(parents=True, exist_ok=True)
        (hooks_dir / "hooks.json").write_text(
            json.dumps(
                {
                    "hooks": {
                        "SessionStart": [
                            {
                                "matcher": "startup",
                                "hooks": [
                                    {
                                        "type": "command",
                                        "command": "printf started > \"$PLUGIN_DATA/session-started\"",
                                        "timeout": 5,
                                    }
                                ],
                            }
                        ]
                    }
                }
            ),
            encoding="utf-8",
        )
        manifest["hooks"] = "./hooks/hooks.json"
    (manifest_dir / "plugin.json").write_text(json.dumps(manifest), encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\n"
        "name: research\n"
        "description: Research using the fixture Plugin.\n"
        "metadata:\n"
        "  openppx:\n"
        f"    version: {version}\n"
        f"    risk: {risk}\n"
        "---\n\n"
        f"{skill_body}",
        encoding="utf-8",
    )
    (root / "mock_mcp_server.py").write_text(
        Path("tests/eval/mock_mcp_server.py").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (root / ".mcp.json").write_text(
        json.dumps(
            {
                "mcpServers": {
                    "echo": {
                        "command": sys.executable,
                        "args": ["./mock_mcp_server.py"],
                        "cwd": ".",
                        "env_vars": env_vars or [],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (root / ".app.json").write_text(
        json.dumps(
            {
                "apps": {
                    "echo": {
                        "id": "plugin_asdk_app_fixture",
                        "required": required_app,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    return root


def _manager(
    root: Path,
    *,
    identities: ResourceIdentityIndex | None = None,
    prefixes: ToolPrefixIndex | None = None,
    available_environment_keys: frozenset[str] = frozenset(),
) -> PluginManager:
    return PluginManager(
        root,
        InMemorySecretStore(),
        identity_index=identities,
        prefix_index=prefixes,
        available_environment_keys=available_environment_keys,
    )


def test_plugin_stage_preview_install_enable_update_snapshot_disable_remove(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source")
    manager = _manager(tmp_path / "node")
    staged = manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    preview = manager.preview(staged)

    assert preview.plugin_id == "plugin-fixture"
    assert preview.display_name == "Plugin Fixture"
    assert preview.resource_counts == {"apps": 1, "hooks": 0, "mcpServers": 1, "skills": 1}

    installed = manager.install(staged, expected_revision=None)
    assert installed.content_root.joinpath(".agent-plugin", "plugin.json").is_file()
    assert installed.record.spec.resources.skills[0].name == "plugin-fixture--research"
    assert installed.record.spec.resources.mcp_servers[0].name == "plugin-fixture--echo"
    assert installed.record.spec.resources.apps[0].technical_id == "plugin_asdk_app_fixture"

    enabled = manager.enable(
        "plugin-fixture",
        "writer",
        expected_revision=installed.revision,
    )
    snapshot = manager.snapshot_for_agent("writer")
    assert snapshot.plugin_ids == ("plugin-fixture",)
    assert snapshot.skills.read_skill("plugin-fixture--research").endswith("# Plugin research\n")
    assert snapshot.mcp.entries[0].record.metadata.name == "plugin-fixture--echo"
    assert snapshot.mcp.entries[0].record.spec.enabled_agent_ids == ["writer"]

    update_source = _write_plugin(
        tmp_path / "update",
        version="1.1.0",
        skill_body="# Updated research\n",
    )
    updated = manager.update(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(update_source))),
        expected_revision=enabled.revision,
    )
    assert updated.record.spec.version == "1.1.0"
    assert updated.record.spec.enabled_agent_ids == ["writer"]

    disabled = manager.disable(
        "plugin-fixture",
        "writer",
        expected_revision=updated.revision,
    )
    manager.remove("plugin-fixture", expected_revision=disabled.revision)
    with pytest.raises(ExtensionError, match="was not found"):
        manager.get("plugin-fixture")


def test_plugin_directory_and_archive_sources_share_agent_plugin_contract(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source")
    archive = tmp_path / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        for path in source.rglob("*"):
            if path.is_file():
                handle.write(path, path.relative_to(source))

    directory_manager = _manager(tmp_path / "directory-node")
    archive_manager = _manager(tmp_path / "archive-node")
    directory_preview = directory_manager.preview(
        directory_manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    )
    archive_preview = archive_manager.preview(
        archive_manager.stage(ExtensionSourceRef(type="local_archive", locator=str(archive)))
    )

    assert directory_preview.plugin_id == archive_preview.plugin_id == "plugin-fixture"
    assert directory_preview.resource_counts == archive_preview.resource_counts


def test_plugin_rejects_legacy_private_manifest_and_unsafe_component_paths(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    (legacy / ".openppx-plugin").mkdir(parents=True)
    (legacy / ".openppx-plugin" / "plugin.json").write_text(
        json.dumps({"apiVersion": "openppx.io/v1alpha1", "kind": "PluginManifest"}),
        encoding="utf-8",
    )
    with pytest.raises(ExtensionError):
        _manager(tmp_path / "legacy-node").stage(
            ExtensionSourceRef(type="local_directory", locator=str(legacy))
        )

    source = _write_plugin(tmp_path / "unsafe")
    path = source / ".agent-plugin" / "plugin.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["skills"] = "../skills"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExtensionError, match="Codex schema"):
        _manager(tmp_path / "unsafe-node").stage(
            ExtensionSourceRef(type="local_directory", locator=str(source))
        )

    source = _write_plugin(tmp_path / "private")
    path = source / ".agent-plugin" / "plugin.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["spec"] = {"resources": {"agentTemplates": []}}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    with pytest.raises(ExtensionError, match="Codex schema"):
        _manager(tmp_path / "private-node").stage(
            ExtensionSourceRef(type="local_directory", locator=str(source))
        )


def test_plugin_readiness_covers_environment_registered_apps_and_hooks(tmp_path: Path) -> None:
    env_source = _write_plugin(tmp_path / "env-source", env_vars=["FIXTURE_TOKEN"])
    missing_manager = _manager(tmp_path / "missing-node")
    installed = missing_manager.install(
        missing_manager.stage(ExtensionSourceRef(type="local_directory", locator=str(env_source))),
        expected_revision=None,
    )
    assert missing_manager.readiness("plugin-fixture").issues == ("mcp_environment_missing",)
    with pytest.raises(ExtensionError, match="dependencies are not ready"):
        missing_manager.enable("plugin-fixture", "writer", expected_revision=installed.revision)

    ready_manager = _manager(
        tmp_path / "ready-node",
        available_environment_keys=frozenset({"FIXTURE_TOKEN"}),
    )
    ready_manager.install(
        ready_manager.stage(ExtensionSourceRef(type="local_directory", locator=str(env_source))),
        expected_revision=None,
    )
    assert ready_manager.readiness("plugin-fixture").ready

    app_source = _write_plugin(tmp_path / "app-source", required_app=True)
    app_manager = _manager(tmp_path / "app-node")
    app_manager.install(
        app_manager.stage(ExtensionSourceRef(type="local_directory", locator=str(app_source))),
        expected_revision=None,
    )
    assert app_manager.readiness("plugin-fixture").issues == ("registered_app_unavailable",)
    app_manager.register_app_resolver("fixture", lambda mapping: mapping.technical_id.endswith("fixture"))
    assert app_manager.readiness("plugin-fixture").ready

    hook_source = _write_plugin(tmp_path / "hook-source", include_hooks=True)
    hook_manager = _manager(tmp_path / "hook-node")
    hook_manager.install(
        hook_manager.stage(ExtensionSourceRef(type="local_directory", locator=str(hook_source))),
        expected_revision=None,
    )
    hook = hook_manager.get("plugin-fixture")
    assert hook_manager.readiness("plugin-fixture").issues == ("plugin_hooks_untrusted",)
    status = hook_manager.hook_status("plugin-fixture")
    assert status.declared_events == ("SessionStart",)
    assert status.executable_count == 1
    assert not status.trusted
    trusted = hook_manager.trust_hooks("plugin-fixture", expected_revision=hook.revision)
    assert trusted.trusted
    assert hook_manager.readiness("plugin-fixture").ready


def test_high_risk_skill_requires_confirmation(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source", risk="high")
    manager = _manager(tmp_path / "node")
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )

    with pytest.raises(ExtensionError, match="requires confirmation"):
        manager.enable("plugin-fixture", "writer", expected_revision=installed.revision)
    enabled = manager.enable(
        "plugin-fixture",
        "writer",
        expected_revision=installed.revision,
        confirmed=True,
    )
    assert enabled.record.spec.enabled_agent_ids == ["writer"]


def test_plugin_mcp_identity_and_prefix_conflict_with_direct_mcp(tmp_path: Path) -> None:
    identities = ResourceIdentityIndex()
    prefixes = ToolPrefixIndex()
    direct = McpManager(
        tmp_path / "node",
        InMemorySecretStore(),
        identity_index=identities,
        prefix_index=prefixes,
    )
    plugin = _manager(tmp_path / "node", identities=identities, prefixes=prefixes)
    direct.create(
        McpServer(
            api_version="openppx.io/v1alpha1",
            kind="McpServer",
            metadata=ResourceMetadata(name="plugin-fixture--echo"),
            spec=McpServerSpec(
                display_name="Conflict",
                description="Direct identity conflict.",
                transport=McpStdioTransport(type="stdio", command="python3"),
                policy=McpToolPolicy(),
            ),
        ),
        expected_revision=None,
    )

    source = _write_plugin(tmp_path / "source")
    staged = plugin.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    with pytest.raises(ExtensionError, match="identity"):
        plugin.install(staged, expected_revision=None)


def test_installed_plugin_manifest_must_match_authoritative_record(tmp_path: Path) -> None:
    source = _write_plugin(tmp_path / "source")
    manager = _manager(tmp_path / "node")
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )
    path = installed.content_root / ".agent-plugin" / "plugin.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["description"] = "Tampered description"
    path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(ExtensionError, match="metadata is inconsistent"):
        manager.readiness("plugin-fixture")
