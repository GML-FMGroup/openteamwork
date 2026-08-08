"""Compilation and pure evaluation tests for static permission snapshots."""

from __future__ import annotations

from copy import deepcopy

import pytest
from pydantic import ValidationError

from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import (
    PermissionRequest,
    combine_permission_decisions,
    compile_permission_snapshot,
    diff_permission_snapshots,
    evaluate_permission,
    load_permission_templates,
)


def node_document() -> dict[str, object]:
    """Return one NodeConfig fixture with no implicit host path grants."""

    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "NodeConfig",
        "metadata": {"name": "local-node"},
        "spec": {
            "displayName": "Local Node",
            "enabledAgents": ["worker"],
        },
    }


def agent_document(*, preset: str = "low") -> dict[str, object]:
    """Return one AgentConfig fixture using a selected static permission preset."""

    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "AgentConfig",
        "metadata": {"name": "worker"},
        "spec": {
            "displayName": "Worker",
            "workspace": "workspace/worker",
            "ownerPrincipalId": "local:owner",
            "privilegeLevel": preset,
        },
    }


def _compile(
    *,
    preset: str = "low",
    node_raw: dict[str, object] | None = None,
    agent_raw: dict[str, object] | None = None,
):
    """Compile one isolated fixture snapshot."""

    return compile_permission_snapshot(
        node=NodeConfig.model_validate(node_raw or node_document()),
        agent=AgentConfig.model_validate(agent_raw or agent_document(preset=preset)),
        source_revisions={"node/local-node": "node-r1", "agent/worker": "agent-r1"},
    )


def _workspace_request(snapshot, *, action: str, path: str) -> PermissionRequest:
    """Build one trusted Workspace request for the compiled Agent."""

    return PermissionRequest.model_validate(
        {
            "requestId": f"request-{action}",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": "worker", "taskId": "task-1"},
            "object": "workspace",
            "action": action,
            "resource": {"kind": "workspace_path", "path": path},
        }
    )


def test_packaged_templates_are_complete_and_versioned() -> None:
    templates = load_permission_templates()

    assert set(templates) == {"low", "medium", "high", "root"}
    assert {template.schema_version for template in templates.values()} == {"openppx.permissions/v1alpha1"}


def test_packaged_template_callers_cannot_mutate_the_cached_catalog() -> None:
    first = load_permission_templates()
    first["low"].object_defaults["tool"] = "allow"

    second = load_permission_templates()

    assert second["low"].object_defaults["tool"] == "deny"


@pytest.mark.parametrize(
    ("preset", "workspace_read", "workspace_write", "external_read", "network_connect", "tool_invoke"),
    [
        ("low", "allow", "deny", "deny", "deny", "deny"),
        ("medium", "allow", "allow", "deny", "allow", "allow"),
        ("high", "allow", "allow", "allow", "allow", "allow"),
        ("root", "allow", "allow", "allow", "allow", "allow"),
    ],
)
def test_snapshot_expands_the_reviewed_matrix_defaults(
    preset: str,
    workspace_read: str,
    workspace_write: str,
    external_read: str,
    network_connect: str,
    tool_invoke: str,
) -> None:
    snapshot = _compile(preset=preset)

    assert snapshot.rollout_for("workspace") == "observe"
    assert snapshot.default_for("workspace", "read") == workspace_read
    assert snapshot.default_for("workspace", "write") == workspace_write
    assert snapshot.default_for("external_path", "read") == external_read
    assert snapshot.default_for("network", "connect") == network_connect
    assert snapshot.default_for("tool", "invoke") == tool_invoke
    assert len(snapshot.defaults) == 33


@pytest.mark.parametrize("mode", ["observe", "enforce"])
def test_agent_global_rollout_mode_overrides_every_object(mode: str) -> None:
    node_raw = node_document()
    node_raw["spec"]["permissions"] = {  # type: ignore[index]
        "rolloutModes": {"workspace": "enforce", "tool": "enforce"},
    }
    agent_raw = agent_document()
    agent_raw["spec"]["permissions"] = {  # type: ignore[index]
        "rolloutMode": mode,
        "rolloutModes": {"network": "enforce" if mode == "observe" else "observe"},
    }

    snapshot = _compile(node_raw=node_raw, agent_raw=agent_raw)

    assert {
        item.object: item.mode for item in snapshot.rollout_modes
    } == {
        "workspace": mode,
        "external_path": mode,
        "command": mode,
        "process": mode,
        "network": mode,
        "tool": mode,
    }


