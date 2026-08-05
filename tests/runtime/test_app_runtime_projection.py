"""App connection projection through the shared MCP runtime adapter."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from openppx.config import InMemorySecretStore
from openppx.extensions.app_models import AppConnection, AppDefinition
from openppx.extensions.apps import AppManager
from openppx.runtime.mcp_adapter import McpRuntimeAdapter


def test_app_fixture_projects_to_real_mcp_toolset_without_app_specific_client(tmp_path: Path) -> None:
    secrets = InMemorySecretStore()
    manager = AppManager(tmp_path, secrets)
    definition = AppDefinition.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AppDefinition",
            "metadata": {"name": "echo-app"},
            "spec": {
                "displayName": "Echo App",
                "description": "Runtime projection fixture.",
                "version": "1.0.0",
                "category": "testing",
                "developer": "OpenPPX",
                "source": {
                    "type": "builtin",
                    "locator": "echo-app",
                    "version": "1.0.0",
                    "revision": "builtin:1.0.0",
                    "digest": "sha256:" + "b" * 64,
                },
                "auth": {"type": "none", "credentials": []},
                "implementation": {
                    "type": "mcp",
                    "transport": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(Path("tests/eval/mock_mcp_server.py").resolve())],
                        "environment": {},
                    },
                },
                "tools": [
                    {
                        "name": "echo_context",
                        "title": "Echo context",
                        "description": "Echo one token.",
                        "access": "read",
                        "risk": "low",
                        "enabledByDefault": True,
                    }
                ],
                "policy": {},
            },
        }
    )
    manager.install_definition(definition, expected_revision=None)
    connection = manager.create_connection(
        AppConnection.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AppConnection",
                "metadata": {"name": "echo-account"},
                "spec": {
                    "appId": "echo-app",
                    "displayName": "Echo account",
                    "credentialRefs": {},
                    "enabledAgentIds": [],
                },
            }
        ),
        expected_revision=None,
    )
    manager.enable_connection(
        "echo-account",
        "writer",
        expected_revision=connection.revision,
    )
    app_snapshot = manager.snapshot_for_agent("writer")

    async def _discover() -> list[str]:
        build = McpRuntimeAdapter(secrets).build(app_snapshot.mcp)
        try:
            tools = await build.toolsets[0].get_tools_with_prefix()
            return [tool.name for tool in tools]
        finally:
            await build.toolsets[0].close()

    names = asyncio.run(_discover())

    assert names == ["app_echo_app_echo_account_echo_context"]
