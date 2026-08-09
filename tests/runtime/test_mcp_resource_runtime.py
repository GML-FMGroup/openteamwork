"""Direct MCP resource to real ADK toolset integration tests."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from google.adk.tools.mcp_tool.mcp_session_manager import (
    StdioConnectionParams,
    StreamableHTTPConnectionParams,
)

from openppx.config import InMemorySecretStore, SecretRef, SecretValue
from openppx.extensions.mcp import McpManager
from openppx.extensions.mcp_models import McpServer
from openppx.runtime.mcp_adapter import McpRuntimeAdapter


def _record(
    name: str,
    transport: dict[str, object],
    *,
    network_access: str = "write",
) -> McpServer:
    return McpServer.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "McpServer",
            "metadata": {"name": name},
            "spec": {
                "displayName": name,
                "description": "Runtime fixture.",
                "transport": transport,
                "policy": {
                    "toolNamePrefix": f"mcp_{name}",
                    "toolFilter": [],
                    "requireConfirmation": False,
                    "progressEvents": True,
                    "longTaskProxy": True,
                    "inlineBudgetMs": 500,
                    "networkAccess": network_access,
                },
                "risk": "low",
                "enabledAgentIds": [],
            },
        }
    )


def _enabled(manager: McpManager, record: McpServer, agent_id: str = "writer"):
    created = manager.create(record, expected_revision=None)
    manager.enable(record.metadata.name, agent_id, expected_revision=created.revision)
    return manager.snapshot_for_agent(agent_id)


def test_runtime_adapter_resolves_secrets_only_into_connection_objects(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    secrets.put(SecretRef(store="system", name="stdio-token"), SecretValue("stdio-secret"))
    secrets.put(SecretRef(store="system", name="http-token"), SecretValue("http-secret"))
    secrets.put(SecretRef(store="system", name="query-token"), SecretValue("query secret/+"))
    manager = McpManager(tmp_path, secrets)
    _enabled(
        manager,
        _record(
            "stdio",
            {
                "type": "stdio",
                "command": sys.executable,
                "args": ["server.py"],
                "environment": {
                    "TOKEN": {
                        "kind": "secret",
                        "secretRef": {"store": "system", "name": "stdio-token"},
                    }
                },
            },
        ),
    )
    _enabled(
        manager,
        _record(
            "remote",
            {
                "type": "streamable_http",
                "url": "https://example.com/mcp",
                "headers": {
                    "Authorization": {
                        "kind": "secret",
                        "secretRef": {"store": "system", "name": "http-token"},
                        "prefix": "Bearer ",
                    }
                },
                "query": {
                    "apiKey": {
                        "kind": "secret",
                        "secretRef": {"store": "system", "name": "query-token"},
                    }
                },
            },
        ),
    )
    snapshot = manager.snapshot_for_agent("writer")

    build = McpRuntimeAdapter(secrets).build(snapshot)

    assert build.diagnostics == ()
    assert isinstance(build.toolsets[0]._connection_params, StreamableHTTPConnectionParams) or isinstance(
        build.toolsets[0]._connection_params, StdioConnectionParams
    )
    by_name = {toolset.meta.name: toolset for toolset in build.toolsets}
    assert by_name["stdio"]._connection_params.server_params.env == {"TOKEN": "stdio-secret"}
    assert by_name["remote"]._connection_params.headers == {"Authorization": "Bearer http-secret"}
    assert by_name["remote"]._connection_params.url == "https://example.com/mcp?apiKey=query+secret%2F%2B"
    persisted = manager.get("stdio").record.model_dump_json()
    assert "stdio-secret" not in persisted
    assert "http-secret" not in persisted
    assert "query secret/+" not in manager.get("remote").record.model_dump_json()


def test_remote_mcp_build_preserves_declared_network_effect(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    manager = McpManager(tmp_path, secrets)
    snapshot = _enabled(
        manager,
        _record(
            "readonly",
            {
                "type": "streamable_http",
                "url": "https://docs.example.com/mcp",
                "headers": {},
            },
            network_access="read",
        ),
    )

    build = McpRuntimeAdapter(secrets).build(snapshot)

    assert build.network_policies == (
        ("mcp_readonly", ("https://docs.example.com/mcp", "read")),
    )


def test_missing_secret_is_diagnostic_and_does_not_block_other_mcp(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    ref = SecretRef(store="system", name="missing-token")
    secrets.put(ref, SecretValue("available-during-enable"))
    manager = McpManager(tmp_path, secrets)
    _enabled(
        manager,
        _record(
            "missing",
            {
                "type": "streamable_http",
                "url": "https://example.com/mcp",
                "headers": {
                    "Authorization": {
                        "kind": "secret",
                        "secretRef": {"store": "system", "name": "missing-token"},
                    }
                },
            },
        ),
    )
    secrets.delete(ref)

    build = McpRuntimeAdapter(secrets).build(manager.snapshot_for_agent("writer"))

    assert build.toolsets == ()
    assert build.diagnostics[0].server_id == "missing"
    assert build.diagnostics[0].code == "authentication_missing"
    assert "missing-token" not in build.diagnostics[0].message


def test_local_stdio_fixture_discovers_real_mcp_tool(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    manager = McpManager(tmp_path, secrets)
    fixture = Path("tests/eval/mock_mcp_server.py").resolve()
    snapshot = _enabled(
        manager,
        _record(
            "eval",
            {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(fixture)],
                "environment": {},
            },
        ),
    )
    adapter = McpRuntimeAdapter(secrets)

    async def _discover() -> list[str]:
        build = adapter.build(snapshot)
        try:
            tools = await build.toolsets[0].get_tools_with_prefix()
            return [tool.name for tool in tools]
        finally:
            await build.toolsets[0].close()

    names = asyncio.run(_discover())

    assert "mcp_eval_echo_context" in names


def test_connection_probe_reports_prefixed_tools_and_closes_fixture(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    manager = McpManager(tmp_path, secrets)
    fixture = Path("tests/eval/mock_mcp_server.py").resolve()
    snapshot = _enabled(
        manager,
        _record(
            "probe",
            {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(fixture)],
                "environment": {},
            },
        ),
    )

    report = asyncio.run(McpRuntimeAdapter(secrets).probe(snapshot))

    assert report.diagnostics == ()
    assert report.results[0]["status"] == "ok"
    assert report.results[0]["tool_names"] == ["mcp_probe_echo_context"]