def test_unset_agent_global_rollout_mode_preserves_per_object_precedence() -> None:
    node_raw = node_document()
    node_raw["spec"]["permissions"] = {  # type: ignore[index]
        "rolloutModes": {"network": "enforce", "tool": "enforce"},
    }
    agent_raw = agent_document()
    agent_raw["spec"]["permissions"] = {  # type: ignore[index]
        "rolloutModes": {"network": "observe"},
    }

    snapshot = _compile(node_raw=node_raw, agent_raw=agent_raw)

    assert snapshot.rollout_for("network") == "observe"
    assert snapshot.rollout_for("tool") == "enforce"
    assert snapshot.rollout_for("workspace") == "observe"


def test_global_and_equivalent_per_object_rollout_have_same_revision() -> None:
    global_raw = agent_document()
    global_raw["spec"]["permissions"] = {"rolloutMode": "enforce"}  # type: ignore[index]
    per_object_raw = agent_document()
    per_object_raw["spec"]["permissions"] = {  # type: ignore[index]
        "rolloutModes": {
            "workspace": "enforce",
            "external_path": "enforce",
            "command": "enforce",
            "process": "enforce",
            "network": "enforce",
            "tool": "enforce",
        }
    }

    assert _compile(agent_raw=global_raw).revision == _compile(agent_raw=per_object_raw).revision


def test_revision_depends_on_effective_permissions_not_source_revision_or_display_name() -> None:
    node = NodeConfig.model_validate(node_document())
    agent_raw = agent_document(preset="medium")
    first = compile_permission_snapshot(
        node=node,
        agent=AgentConfig.model_validate(agent_raw),
        source_revisions={"node/local-node": "node-r1", "agent/worker": "agent-r1"},
    )
    renamed = deepcopy(agent_raw)
    renamed["spec"]["displayName"] = "Renamed Worker"  # type: ignore[index]
    second = compile_permission_snapshot(
        node=node,
        agent=AgentConfig.model_validate(renamed),
        source_revisions={"node/local-node": "node-r2", "agent/worker": "agent-r2"},
    )

    assert first.revision == second.revision
    assert first.sources != second.sources

    moved = deepcopy(agent_raw)
    moved["spec"]["workspace"] = "workspace/moved"  # type: ignore[index]
    assert _compile(agent_raw=moved, preset="medium").revision != first.revision


def test_agent_overlay_adds_narrow_write_rules_without_changing_low_default() -> None:
    raw = agent_document()
    raw["spec"]["permissions"] = {  # type: ignore[index]
        "rules": [
            {
                "ruleId": "allow-drafts-write",
                "effect": "allow",
                "object": "workspace",
                "actions": ["create", "write", "edit"],
                "selector": {"kind": "workspace_path", "patterns": ["drafts/**"]},
            },
            {
                "ruleId": "deny-private-drafts",
                "effect": "deny",
                "object": "workspace",
                "actions": ["write"],
                "selector": {"kind": "workspace_path", "patterns": ["drafts/private/**"]},
            },
        ]
    }
    snapshot = _compile(agent_raw=raw)

    assert snapshot.default_for("workspace", "write") == "deny"
    allowed = evaluate_permission(snapshot, _workspace_request(snapshot, action="write", path="drafts/answer.md"))
    denied = evaluate_permission(snapshot, _workspace_request(snapshot, action="write", path="drafts/private/key.txt"))

    assert allowed.outcome == "allow"
    assert allowed.reason_code == "explicit_allow"
    assert denied.outcome == "deny"
    assert denied.reason_code == "explicit_deny"
    assert set(denied.matched_rule_ids) == {
        "agent/worker:allow-drafts-write.write",
        "agent/worker:deny-private-drafts",
    }


def test_permission_diff_detects_rule_semantics_changed_under_the_same_id() -> None:
    first_raw = agent_document()
    first_raw["spec"]["permissions"] = {  # type: ignore[index]
        "rules": [
            {
                "ruleId": "allow-drafts-read",
                "effect": "allow",
                "object": "workspace",
                "actions": ["read"],
                "selector": {"kind": "workspace_path", "patterns": ["drafts/**"]},
            }
        ]
    }
    second_raw = deepcopy(first_raw)
    second_raw["spec"]["permissions"]["rules"][0]["selector"]["patterns"] = ["published/**"]  # type: ignore[index]

    changes = diff_permission_snapshots(
        _compile(agent_raw=first_raw),
        _compile(agent_raw=second_raw),
    )

    assert [(change.change_kind, change.rule_id) for change in changes] == [
        ("rule_changed", "agent/worker:allow-drafts-read")
    ]


def test_medium_node_safe_roots_compile_to_read_rules_and_clear_the_gate() -> None:
    node_raw = node_document()
    node_raw["spec"]["permissions"] = {  # type: ignore[index]
        "safeExternalReadRoots": ["/opt/openppx/reference"]
    }

    snapshot = _compile(preset="medium", node_raw=node_raw)

    assert "medium-safe-external-read-roots" not in snapshot.blocking_gates
    safe_rules = [rule for rule in snapshot.rules if rule.source_rule_id == "medium-safe-external-read-roots"]
    assert {rule.action for rule in safe_rules} == {"list", "read", "search"}
    assert all(rule.effect == "allow" and rule.locked is False for rule in safe_rules)


