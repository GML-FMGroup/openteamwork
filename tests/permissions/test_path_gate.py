"""Path Gate and permission-derived sandbox boundary tests."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import AgentWorkspaceBoundary, authorize_path, compile_permission_snapshot
from openppx.runtime.sandbox import NetworkMode, PathAccessMode, derive_sandbox_permission_profile
from openppx.runtime.sandbox.egress_policy import write_egress_proxy_policy


def _snapshot(
    preset: str,
    workspace: Path,
    *,
    node_permissions: dict[str, object] | None = None,
    boundaries: tuple[AgentWorkspaceBoundary, ...] = (),
    rollout_mode: str | None = None,
):
    effective_node_permissions = {
        "rolloutModes": {"workspace": "enforce", "external_path": "enforce"},
        **(node_permissions or {}),
    }
    node = NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {
                "displayName": "Node",
                "enabledAgents": ["worker"],
                "permissions": effective_node_permissions,
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
                "permissions": {
                    **({"rolloutMode": rollout_mode} if rollout_mode is not None else {}),
                },
            },
        }
    )
    return compile_permission_snapshot(node=node, agent=agent, agent_workspaces=boundaries)


def test_low_can_read_but_cannot_write_its_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "low"
    workspace.mkdir()
    target = workspace / "answer.txt"
    target.write_text("answer", encoding="utf-8")
    snapshot = _snapshot("low", workspace)

    assert authorize_path(snapshot, workspace_root=workspace, raw_path=target, action="read").path == target
    with pytest.raises(PermissionError, match="Path action 'write'"):
        authorize_path(snapshot, workspace_root=workspace, raw_path=target, action="write")


def test_medium_safe_root_never_overrides_high_workspace_deny(tmp_path: Path) -> None:
    workspace = tmp_path / "medium"
    high_workspace = tmp_path / "shared" / "high"
    workspace.mkdir()
    high_workspace.mkdir(parents=True)
    secret = high_workspace / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    snapshot = _snapshot(
        "medium",
        workspace,
        node_permissions={"safeExternalReadRoots": [str(tmp_path / "shared")]},
        boundaries=(
            AgentWorkspaceBoundary(
                agent_id="high-agent",
                privilege_level="high",
                workspace=str(high_workspace),
            ),
        ),
    )

    with pytest.raises(PermissionError, match="explicit_deny|mandatory_other_agent_workspace_boundary"):
        authorize_path(snapshot, workspace_root=workspace, raw_path=secret, action="read")


def test_symlink_is_classified_by_its_canonical_external_target(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()
    secret = external / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (workspace / "shortcut").symlink_to(secret)

    with pytest.raises(PermissionError):
        authorize_path(
            _snapshot("medium", workspace),
            workspace_root=workspace,
            raw_path="shortcut",
            action="read",
        )


@pytest.mark.skipif(not hasattr(os, "link"), reason="hard links are unavailable")
def test_non_root_presets_reject_hardlinked_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    source = workspace / "source.txt"
    alias = workspace / "alias.txt"
    source.write_text("shared inode", encoding="utf-8")
    os.link(source, alias)

    with pytest.raises(PermissionError, match="Hard-linked"):
        authorize_path(
            _snapshot("medium", workspace),
            workspace_root=workspace,
            raw_path=alias,
            action="read",
        )


def test_authorized_file_descriptor_rejects_inode_replacement(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    target = workspace / "answer.txt"
    replacement = workspace / "replacement.txt"
    target.write_text("original", encoding="utf-8")
    replacement.write_text("replacement", encoding="utf-8")
    authorized = authorize_path(
        _snapshot("medium", workspace),
        workspace_root=workspace,
        raw_path=target,
        action="read",
    )
    os.replace(replacement, target)

    with pytest.raises(PermissionError, match="identity changed"):
        authorized.open_fd(os.O_RDONLY)


@pytest.mark.parametrize(
    ("preset", "workspace_access", "network_mode"),
    [
        ("low", PathAccessMode.READ, NetworkMode.DISABLED),
        ("medium", PathAccessMode.WRITE, NetworkMode.DISABLED),
        ("high", PathAccessMode.WRITE, NetworkMode.DISABLED),
        ("root", PathAccessMode.WRITE, NetworkMode.ENABLED),
    ],
)
def test_sandbox_profile_is_derived_from_the_permission_snapshot(
    tmp_path: Path,
    preset: str,
    workspace_access: PathAccessMode,
    network_mode: NetworkMode,
) -> None:
    workspace = tmp_path / preset
    workspace.mkdir()

    profile = derive_sandbox_permission_profile(_snapshot(preset, workspace), workspace_root=workspace)
    workspace_grants = (*profile.filesystem.readable_roots, *profile.filesystem.writable_roots)

    assert len([grant for grant in workspace_grants if grant.logical_name == "workspace"]) == 1
    assert next(grant.access for grant in workspace_grants if grant.logical_name == "workspace") == workspace_access
    assert profile.network.mode == network_mode


@pytest.mark.parametrize("preset", ["medium", "high"])
def test_configured_code_egress_proxy_keeps_proxy_only_sandbox_network(
    tmp_path: Path,
    preset: str,
) -> None:
    """An explicitly configured reviewed proxy preserves the existing network path."""

    workspace = tmp_path / preset
    policy_directory = tmp_path / "egress-policies"
    workspace.mkdir()
    policy_directory.mkdir(mode=0o700)
    snapshot = _snapshot(
        preset,
        workspace,
        node_permissions={
            "codeEgressProxy": {
                "url": "http://openppx-egress-proxy:3128",
                "dockerNetwork": "openppx-egress-internal",
                "policyDirectory": str(policy_directory),
            }
        },
    )
    write_egress_proxy_policy(snapshot, policy_directory=policy_directory)

    profile = derive_sandbox_permission_profile(snapshot, workspace_root=workspace)

    assert profile.network.mode == NetworkMode.PROXY_ONLY
    assert profile.network.lock == NetworkMode.PROXY_ONLY


def test_medium_safe_root_becomes_a_readonly_sandbox_grant(tmp_path: Path) -> None:
    workspace = tmp_path / "medium"
    safe_root = tmp_path / "reference"
    workspace.mkdir()
    safe_root.mkdir()
    profile = derive_sandbox_permission_profile(
        _snapshot(
            "medium",
            workspace,
            node_permissions={"safeExternalReadRoots": [str(safe_root)]},
        ),
        workspace_root=workspace,
    )

    assert any(
        grant.host_path == safe_root and grant.access == PathAccessMode.READ
        for grant in profile.filesystem.readable_roots
    )


def test_high_reads_external_files_but_cannot_mutate_node_protected_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "high"
    protected = tmp_path / "protected"
    workspace.mkdir()
    protected.mkdir()
    target = protected / "node.json"
    target.write_text("{}", encoding="utf-8")
    snapshot = _snapshot(
        "high",
        workspace,
        node_permissions={"highProtectedWriteRoots": [str(protected)]},
    )

    assert authorize_path(
        snapshot,
        workspace_root=workspace,
        raw_path=target,
        action="read",
    ).path == target
    with pytest.raises(PermissionError, match="explicit_deny"):
        authorize_path(
            snapshot,
            workspace_root=workspace,
            raw_path=target,
            action="write",
        )


def test_root_can_read_and_write_external_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "root"
    external = tmp_path / "external"
    workspace.mkdir()
    external.mkdir()
    target = external / "system.conf"
    target.write_text("value", encoding="utf-8")
    snapshot = _snapshot("root", workspace)

    assert authorize_path(snapshot, workspace_root=workspace, raw_path=target, action="read").path == target
    assert authorize_path(snapshot, workspace_root=workspace, raw_path=target, action="write").path == target


def test_non_root_observe_cannot_read_node_data_outside_its_nested_workspace(
    tmp_path: Path,
) -> None:
    node_root = tmp_path / "node"
    workspace = node_root / "users" / "owner" / "agents" / "worker" / "workspace"
    database = node_root / "database" / "sessions.db"
    workspace.mkdir(parents=True)
    database.parent.mkdir(parents=True)
    database.write_text("private", encoding="utf-8")
    own_file = workspace / "notes.txt"
    own_file.write_text("allowed", encoding="utf-8")
    snapshot = _snapshot("high", workspace, rollout_mode="observe")

    assert authorize_path(
        snapshot,
        workspace_root=workspace,
        raw_path=own_file,
        action="read",
        protected_roots=(node_root,),
    ).path == own_file
    with pytest.raises(PermissionError, match="Node data"):
        authorize_path(
            snapshot,
            workspace_root=workspace,
            raw_path=database,
            action="read",
            protected_roots=(node_root,),
        )


def test_non_root_observe_cannot_read_another_agent_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "worker"
    other_workspace = tmp_path / "other"
    workspace.mkdir()
    other_workspace.mkdir()
    secret = other_workspace / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    snapshot = _snapshot(
        "high",
        workspace,
        boundaries=(AgentWorkspaceBoundary(agent_id="other", privilege_level="low", workspace=str(other_workspace)),),
        rollout_mode="observe",
    )

    with pytest.raises(PermissionError, match="denied"):
        authorize_path(
            snapshot,
            workspace_root=workspace,
            raw_path=secret,
            action="read",
        )


def test_non_root_workspace_equal_to_node_root_still_cannot_read_node_data(
    tmp_path: Path,
) -> None:
    node_root = tmp_path / "node"
    database = node_root / "database" / "sessions.db"
    database.parent.mkdir(parents=True)
    database.write_text("private", encoding="utf-8")
    snapshot = _snapshot("high", node_root, rollout_mode="observe")

    with pytest.raises(PermissionError, match="Node data"):
        authorize_path(
            snapshot,
            workspace_root=node_root,
            raw_path=database,
            action="read",
            protected_roots=(node_root,),
        )


def test_other_agent_workspace_takes_precedence_over_overlapping_source_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    other_workspace = workspace / "nested-other"
    other_workspace.mkdir(parents=True)
    secret = other_workspace / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    snapshot = _snapshot(
        "high",
        workspace,
        boundaries=(
            AgentWorkspaceBoundary(
                agent_id="other",
                privilege_level="low",
                workspace=str(other_workspace),
            ),
        ),
        rollout_mode="observe",
    )

    with pytest.raises(PermissionError, match="mandatory_other_agent_workspace_boundary"):
        authorize_path(
            snapshot,
            workspace_root=workspace,
            raw_path=secret,
            action="read",
        )
