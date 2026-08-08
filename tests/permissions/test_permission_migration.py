"""Legacy execution override migration and rollout Gate tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import compile_permission_snapshot, migrate_legacy_execution_permissions


def _agent(workspace: Path) -> AgentConfig:
    return AgentConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "worker"},
            "spec": {
                "displayName": "Worker",
                "workspace": str(workspace),
                "ownerPrincipalId": "local:owner",
                "privilegeLevel": "medium",
                "permissionOverrides": {
                    "filesystemAccess": "read_only",
                    "shellExec": "denied",
                    "networkAccess": "denied",
                    "toolAccess": "safe",
                    "canDelegate": False,
                },
            },
        }
    )


def _node(*, enforce_tool: bool = False) -> NodeConfig:
    permissions = {"rolloutModes": {"tool": "enforce"}} if enforce_tool else {}
    return NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {"displayName": "Node", "permissions": permissions},
        }
    )


def test_migration_preserves_non_execution_overrides_and_never_enables_rollout(tmp_path: Path) -> None:
    migrated = migrate_legacy_execution_permissions(_agent(tmp_path))
    snapshot = compile_permission_snapshot(node=_node(), agent=migrated)

    assert migrated.spec.permission_overrides.can_delegate is False
    assert migrated.spec.permission_overrides.filesystem_access is None
    assert migrated.spec.permissions.object_defaults["command"] == "deny"
    assert migrated.spec.permissions.object_defaults["network"] == "deny"
    assert migrated.spec.permissions.object_defaults["tool"] == "deny"
    assert migrated.spec.permissions.defaults["workspace"]["write"] == "deny"
    assert snapshot.legacy_override_fields == ()
    assert snapshot.rollout_for("tool") == "observe"


def test_unmigrated_execution_override_fails_closed_when_enforce_is_requested(tmp_path: Path) -> None:
    snapshot = compile_permission_snapshot(node=_node(enforce_tool=True), agent=_agent(tmp_path))

    with pytest.raises(PermissionError, match="legacy-permission-overrides-migration"):
        snapshot.assert_enforce_ready("tool")
