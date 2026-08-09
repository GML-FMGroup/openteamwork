"""Current-snapshot authority tests for long-lived Agent runtimes."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import PermissionSnapshotAuthority, compile_permission_snapshot
from openppx.runtime.tool_execution_context import (
    ToolExecutionContext,
    bind_tool_callable,
    current_tool_execution_context,
)
from openppx.tooling.registry import exec_command, process_session, write_file


def _snapshot(
    workspace: Path,
    *,
    preset: str = "medium",
    workspace_read: str = "allow",
    workspace_rollout: str | None = None,
    rollout_modes: dict[str, str] | None = None,
):
    node = NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {"displayName": "Node", "enabledAgents": ["worker"]},
        }
    )
    effective_rollout = dict(rollout_modes or {})
    if workspace_rollout is not None:
        effective_rollout["workspace"] = workspace_rollout
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
                "permissions": {
                    "defaults": {"workspace": {"read": workspace_read}},
                    "rolloutModes": effective_rollout,
                },
            },
        }
    )
    return compile_permission_snapshot(node=node, agent=agent)


def test_bound_tool_pins_the_current_permission_revision_per_invocation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = _snapshot(workspace)
    tightened = _snapshot(workspace, workspace_read="deny")
    current = [baseline]
    authority = PermissionSnapshotAuthority(baseline, provider=lambda: current[0])
    context = ToolExecutionContext.for_agent(
        agent_id="worker",
        workspace_root=workspace,
        permission_authority=authority,
    )

    def active_revision() -> str:
        active = current_tool_execution_context()
        assert active is not None
        assert active.permission_authority is None
        assert active.permission_snapshot is not None
        return active.permission_snapshot.revision

    bound = bind_tool_callable(active_revision, context)
    assert bound() == baseline.revision

    current[0] = tightened
    assert bound() == tightened.revision


def test_authority_rejects_identity_boundary_changes_and_provider_failure(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    other_workspace = tmp_path / "other"
    workspace.mkdir()
    other_workspace.mkdir()
    baseline = _snapshot(workspace)

    changed_workspace = PermissionSnapshotAuthority(
        baseline,
        provider=lambda: _snapshot(other_workspace),
    )
    with pytest.raises(PermissionError, match="Workspace changed"):
        changed_workspace.current()

    def unavailable():
        raise OSError("config store unavailable")

    unavailable_authority = PermissionSnapshotAuthority(baseline, provider=unavailable)
    with pytest.raises(PermissionError, match="snapshot is unavailable"):
        unavailable_authority.current()

    invalid_authority = PermissionSnapshotAuthority(
        baseline,
        provider=lambda: object(),  # type: ignore[arg-type,return-value]
    )
    with pytest.raises(PermissionError, match="snapshot is invalid"):
        invalid_authority.current()


def test_delegated_authority_fails_closed_when_permission_revision_changes(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = _snapshot(workspace)
    changed = _snapshot(workspace, workspace_read="deny")
    authority = PermissionSnapshotAuthority(
        baseline,
        provider=lambda: changed,
        required_revision=baseline.revision,
    )

    with pytest.raises(PermissionError, match="delegated permission ceiling"):
        authority.current()


def test_direct_file_tool_rechecks_tightened_workspace_permissions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = _snapshot(workspace)
    tightened = _snapshot(
        workspace,
        preset="low",
        workspace_rollout="enforce",
    )
    context = ToolExecutionContext.for_agent(
        agent_id="worker",
        workspace_root=workspace,
        permission_authority=PermissionSnapshotAuthority(
            baseline,
            provider=lambda: tightened,
        ),
    )

    result = bind_tool_callable(write_file, context)("blocked.txt", "must not be written")

    assert "denied by Agent permissions" in result
    assert not (workspace / "blocked.txt").exists()


def test_direct_command_tool_rechecks_revocation_before_process_creation(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = _snapshot(workspace)
    tightened = _snapshot(
        workspace,
        preset="low",
        rollout_modes={"command": "enforce"},
    )
    context = ToolExecutionContext.for_agent(
        agent_id="worker",
        workspace_root=workspace,
        permission_authority=PermissionSnapshotAuthority(
            baseline,
            provider=lambda: tightened,
        ),
    )

    with mock.patch("openppx.tooling.registry._run_limited_foreground_process") as run_process:
        result = bind_tool_callable(exec_command, context)("python -c 'print(1)'")

    assert "denied by Agent permissions" in result
    run_process.assert_not_called()


def test_process_follow_up_rechecks_current_policy_and_provenance(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    baseline = _snapshot(workspace)
    tightened = _snapshot(
        workspace,
        preset="low",
        rollout_modes={"process": "enforce"},
    )
    context = ToolExecutionContext.for_agent(
        agent_id="worker",
        workspace_root=workspace,
        permission_authority=PermissionSnapshotAuthority(
            baseline,
            provider=lambda: tightened,
        ),
    )
    manager = mock.Mock()
    manager.describe_session.return_value = SimpleNamespace(
        pid=123,
        created_by_agent_id="worker",
        created_by_task_id="task-before-revocation",
        created_by_allowed_command=True,
        protected=False,
        system_process=False,
    )

    with mock.patch(
        "openppx.tooling.registry.get_process_session_manager",
        return_value=manager,
    ):
        result = bind_tool_callable(process_session, context)(
            action="kill",
            session_id="session-1",
        )

    assert "denied by Agent permissions" in result
    manager.kill_session.assert_not_called()
