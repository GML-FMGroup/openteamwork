"""Strict direct MCP resource and lifecycle tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openppx.config import InMemorySecretStore, SecretRef, SecretValue
from openppx.extensions import ExtensionError
from openppx.extensions.mcp import McpManager
from openppx.extensions.mcp_models import McpServer


def _server(
    name: str = "docs",
    *,
    transport: dict[str, object] | None = None,
    risk: str = "low",
    prefix: str | None = None,
    managed_by: dict[str, str] | None = None,
) -> McpServer:
    policy: dict[str, object] = {
        "requireConfirmation": False,
        "progressEvents": True,
        "longTaskProxy": True,
        "inlineBudgetMs": 750,
    }
    if prefix is not None:
        policy["toolNamePrefix"] = prefix
    return McpServer.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "McpServer",
            "metadata": {"name": name},
            "spec": {
                "displayName": f"{name} MCP",
                "description": "Deterministic direct MCP fixture.",
                "transport": transport
                or {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": ["server.py"],
                    "environment": {},
                },
                "policy": policy,
                "risk": risk,
                "enabledAgentIds": [],
                "managedBy": managed_by,
            },
        }
    )


def test_mcp_schema_rejects_ambiguous_transport_secret_leaks_and_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        _server(
            transport={
                "type": "streamable_http",
                "url": "https://token@example.com/mcp",
                "headers": {},
            }
        )
    protected_query = _server(
        transport={
            "type": "streamable_http",
            "url": "https://example.com/mcp",
            "headers": {},
            "query": {
                "apiKey": {
                    "kind": "secret",
                    "secretRef": {"store": "system", "name": "browserbase-key"},
                }
            },
        }
    )
    assert protected_query.spec.transport.query["apiKey"].secret_ref.name == "browserbase-key"
    with pytest.raises(ValidationError):
        _server(
            transport={
                "type": "streamable_http",
                "url": "https://example.com/mcp?token=secret",
                "headers": {},
            }
        )
    with pytest.raises(ValidationError):
        _server(
            transport={
                "type": "stdio",
                "command": "python",
                "args": [],
                "environment": {
                    "TOKEN": {"kind": "secret", "value": "plaintext-not-allowed"}
                },
            }
        )
    payload = _server().model_dump(mode="json", by_alias=True)
    payload["spec"]["unexpected"] = True
    with pytest.raises(ValidationError):
        McpServer.model_validate(payload)


def test_mcp_create_enable_disable_remove_and_snapshot(tmp_path: Path) -> None:
    manager = McpManager(tmp_path, InMemorySecretStore())
    created = manager.create(_server(), expected_revision=None)

    assert created.status == "disabled"
    assert manager.readiness("docs").ready is True
    enabled = manager.enable("docs", "writer", expected_revision=created.revision)
    snapshot = manager.snapshot_for_agent("writer")

    assert enabled.status == "enabled"
    assert snapshot.names == ("docs",)
    assert snapshot.entries[0].record.spec.policy.resolved_prefix("docs") == "mcp_docs"
    with pytest.raises(ExtensionError) as in_use:
        manager.remove("docs", expected_revision=enabled.revision)
    assert in_use.value.code == "extension_in_use"

    disabled = manager.disable("docs", "writer", expected_revision=enabled.revision)
    manager.remove("docs", expected_revision=disabled.revision)
    assert manager.list() == ()


def test_mcp_secret_executable_risk_revision_and_prefix_checks(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    manager = McpManager(tmp_path, secrets, executable_resolver=lambda command: command if command == "python" else None)
    secured = _server(
        "secured",
        transport={
            "type": "stdio",
            "command": "missing-python",
            "args": [],
            "environment": {
                "API_TOKEN": {
                    "kind": "secret",
                    "secretRef": {"store": "system", "name": "mcp-token"},
                }
            },
        },
        risk="high",
        prefix="shared",
    )
    created = manager.create(secured, expected_revision=None)
    readiness = manager.readiness("secured")

    assert readiness.ready is False
    assert readiness.auth_state == "missing"
    assert readiness.executable_state == "missing"
    with pytest.raises(ExtensionError) as dependency:
        manager.enable("secured", "writer", expected_revision=created.revision, confirmed=True)
    assert dependency.value.code == "dependency_missing"

    secrets.put(SecretRef(store="system", name="mcp-token"), SecretValue("never-persisted"))
    manager = McpManager(tmp_path, secrets, executable_resolver=lambda _command: "/usr/bin/python")
    with pytest.raises(ExtensionError) as confirmation:
        manager.enable("secured", "writer", expected_revision=created.revision)
    assert confirmation.value.code == "confirmation_required"
    enabled = manager.enable("secured", "writer", expected_revision=created.revision, confirmed=True)

    with pytest.raises(ExtensionError) as revision:
        manager.disable("secured", "writer", expected_revision="sha256:" + "0" * 64)
    assert revision.value.code == "revision_conflict"

    other = manager.create(_server("other", prefix="shared"), expected_revision=None)
    with pytest.raises(ExtensionError) as collision:
        manager.enable("other", "writer", expected_revision=other.revision)
    assert collision.value.code == "extension_conflict"
    assert manager.get("secured").revision == enabled.revision


def test_mcp_managed_resource_cannot_be_removed_by_direct_manager(tmp_path: Path) -> None:
    manager = McpManager(tmp_path, InMemorySecretStore())
    created = manager.create(
        _server("managed", managed_by={"kind": "plugin", "name": "fixture-plugin"}),
        expected_revision=None,
    )

    with pytest.raises(ExtensionError) as error:
        manager.remove("managed", expected_revision=created.revision)

    assert error.value.code == "invalid_operation"


def test_mcp_update_preserves_enablement_and_old_snapshot(tmp_path: Path) -> None:
    manager = McpManager(tmp_path, InMemorySecretStore())
    created = manager.create(_server(), expected_revision=None)
    enabled = manager.enable("docs", "writer", expected_revision=created.revision)
    first = manager.snapshot_for_agent("writer")
    candidate = _server().model_copy(
        update={
            "spec": _server().spec.model_copy(update={"description": "Updated MCP description."})
        }
    )

    updated = manager.update(candidate, expected_revision=enabled.revision)
    second = manager.snapshot_for_agent("writer")

    assert updated.record.spec.enabled_agent_ids == ["writer"]
    assert first.revision != second.revision
    assert first.entries[0].record.spec.description == "Deterministic direct MCP fixture."
    assert second.entries[0].record.spec.description == "Updated MCP description."


def test_mcp_probe_snapshot_does_not_change_agent_enablement(tmp_path: Path) -> None:
    manager = McpManager(tmp_path, InMemorySecretStore())
    created = manager.create(_server("probe-only"), expected_revision=None)

    snapshot = manager.snapshot_for_probe("probe-only")

    assert snapshot.names == ("probe-only",)
    assert snapshot.entries[0].revision == created.revision
    assert manager.get("probe-only").record.spec.enabled_agent_ids == []