def test_node_hard_deny_is_locked_and_wins_over_agent_allow() -> None:
    node_raw = node_document()
    node_raw["spec"]["permissions"] = {  # type: ignore[index]
        "hardRules": [
            {
                "ruleId": "deny-control-tool",
                "effect": "deny",
                "object": "tool",
                "actions": ["invoke"],
                "selector": {"kind": "tool", "toolIds": ["openppx.control"]},
            }
        ]
    }
    agent_raw = agent_document()
    agent_raw["spec"]["permissions"] = {  # type: ignore[index]
        "rules": [
            {
                "ruleId": "allow-control-tool",
                "effect": "allow",
                "object": "tool",
                "actions": ["invoke"],
                "selector": {"kind": "tool", "toolIds": ["openppx.control"]},
            }
        ]
    }
    snapshot = _compile(node_raw=node_raw, agent_raw=agent_raw)
    request = PermissionRequest.model_validate(
        {
            "requestId": "request-tool",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": "worker"},
            "object": "tool",
            "action": "invoke",
            "resource": {
                "kind": "tool",
                "toolId": "openppx.control",
                "operation": "update",
                "source": "builtin",
            },
        }
    )

    decision = evaluate_permission(snapshot, request)

    hard_rule = next(rule for rule in snapshot.rules if rule.source_rule_id == "deny-control-tool")
    assert hard_rule.locked is True
    assert decision.outcome == "deny"
    assert decision.reason_code == "explicit_deny"


def test_medium_locked_workspace_rule_uses_trusted_owner_facts() -> None:
    snapshot = _compile(preset="medium")
    request = PermissionRequest.model_validate(
        {
            "requestId": "request-external-read",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": "worker"},
            "object": "external_path",
            "action": "read",
            "resource": {
                "kind": "external_path",
                "path": "/srv/agents/root/workspace/secret.txt",
                "ownerAgentId": "root-agent",
                "ownerPrivilegeLevel": "root",
            },
        }
    )

    decision = evaluate_permission(snapshot, request)

    assert decision.outcome == "deny"
    assert decision.reason_code == "explicit_deny"
    assert decision.matched_rule_ids == ("permission-preset/medium:deny-high-root-workspace-read.read",)


def test_action_intersection_uses_the_minimum_permission() -> None:
    snapshot = _compile()
    workspace = evaluate_permission(snapshot, _workspace_request(snapshot, action="read", path="faq.md"))
    tool_request = PermissionRequest.model_validate(
        {
            "requestId": "request-tool",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": "worker"},
            "object": "tool",
            "action": "invoke",
            "resource": {
                "kind": "tool",
                "toolId": "builtin.read_file",
                "operation": "read",
                "source": "builtin",
            },
        }
    )
    tool = evaluate_permission(snapshot, tool_request)

    combined = combine_permission_decisions((workspace, tool))

    assert workspace.outcome == "allow"
    assert tool.outcome == "deny"
    assert combined.outcome == "deny"
    assert combined.reason_code == "intersection_denied"


@pytest.mark.parametrize(
    ("object_kind", "constraints"),
    [
        (
            "network",
            {"kind": "network", "maxResponseBytes": 1024},
        ),
        (
            "tool",
            {"kind": "tool", "parameterProfile": "reviewed-input-v1"},
        ),
    ],
)
def test_unimplemented_runtime_constraints_are_rejected_by_schema(
    object_kind: str,
    constraints: dict[str, object],
) -> None:
    raw = agent_document(preset="medium")
    raw["spec"]["permissions"] = {  # type: ignore[index]
        "rolloutModes": {object_kind: "enforce"},
        "rules": [
            {
                "ruleId": "allow-with-runtime-constraint",
                "effect": "allow",
                "object": object_kind,
                "actions": ["connect" if object_kind == "network" else "invoke"],
                "selector": (
                    {"kind": "network", "domains": ["example.com"]}
                    if object_kind == "network"
                    else {"kind": "tool", "toolIds": ["openppx.builtin.read_file"]}
                ),
                "constraints": constraints,
            }
        ],
    }
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(raw)


def test_deny_rule_cannot_declare_allow_execution_constraints() -> None:
    raw = agent_document()
    raw["spec"]["permissions"] = {  # type: ignore[index]
        "rules": [
            {
                "ruleId": "invalid-deny-constraint",
                "effect": "deny",
                "object": "workspace",
                "actions": ["read"],
                "selector": {"kind": "workspace_path", "patterns": ["private/**"]},
                "constraints": {"kind": "path", "maxBytes": 1024},
            }
        ]
    }

    with pytest.raises(ValidationError, match="deny rules cannot declare"):
        AgentConfig.model_validate(raw)
