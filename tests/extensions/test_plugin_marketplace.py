"""Codex-compatible Plugin marketplace lifecycle tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openppx.config import InMemorySecretStore
from openppx.extensions import PluginManager
from openppx.extensions.plugin_marketplace import (
    PluginMarketplaceManager,
    PluginMarketplaceSourceSpec,
)


def _write_marketplace(root: Path) -> Path:
    plugin = root / "plugins" / "research"
    (plugin / ".agent-plugin").mkdir(parents=True)
    (plugin / ".agent-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "research-tools",
                "version": "1.0.0",
                "description": "Portable research tools.",
                "author": {"name": "OpenPPX"},
            }
        ),
        encoding="utf-8",
    )
    catalog = root / ".agents" / "plugins"
    catalog.mkdir(parents=True)
    (catalog / "marketplace.json").write_text(
        json.dumps(
            {
                "name": "fixture-marketplace",
                "plugins": [
                    {
                        "name": "research-tools",
                        "version": "1.0.0",
                        "description": "Portable research tools.",
                        "author": {"name": "OpenPPX"},
                        "category": "Developer Tools",
                        "source": "./plugins/research",
                        "installationPolicy": "AVAILABLE",
                        "authenticationPolicy": "ON_INSTALL",
                        "interface": {"displayName": "Research Tools"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return root


def test_local_marketplace_refresh_projects_into_plugin_install(tmp_path: Path) -> None:
    source = _write_marketplace(tmp_path / "marketplace")
    manager = PluginMarketplaceManager(tmp_path / "node")
    created = manager.create(
        "fixture",
        PluginMarketplaceSourceSpec(
            display_name="Fixture",
            type="local",
            locator=str(source),
        ),
    )
    assert not created.to_payload()["ready"]

    refreshed = manager.refresh("fixture", expected_revision=created.revision)
    assert refreshed.record.spec.entry_count == 1
    entry = manager.entries("fixture")[0]
    assert entry.display_name == "Research Tools"
    assert entry.source is not None
    assert entry.to_payload()["ready"]

    plugins = PluginManager(tmp_path / "node", InMemorySecretStore())
    preview = plugins.preview(plugins.stage(entry.source))
    assert preview.plugin_id == "research-tools"


def test_marketplace_update_invalidates_cache_and_remove_checks_revision(tmp_path: Path) -> None:
    first = _write_marketplace(tmp_path / "first")
    second = _write_marketplace(tmp_path / "second")
    manager = PluginMarketplaceManager(tmp_path / "node")
    current = manager.create(
        "fixture",
        PluginMarketplaceSourceSpec(display_name="Fixture", type="local", locator=str(first)),
    )
    current = manager.refresh("fixture", expected_revision=current.revision)
    updated = manager.update(
        "fixture",
        PluginMarketplaceSourceSpec(display_name="Other", type="local", locator=str(second)),
        expected_revision=current.revision,
    )
    assert updated.record.spec.catalog_digest is None
    with pytest.raises(Exception, match="revision"):
        manager.remove("fixture", expected_revision=current.revision)
    manager.remove("fixture", expected_revision=updated.revision)
    assert manager.list() == ()


def test_marketplace_requires_pinned_npm_and_external_git_sources(tmp_path: Path) -> None:
    source = _write_marketplace(tmp_path / "marketplace")
    path = source / ".agents" / "plugins" / "marketplace.json"
    raw = json.loads(path.read_text())
    raw["plugins"] = [
        {"name": "npm-one", "source": {"source": "npm", "package": "npm-one"}},
        {
            "name": "npm-ready",
            "source": {"source": "npm", "package": "@openppx/npm-ready", "version": "1.2.3"},
        },
        {"name": "git-one", "source": {"source": "git", "url": "https://example.com/a.git", "ref": "main"}},
    ]
    path.write_text(json.dumps(raw), encoding="utf-8")
    manager = PluginMarketplaceManager(tmp_path / "node")
    current = manager.create(
        "fixture",
        PluginMarketplaceSourceSpec(display_name="Fixture", type="local", locator=str(source)),
    )
    manager.refresh("fixture", expected_revision=current.revision)
    entries = manager.entries("fixture")
    assert {item.issue for item in entries} == {
        "marketplace_npm_source_requires_exact_version",
        "marketplace_git_source_requires_pinned_sha",
        None,
    }
    npm_entry = next(item for item in entries if item.plugin_id == "npm-ready")
    assert npm_entry.source is not None
    assert npm_entry.source.type == "npm"
    assert npm_entry.source.version == "1.2.3"


def test_repository_portable_plugin_fixture_covers_all_standard_components(tmp_path: Path) -> None:
    source = Path(__file__).parents[2] / "examples" / "plugin-marketplace"
    marketplaces = PluginMarketplaceManager(tmp_path / "node")
    current = marketplaces.create(
        "examples",
        PluginMarketplaceSourceSpec(display_name="Examples", type="local", locator=str(source)),
    )
    marketplaces.refresh("examples", expected_revision=current.revision)
    entry = marketplaces.entries("examples")[0]
    assert entry.source is not None

    plugins = PluginManager(tmp_path / "node", InMemorySecretStore())
    staged = plugins.stage(entry.source)
    preview = plugins.preview(staged)
    assert preview.resource_counts == {"apps": 1, "hooks": 1, "mcpServers": 1, "skills": 1}
    installed = plugins.install(staged, expected_revision=None, confirmed=True)
    status = plugins.hook_status(installed.record.metadata.name)
    assert status.handler_count == 1
    assert not status.trusted
    assert installed.content_root.joinpath("assets", "logo.svg").is_file()
