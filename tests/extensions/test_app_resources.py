"""App definition, connection/auth, policy, and lifecycle tests."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from openppx.config import InMemorySecretStore, SecretRef, SecretValue
from openppx.extensions import ExtensionError, McpManager, McpServer
from openppx.extensions.app_models import AppConnection, AppDefinition
from openppx.extensions.app_adapters import (
    NativeAppAdapterProbe,
    NativeAppAdapterReadiness,
    NativeAppAdapterRegistry,
    NativeAppContext,
)
from openppx.extensions.apps import AppManager
from openppx.extensions.prefixes import ToolPrefixIndex


_DIGEST = "sha256:" + "a" * 64


class _NativeTool:
    name = "native_lookup"


class _FixtureNativeAdapter:
    adapter_id = "fixture-native"

    def readiness(self, context: NativeAppContext) -> NativeAppAdapterReadiness:
        return NativeAppAdapterReadiness(ready=bool(context.tools))

    async def probe(self, context: NativeAppContext) -> NativeAppAdapterProbe:
        return NativeAppAdapterProbe(ready=bool(context.tools))

    def build_tools(self, context: NativeAppContext) -> tuple[object, ...]:
        return (_NativeTool(),) if context.tools else ()


def _definition(
    *,
    name: str = "fixture-app",
    auth_type: str = "secret",
    tools: list[dict[str, object]] | None = None,
) -> AppDefinition:
    credentials = (
        []
        if auth_type == "none"
        else [{"name": "api-token", "label": "API token", "required": True}]
    )
    environment = (
        {}
        if auth_type == "none"
        else {"FIXTURE_TOKEN": {"kind": "credential", "credentialSlot": "api-token"}}
    )
    return AppDefinition.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AppDefinition",
            "metadata": {"name": name},
            "spec": {
                "displayName": "Fixture App",
                "description": "A deterministic MCP-backed App fixture.",
                "version": "1.0.0",
                "category": "productivity",
                "developer": "OpenPPX",
                "source": {
                    "type": "builtin",
                    "locator": "fixture-app",
                    "version": "1.0.0",
                    "revision": "builtin:1.0.0",
                    "digest": _DIGEST,
                },
                "auth": {"type": auth_type, "credentials": credentials},
                "implementation": {
                    "type": "mcp",
                    "transport": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(Path("tests/eval/mock_mcp_server.py").resolve())],
                        "environment": environment,
                    },
                },
                "tools": tools
                or [
                    {
                        "name": "echo_context",
                        "title": "Echo context",
                        "description": "Echo one safe token.",
                        "access": "read",
                        "risk": "low",
                        "enabledByDefault": True,
                    }
                ],
                "policy": {
                    "requireConfirmation": False,
                    "progressEvents": True,
                    "longTaskProxy": True,
                    "inlineBudgetMs": 500,
                },
            },
        }
    )


def _connection(
    *,
    name: str = "fixture-account",
    app_id: str = "fixture-app",
    credential_refs: dict[str, dict[str, str]] | None = None,
    enabled_tools: list[str] | None = None,
) -> AppConnection:
    spec: dict[str, object] = {
        "appId": app_id,
        "displayName": "Fixture account",
        "credentialRefs": credential_refs or {},
        "requireConfirmation": False,
        "enabledAgentIds": [],
    }
    if enabled_tools is not None:
        spec["enabledTools"] = enabled_tools
    return AppConnection.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AppConnection",
            "metadata": {"name": name},
            "spec": spec,
        }
    )


def _native_definition() -> AppDefinition:
    payload = _definition(auth_type="none").model_dump(mode="json", by_alias=True)
    payload["metadata"]["name"] = "native-app"
    payload["spec"]["displayName"] = "Native App"
    payload["spec"]["implementation"] = {
        "type": "native",
        "adapter": "fixture-native",
    }
    payload["spec"]["tools"][0]["name"] = "native_lookup"
    return AppDefinition.model_validate(payload)


def test_native_app_adapter_is_explicit_trusted_runtime_boundary(tmp_path: Path) -> None:
    missing = AppManager(tmp_path / "missing", InMemorySecretStore())
    missing.install_definition(_native_definition(), expected_revision=None)
    missing.create_connection(
        _connection(name="native-account", app_id="native-app"),
        expected_revision=None,
    )
    assert missing.readiness("native-account").issues == ("adapter_missing",)

    adapters = NativeAppAdapterRegistry()
    adapters.register(_FixtureNativeAdapter())
    manager = AppManager(
        tmp_path / "ready",
        InMemorySecretStore(),
        adapter_registry=adapters,
    )
    installed = manager.install_definition(_native_definition(), expected_revision=None)
    connection = manager.create_connection(
        _connection(name="native-account", app_id="native-app"),
        expected_revision=None,
    )
    enabled = manager.enable_connection(
        "native-account",
        "writer",
        expected_revision=connection.revision,
    )
    snapshot = manager.snapshot_for_agent("writer")

    assert installed.record.spec.implementation.type == "native"
    assert enabled.status == "connected"
    assert snapshot.mcp.entries == ()
    assert [tool.name for tool in manager.build_native_tools(snapshot)] == ["native_lookup"]
    assert [tool.name for tool in manager.native_tools_for_probe("native-account")] == [
        "native_lookup"
    ]


def test_app_schema_separates_definition_connection_and_rejects_cli_installers() -> None:
    definition = _definition()
    payload = definition.model_dump(mode="json", by_alias=True)
    payload["spec"]["commandInstaller"] = "pip install unsafe"
    with pytest.raises(ValidationError):
        AppDefinition.model_validate(payload)

    payload = definition.model_dump(mode="json", by_alias=True)
    payload["spec"]["implementation"]["transport"]["environment"]["FIXTURE_TOKEN"] = {
        "kind": "secret",
        "secretRef": {"store": "system", "name": "not-allowed-in-definition"},
    }
    with pytest.raises(ValidationError):
        AppDefinition.model_validate(payload)

    connection = _connection(
        credential_refs={"api-token": {"store": "system", "name": "fixture-token"}}
    )
    assert "fixture-token" not in definition.model_dump_json()
    assert connection.spec.app_id == "fixture-app"


def test_app_definition_connection_auth_enable_snapshot_and_remove(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    manager = AppManager(tmp_path, secrets)
    installed = manager.install_definition(_definition(), expected_revision=None)
    connection = manager.create_connection(_connection(), expected_revision=None)

    assert installed.status == "installed"
    assert connection.status == "disconnected"
    assert manager.readiness("fixture-account").auth_state == "missing"
    with pytest.raises(ExtensionError) as missing:
        manager.enable_connection(
            "fixture-account",
            "writer",
            expected_revision=connection.revision,
        )
    assert missing.value.code == "dependency_missing"

    ref = SecretRef(store="system", name="fixture-token")
    secrets.put(ref, SecretValue("never-persisted"))
    authorized = manager.reauthorize(
        "fixture-account",
        {"api-token": ref},
        expected_revision=connection.revision,
    )
    assert manager.readiness("fixture-account").ready is True
    enabled = manager.enable_connection(
        "fixture-account",
        "writer",
        expected_revision=authorized.revision,
    )
    snapshot = manager.snapshot_for_agent("writer")

    assert enabled.status == "connected"
    assert snapshot.connection_ids == ("fixture-account",)
    projected = snapshot.mcp.entries[0].record
    assert projected.spec.managed_by is not None
    assert projected.spec.managed_by.kind == "app"
    assert projected.spec.managed_by.name == "fixture-app"
    assert projected.spec.policy.tool_filter == ["echo_context"]
    assert "never-persisted" not in manager.get_connection("fixture-account").record.model_dump_json()

    with pytest.raises(ExtensionError) as referenced:
        manager.remove_definition("fixture-app", expected_revision=installed.revision)
    assert referenced.value.code == "extension_in_use"
    with pytest.raises(ExtensionError) as active:
        manager.remove_connection("fixture-account", expected_revision=enabled.revision)
    assert active.value.code == "extension_in_use"

    disabled = manager.disable_connection(
        "fixture-account",
        "writer",
        expected_revision=enabled.revision,
    )
    manager.remove_connection("fixture-account", expected_revision=disabled.revision)
    manager.remove_definition("fixture-app", expected_revision=installed.revision)
    assert manager.list_definitions() == ()
    assert manager.list_connections() == ()


def test_connection_policy_update_cannot_replace_credential_refs(tmp_path: Path) -> None:
    manager = AppManager(tmp_path, InMemorySecretStore())
    manager.install_definition(_definition(), expected_revision=None)
    created = manager.create_connection(
        _connection(
            credential_refs={
                "api-token": {"store": "system", "name": "original-token"}
            }
        ),
        expected_revision=None,
    )
    candidate = created.record.model_copy(
        update={
            "spec": created.record.spec.model_copy(
                update={
                    "display_name": "Renamed account",
                    "credential_refs": {
                        "api-token": SecretRef(store="system", name="replacement-token")
                    },
                }
            )
        }
    )

    updated = manager.update_connection(candidate, expected_revision=created.revision)

    assert updated.record.spec.display_name == "Renamed account"
    assert updated.record.spec.credential_refs == {
        "api-token": SecretRef(store="system", name="original-token")
    }


def test_app_probe_snapshot_does_not_enable_connection(tmp_path: Path) -> None:
    manager = AppManager(tmp_path, InMemorySecretStore())
    manager.install_definition(_definition(auth_type="none"), expected_revision=None)
    created = manager.create_connection(_connection(), expected_revision=None)

    snapshot = manager.mcp_snapshot_for_probe("fixture-account")

    assert snapshot.names == ("app-fixture-app-fixture-account",)
    assert snapshot.entries[0].record.spec.enabled_agent_ids == ["connection-probe"]
    assert manager.get_connection("fixture-account").revision == created.revision
    assert manager.get_connection("fixture-account").record.spec.enabled_agent_ids == []


def test_app_tool_policy_only_narrows_definition_and_high_risk_requires_confirmation(
    tmp_path: Path,
) -> None:
    manager = AppManager(tmp_path, InMemorySecretStore())
    tools = [
        {
            "name": "read_item",
            "title": "Read item",
            "description": "Read one item.",
            "access": "read",
            "risk": "low",
            "enabledByDefault": True,
        },
        {
            "name": "delete_item",
            "title": "Delete item",
            "description": "Delete one item.",
            "access": "write",
            "risk": "high",
            "enabledByDefault": False,
        },
    ]
    manager.install_definition(
        _definition(auth_type="none", tools=tools),
        expected_revision=None,
    )
    with pytest.raises(ExtensionError) as unknown_tool:
        manager.create_connection(
            _connection(enabled_tools=["unknown_tool"]),
            expected_revision=None,
        )
    assert unknown_tool.value.code == "invalid_policy"

    connection = manager.create_connection(
        _connection(enabled_tools=["delete_item"]),
        expected_revision=None,
    )
    with pytest.raises(ExtensionError) as confirmation:
        manager.enable_connection(
            "fixture-account",
            "writer",
            expected_revision=connection.revision,
        )
    assert confirmation.value.code == "confirmation_required"
    enabled = manager.enable_connection(
        "fixture-account",
        "writer",
        expected_revision=connection.revision,
        confirmed=True,
    )
    projected = manager.snapshot_for_agent("writer").mcp.entries[0].record
    assert enabled.status == "connected"
    assert projected.spec.policy.require_confirmation is True
    assert projected.spec.policy.tool_filter == ["delete_item"]


def test_app_update_preserves_connection_snapshot_and_rejects_incompatible_definition(
    tmp_path: Path,
) -> None:
    manager = AppManager(tmp_path, InMemorySecretStore())
    installed = manager.install_definition(_definition(auth_type="none"), expected_revision=None)
    connection = manager.create_connection(_connection(enabled_tools=["echo_context"]), expected_revision=None)
    enabled = manager.enable_connection(
        "fixture-account",
        "writer",
        expected_revision=connection.revision,
    )
    first = manager.snapshot_for_agent("writer")

    incompatible = _definition(
        auth_type="none",
        tools=[
            {
                "name": "replacement",
                "title": "Replacement",
                "description": "A replacement tool.",
                "access": "read",
                "risk": "low",
                "enabledByDefault": True,
            }
        ],
    )
    with pytest.raises(ExtensionError) as conflict:
        manager.update_definition(incompatible, expected_revision=installed.revision)
    assert conflict.value.code == "extension_in_use"

    compatible = _definition(auth_type="none").model_copy(
        update={
            "spec": _definition(auth_type="none").spec.model_copy(
                update={"description": "Updated App definition."}
            )
        }
    )
    updated = manager.update_definition(compatible, expected_revision=installed.revision)
    second = manager.snapshot_for_agent("writer")

    assert manager.get_connection("fixture-account").revision == enabled.revision
    assert first.revision != second.revision
    assert first.entries[0].definition.spec.description == "A deterministic MCP-backed App fixture."
    assert second.entries[0].definition.spec.description == "Updated App definition."
    assert updated.record.spec.description == "Updated App definition."


def test_app_and_direct_mcp_prefixes_conflict_in_both_enablement_orders(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    prefixes = ToolPrefixIndex()
    apps = AppManager(tmp_path, secrets, prefix_index=prefixes)
    direct = McpManager(tmp_path, secrets, prefix_index=prefixes)
    apps.install_definition(_definition(auth_type="none"), expected_revision=None)
    connection = apps.create_connection(_connection(), expected_revision=None)
    direct_record = McpServer.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "McpServer",
            "metadata": {"name": "direct-fixture"},
            "spec": {
                "displayName": "Direct fixture",
                "description": "Prefix conflict fixture.",
                "transport": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": [str(Path("tests/eval/mock_mcp_server.py").resolve())],
                    "environment": {},
                },
                "policy": {"toolNamePrefix": "app_fixture_app_fixture_account"},
                "risk": "low",
                "enabledAgentIds": [],
            },
        }
    )
    direct_created = direct.create(direct_record, expected_revision=None)

    app_enabled = apps.enable_connection(
        "fixture-account",
        "writer",
        expected_revision=connection.revision,
    )
    with pytest.raises(ExtensionError) as direct_conflict:
        direct.enable(
            "direct-fixture",
            "writer",
            expected_revision=direct_created.revision,
        )
    assert direct_conflict.value.code == "extension_conflict"

    direct_enabled = direct.enable(
        "direct-fixture",
        "reviewer",
        expected_revision=direct_created.revision,
    )
    with pytest.raises(ExtensionError) as app_conflict:
        apps.enable_connection(
            "fixture-account",
            "reviewer",
            expected_revision=app_enabled.revision,
        )
    assert app_conflict.value.code == "extension_conflict"
    assert direct_enabled.record.spec.enabled_agent_ids == ["reviewer"]
