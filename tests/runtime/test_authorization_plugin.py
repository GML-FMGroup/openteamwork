"""Tests for the Google ADK-native OpenPPX authorization Plugin."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import PermissionSnapshotAuthority, compile_permission_snapshot
from openppx.runtime.authorization_plugin import OpenPpxAuthorizationPlugin
from openppx.tooling.registry import invoke_skill_api


class _RecordingAudit:
    def __init__(self) -> None:
        self.records: list[tuple[object, object, str]] = []

    def record(self, request, decision, *, rollout_mode: str) -> None:
        self.records.append((request, decision, rollout_mode))


def _snapshot(
    *,
    preset: str = "low",
    tool_rollout: str | None = None,
    agent_rollout: str | None = None,
    denied_network_origin: str | None = None,
    denied_network_actions: tuple[str, ...] = ("connect",),
):
    node = NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {"displayName": "Node", "enabledAgents": ["worker"]},
        }
    )
    agent = AgentConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "worker"},
            "spec": {
                "displayName": "Worker",
                "workspace": "workspace/worker",
                "ownerPrincipalId": "local:owner",
                "privilegeLevel": preset,
                "permissions": {
                    **({"rolloutMode": agent_rollout} if agent_rollout is not None else {}),
                    "rolloutModes": {
                        **({"tool": tool_rollout} if tool_rollout is not None else {}),
                        **({"network": "enforce"} if denied_network_origin is not None else {}),
                    },
                    "rules": (
                        [
                            {
                                "ruleId": "deny-current-mcp-origin",
                                "effect": "deny",
                                "object": "network",
                                "actions": list(denied_network_actions),
                                "selector": {
                                    "kind": "network",
                                    "domains": [denied_network_origin],
                                },
                            }
                        ]
                        if denied_network_origin is not None
                        else []
                    ),
                },
            },
        }
    )
    return compile_permission_snapshot(node=node, agent=agent)


def _context():
    return SimpleNamespace(
        invocation_id="invocation-1",
        function_call_id="function-1",
        run_id="",
        session=SimpleNamespace(id="session-1"),
        custom_metadata={},
    )


def external_tool() -> dict[str, object]:
    return {"ok": True}


def mcp_docs_search() -> dict[str, object]:
    return {"ok": True}


def test_non_root_observe_mode_enforces_the_mandatory_tool_floor() -> None:
    audit = _RecordingAudit()
    plugin = OpenPpxAuthorizationPlugin(_snapshot(), audit=audit, rollout_mode="observe")
    context = _context()

    result = asyncio.run(
        plugin.before_tool_callback(tool=external_tool, tool_args={}, tool_context=context)
    )

    assert result is not None
    assert result["error"]["code"] == "permission_denied"
    assert result["error"]["terminal"] is True
    assert result["error"]["allowedNextActions"] == [
        "Use an allowed tool for the current Agent privilege.",
        "Explain the permission limit to the user.",
    ]
    assert len(audit.records) == 1
    request, decision, mode = audit.records[0]
    assert request.resource.tool_id == "openppx.extension.external_tool"
    assert decision.outcome == "deny"
    assert mode == "enforce"
    assert context.custom_metadata["openppx.permission"]["outcome"] == "deny"


def test_root_can_explicitly_keep_tool_rollout_in_observe_mode() -> None:
    audit = _RecordingAudit()
    plugin = OpenPpxAuthorizationPlugin(
        _snapshot(preset="root", agent_rollout="observe"),
        audit=audit,
    )

    result = asyncio.run(
        plugin.before_tool_callback(tool=external_tool, tool_args={}, tool_context=_context())
    )

    assert result is None
    assert audit.records[0][2] == "observe"


def test_non_root_plugin_blocks_unsandboxed_skill_api_execution() -> None:
    plugin = OpenPpxAuthorizationPlugin(
        _snapshot(preset="high", agent_rollout="observe"),
        audit=_RecordingAudit(),
    )

    result = asyncio.run(
        plugin.before_tool_callback(
            tool=invoke_skill_api,
            tool_args={"skill_name": "unsafe", "api_name": "read_node"},
            tool_context=_context(),
        )
    )

    assert result is not None
    assert result["error"]["reasonCode"] == "mandatory_non_root_skill_api_boundary"


def test_enforce_mode_returns_an_adk_short_circuit_result_for_denied_tool() -> None:
    plugin = OpenPpxAuthorizationPlugin(_snapshot(), audit=_RecordingAudit(), rollout_mode="enforce")

    result = asyncio.run(
        plugin.before_tool_callback(tool=external_tool, tool_args={}, tool_context=_context())
    )

    assert result is not None
    assert result["ok"] is False
    assert result["error"]["code"] == "permission_denied"


def test_agent_global_enforce_blocks_a_denied_tool_without_plugin_override() -> None:
    snapshot = _snapshot(agent_rollout="enforce")
    plugin = OpenPpxAuthorizationPlugin(snapshot, audit=_RecordingAudit())

    result = asyncio.run(
        plugin.before_tool_callback(tool=external_tool, tool_args={}, tool_context=_context())
    )

    assert snapshot.rollout_for("tool") == "enforce"
    assert result is not None
    assert result["ok"] is False
    assert result["error"]["code"] == "permission_denied"


def test_enforce_mode_fails_closed_when_permission_audit_is_unavailable() -> None:
    class _BrokenAudit:
        def record(self, request, decision, *, rollout_mode: str) -> None:
            raise OSError("unavailable")

    plugin = OpenPpxAuthorizationPlugin(
        _snapshot(preset="medium"),
        audit=_BrokenAudit(),
        rollout_mode="enforce",
    )

    result = asyncio.run(
        plugin.before_tool_callback(tool=external_tool, tool_args={}, tool_context=_context())
    )

    assert result is not None
    assert result["error"]["reasonCode"] == "permission_audit_unavailable"


def test_long_lived_plugin_uses_current_permissions_before_each_tool_call() -> None:
    baseline = _snapshot(preset="medium")
    current = _snapshot(preset="low", tool_rollout="enforce")
    plugin = OpenPpxAuthorizationPlugin(
        baseline,
        audit=_RecordingAudit(),
        authority=PermissionSnapshotAuthority(baseline, provider=lambda: current),
    )

    result = asyncio.run(
        plugin.before_tool_callback(tool=external_tool, tool_args={}, tool_context=_context())
    )

    assert result is not None
    assert result["error"]["permissionRevision"] == current.revision


def test_plugin_fails_closed_when_current_permission_snapshot_is_unavailable() -> None:
    baseline = _snapshot(preset="medium")

    def unavailable():
        raise OSError("config unavailable")

    plugin = OpenPpxAuthorizationPlugin(
        baseline,
        audit=_RecordingAudit(),
        authority=PermissionSnapshotAuthority(baseline, provider=unavailable),
    )

    result = asyncio.run(
        plugin.before_tool_callback(tool=external_tool, tool_args={}, tool_context=_context())
    )

    assert result is not None
    assert result["error"]["reasonCode"] == "permission_snapshot_unavailable"


def test_remote_mcp_origin_is_reauthorized_with_current_network_permissions() -> None:
    baseline = _snapshot(preset="medium")
    current = _snapshot(
        preset="medium",
        denied_network_origin="blocked.example",
    )
    plugin = OpenPpxAuthorizationPlugin(
        baseline,
        audit=_RecordingAudit(),
        authority=PermissionSnapshotAuthority(baseline, provider=lambda: current),
        fixed_network_policies={"mcp_docs": ("https://blocked.example", "write")},
    )

    result = asyncio.run(
        plugin.before_tool_callback(tool=mcp_docs_search, tool_args={}, tool_context=_context())
    )

    assert result is not None
    assert result["error"]["reasonCode"] == "network_intersection_denied"
    assert result["error"]["permissionRevision"] == current.revision


def test_read_only_remote_mcp_does_not_require_network_write_permission() -> None:
    snapshot = _snapshot(
        preset="medium",
        denied_network_origin="8.8.8.8",
        denied_network_actions=("write", "upload"),
    )
    plugin = OpenPpxAuthorizationPlugin(
        snapshot,
        audit=_RecordingAudit(),
        fixed_network_policies={"mcp_docs": ("https://8.8.8.8", "read")},
    )

    result = asyncio.run(
        plugin.before_tool_callback(tool=mcp_docs_search, tool_args={}, tool_context=_context())
    )

    assert result is None
