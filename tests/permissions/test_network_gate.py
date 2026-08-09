"""Managed network normalization, DNS/IP, and preset tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import authorize_network_url, compile_permission_snapshot
from openppx.runtime.tool_execution_context import ToolExecutionContext, activate_tool_execution_context
from openppx.tooling import registry


def _snapshot(
    preset: str,
    workspace: Path,
    *,
    agent_rules: list[dict[str, object]] | None = None,
):
    node = NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {
                "displayName": "Node",
                "enabledAgents": ["worker"],
                "permissions": {"rolloutModes": {"network": "enforce"}},
            },
        }
    )
    agent = AgentConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "worker"},
            "spec": {
                "displayName": "Worker",
                "workspace": str(workspace),
                "ownerPrincipalId": "local:owner",
                "privilegeLevel": preset,
                "permissions": {"rules": agent_rules or []},
            },
        }
    )
    return compile_permission_snapshot(node=node, agent=agent)


def _public_resolver(_host: str, _port: int) -> tuple[str, ...]:
    return ("93.184.216.34",)


def test_low_empty_allowlist_denies_public_web(tmp_path: Path) -> None:
    with pytest.raises(PermissionError, match="Network request is denied"):
        authorize_network_url(
            _snapshot("low", tmp_path),
            "https://example.com/docs",
            resolver=_public_resolver,
        )


def test_low_domain_allowlist_requires_every_network_action(tmp_path: Path) -> None:
    rules = [
        {
            "ruleId": "allow-docs",
            "effect": "allow",
            "object": "network",
            "actions": ["connect", "read"],
            "selector": {"kind": "network", "domains": ["docs.example.com"], "schemes": ["https"]},
            "constraints": {"kind": "network", "managedWebOnly": True, "readOnly": True},
        }
    ]
    snapshot = _snapshot("low", tmp_path, agent_rules=rules)

    target = authorize_network_url(
        snapshot,
        "https://DOCS.example.com./guide#section",
        resolver=_public_resolver,
    )

    assert target.host == "docs.example.com"
    assert target.url == "https://docs.example.com/guide"
    with pytest.raises(PermissionError):
        authorize_network_url(snapshot, "https://evil-example.com/", resolver=_public_resolver)


def test_medium_allows_public_but_denies_private_and_metadata_targets(tmp_path: Path) -> None:
    snapshot = _snapshot("medium", tmp_path)

    assert authorize_network_url(
        snapshot,
        "https://example.com/",
        resolver=_public_resolver,
    ).visibility == "public"
    with pytest.raises(PermissionError):
        authorize_network_url(
            snapshot,
            "http://internal.example/",
            resolver=lambda _host, _port: ("10.0.0.8",),
        )
    with pytest.raises(PermissionError):
        authorize_network_url(snapshot, "http://169.254.169.254/latest/meta-data")


def test_reviewed_imaps_endpoint_uses_the_same_network_permission_intersection(
    tmp_path: Path,
) -> None:
    snapshot = _snapshot(
        "low",
        tmp_path,
        agent_rules=[
            {
                "ruleId": "allow-qq-imaps-read",
                "effect": "allow",
                "object": "network",
                "actions": ["connect", "read"],
                "selector": {
                    "kind": "network",
                    "domains": ["imap.qq.com"],
                    "schemes": ["imaps"],
                    "ports": [993],
                },
                "constraints": {"kind": "network", "readOnly": True},
            }
        ],
    )

    target = authorize_network_url(
        snapshot,
        "imaps://IMAP.QQ.COM./",
        resolver=_public_resolver,
    )

    assert target.url == "imaps://imap.qq.com/"
    assert target.port == 993
    with pytest.raises(PermissionError):
        authorize_network_url(
            snapshot,
            "imaps://imap.163.com/",
            resolver=_public_resolver,
        )


def test_web_fetch_low_empty_allowlist_stops_before_http_io(tmp_path: Path, monkeypatch) -> None:
    snapshot = _snapshot("low", tmp_path)
    context = ToolExecutionContext.for_agent(
        agent_id="worker",
        workspace_root=tmp_path,
        permission_snapshot=snapshot,
    )

    def unexpected_urlopen(*_args, **_kwargs):
        raise AssertionError("HTTP I/O must not start")

    monkeypatch.setattr(registry, "urlopen", unexpected_urlopen)
    with activate_tool_execution_context(context):
        result = registry.web_fetch("https://8.8.8.8/")

    assert "Network request is denied" in result
