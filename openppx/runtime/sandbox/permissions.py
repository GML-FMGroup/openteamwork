"""Derive sandbox intent from one immutable Agent permission snapshot."""

from __future__ import annotations

from pathlib import Path

from openppx.permissions.models import ExternalPathSelector, ResolvedPermissionSnapshot

from .egress_policy import load_egress_proxy_credential
from .plan import (
    FileSystemPolicy,
    NetworkMode,
    NetworkPolicy,
    PathAccessMode,
    PathGrant,
    PermissionProfile,
)


def derive_sandbox_permission_profile(
    snapshot: ResolvedPermissionSnapshot,
    *,
    workspace_root: Path,
) -> PermissionProfile:
    """Compile an Agent permission snapshot into sandbox filesystem/network intent.

    Only concrete external roots become mounts. A logical ``external_path=allow``
    default never mounts the host root implicitly; operators must declare mountable
    roots explicitly when arbitrary code needs host files.
    """

    workspace = workspace_root.expanduser().resolve(strict=False)
    workspace_access = (
        PathAccessMode.READ
        if snapshot.default_for("workspace", "write") == "deny"
        else PathAccessMode.WRITE
    )
    workspace_grant = PathGrant(
        logical_name="workspace",
        host_path=workspace,
        container_path=str(workspace),
        access=workspace_access,
        follow_symlinks=False,
    )
    readable: list[PathGrant] = []
    writable: list[PathGrant] = []
    denied = set(_workspace_sensitive_roots(workspace))

    for rule in snapshot.rules:
        if rule.object != "external_path" or not isinstance(rule.selector, ExternalPathSelector):
            continue
        roots = tuple(Path(value).expanduser().resolve(strict=False) for value in rule.selector.paths)
        if rule.effect == "deny":
            if rule.action in {"create", "write", "edit", "rename", "delete", "execute"}:
                denied.update(roots)
            continue
        for root in roots:
            access = (
                PathAccessMode.WRITE
                if rule.action in {"create", "write", "edit", "rename", "delete"}
                else PathAccessMode.READ
            )
            grant = PathGrant(
                logical_name=f"permission:{rule.source_rule_id}:{root.name or 'root'}",
                host_path=root,
                container_path=str(root),
                access=access,
                follow_symlinks=False,
            )
            target = writable if access == PathAccessMode.WRITE else readable
            if grant not in target:
                target.append(grant)

    filesystem = FileSystemPolicy(
        readable_roots=(workspace_grant, *readable) if workspace_access == PathAccessMode.READ else tuple(readable),
        writable_roots=(workspace_grant, *writable) if workspace_access == PathAccessMode.WRITE else tuple(writable),
        denied_roots=tuple(sorted(denied, key=str)),
    )
    return PermissionProfile(
        name=f"permission-{snapshot.preset}-{snapshot.revision[7:19]}",
        filesystem=filesystem,
        network=_network_policy(snapshot),
    )


def _network_policy(snapshot: ResolvedPermissionSnapshot) -> NetworkPolicy:
    if snapshot.preset == "low":
        return NetworkPolicy(mode=NetworkMode.DISABLED, lock=NetworkMode.DISABLED)
    if snapshot.preset in {"medium", "high"}:
        proxy = snapshot.code_egress_proxy
        return NetworkPolicy(
            mode=NetworkMode.PROXY_ONLY,
            lock=NetworkMode.PROXY_ONLY,
            proxy_url=proxy.url if proxy is not None else None,
            docker_network=proxy.docker_network if proxy is not None else None,
            permission_revision=snapshot.revision,
            proxy_credential=(
                load_egress_proxy_credential(
                    policy_directory=Path(proxy.policy_directory),
                    permission_revision=snapshot.revision,
                )
                if proxy is not None
                else None
            ),
        )
    return NetworkPolicy(mode=NetworkMode.ENABLED)


def _workspace_sensitive_roots(workspace: Path) -> tuple[Path, ...]:
    return tuple(workspace / name for name in (".env", ".ssh", ".aws", ".git-credentials"))


__all__ = ["derive_sandbox_permission_profile"]
