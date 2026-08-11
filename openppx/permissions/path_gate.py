"""Canonical Path authorization shared by trusted filesystem executors."""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from stat import S_ISREG

from .audit import NullPermissionAuditSink, PermissionAuditSink, record_permission_audit
from .evaluator import evaluate_permission
from .models import (
    AgentWorkspaceBoundary,
    PathConstraints,
    PermissionAction,
    PermissionDecision,
    PermissionRequest,
    ResolvedPermissionSnapshot,
)


@dataclass(frozen=True, slots=True)
class AuthorizedPath:
    """Canonical path and authorization facts fixed for one filesystem action."""

    path: Path
    object: str
    action: PermissionAction
    permission_revision: str
    identity: tuple[int, int] | None = None
    max_bytes: int | None = None
    max_entries: int | None = None
    max_depth: int | None = None

    def revalidate(self) -> None:
        """Reject a path whose canonical target or inode changed after authorization."""

        current = self.path.resolve(strict=False)
        if current != self.path or self.path.is_symlink():
            raise PermissionError("Path changed after permission authorization.")
        if self.identity is not None:
            current_stat = self.path.stat(follow_symlinks=False)
            if (current_stat.st_dev, current_stat.st_ino) != self.identity:
                raise PermissionError("Path identity changed after permission authorization.")

    def ensure_parent_directories(self, *, mode: int = 0o700) -> None:
        """Create missing parent directories without following a swapped symlink."""

        if os.name == "nt":
            self.path.parent.mkdir(parents=True, exist_ok=True, mode=mode)
            self.revalidate()
            return
        directory_fd = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
        try:
            for component in self.path.parent.parts[1:]:
                try:
                    next_fd = os.open(
                        component,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                except FileNotFoundError:
                    os.mkdir(component, mode=mode, dir_fd=directory_fd)
                    next_fd = os.open(
                        component,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                os.close(directory_fd)
                directory_fd = next_fd
        finally:
            os.close(directory_fd)

    def open_fd(self, flags: int, *, mode: int = 0o600) -> int:
        """Open the authorized target through a no-symlink descriptor walk."""

        self.revalidate()
        if os.name == "nt":
            fd = os.open(self.path, flags | getattr(os, "O_NOFOLLOW", 0), mode)
        else:
            directory_fd = os.open("/", os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                for component in self.path.parent.parts[1:]:
                    next_fd = os.open(
                        component,
                        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0),
                        dir_fd=directory_fd,
                    )
                    os.close(directory_fd)
                    directory_fd = next_fd
                fd = os.open(
                    self.path.name,
                    flags | getattr(os, "O_NOFOLLOW", 0),
                    mode,
                    dir_fd=directory_fd,
                )
            finally:
                os.close(directory_fd)
        if self.identity is not None:
            metadata = os.fstat(fd)
            if (metadata.st_dev, metadata.st_ino) != self.identity:
                os.close(fd)
                raise PermissionError("Path identity changed while opening the authorized target.")
        return fd


def authorize_path(
    snapshot: ResolvedPermissionSnapshot,
    *,
    workspace_root: Path,
    raw_path: str | Path,
    action: PermissionAction,
    base_dir: Path | None = None,
    protected_roots: tuple[Path, ...] = (),
    audit: PermissionAuditSink | None = None,
) -> AuthorizedPath:
    """Canonicalize, classify, authorize, and audit one filesystem target.

    ``protected_roots`` are trusted Node-owned directories. Non-root Agents may
    use their own Workspace when it is nested below such a root, but no sibling
    Node data or another Agent Workspace is accessible.
    """

    workspace = workspace_root.expanduser().resolve(strict=False)
    candidate = Path(raw_path).expanduser()
    if not candidate.is_absolute():
        candidate = (base_dir or workspace) / candidate
    canonical = candidate.resolve(strict=False)
    resource, object_kind = _path_resource(snapshot, workspace=workspace, path=canonical)
    request = PermissionRequest.model_validate(
        {
            "requestId": f"path-{uuid.uuid4().hex}",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": snapshot.agent_id},
            "object": object_kind,
            "action": action,
            "resource": resource,
        }
    )
    floor_reason = _mandatory_floor_reason(
        snapshot,
        workspace=workspace,
        path=canonical,
        protected_roots=protected_roots,
    )
    if floor_reason is None:
        snapshot.assert_enforce_ready(object_kind)  # type: ignore[arg-type]
        decision = evaluate_permission(snapshot, request)
    else:
        decision = PermissionDecision(
            outcome="deny",
            reason_code=floor_reason,
            permission_revision=snapshot.revision,
        )
    rollout_mode = snapshot.enforcement_mode_for(object_kind)  # type: ignore[arg-type]
    record_permission_audit(
        audit or NullPermissionAuditSink(),
        request,
        decision,
        rollout_mode=rollout_mode,
    )
    if rollout_mode == "enforce" and decision.outcome != "allow":
        if floor_reason == "mandatory_node_data_boundary":
            raise PermissionError("Node data outside the current Agent Workspace is denied.")
        raise PermissionError(
            f"Path action '{action}' is denied by Agent permissions "
            f"({decision.reason_code}, revision {snapshot.revision})."
        )
    identity: tuple[int, int] | None = None
    if rollout_mode == "enforce" and canonical.exists():
        path_stat = canonical.stat(follow_symlinks=False)
        identity = (path_stat.st_dev, path_stat.st_ino)
        if snapshot.preset != "root" and S_ISREG(path_stat.st_mode) and path_stat.st_nlink > 1:
            raise PermissionError(
                "Hard-linked files are denied outside the root preset because their other names "
                "cannot be proven to stay inside the authorized boundary."
            )
    authorized = AuthorizedPath(
        path=canonical,
        object=object_kind,
        action=action,
        permission_revision=snapshot.revision,
        identity=identity,
        max_bytes=_minimum_constraint(snapshot, decision.matched_rule_ids, "max_bytes"),
        max_entries=_minimum_constraint(snapshot, decision.matched_rule_ids, "max_entries"),
        max_depth=_minimum_constraint(snapshot, decision.matched_rule_ids, "max_depth"),
    )
    authorized.revalidate()
    return authorized


def _mandatory_floor_reason(
    snapshot: ResolvedPermissionSnapshot,
    *,
    workspace: Path,
    path: Path,
    protected_roots: tuple[Path, ...],
) -> str | None:
    """Return a non-overridable non-root filesystem denial reason, if any."""

    if snapshot.preset == "root":
        return None
    owner = _workspace_owner(snapshot.agent_workspaces, path)
    if owner is not None and owner.agent_id != snapshot.agent_id:
        return "mandatory_other_agent_workspace_boundary"
    inside_workspace = path == workspace or path.is_relative_to(workspace)
    for root in protected_roots:
        canonical_root = root.expanduser().resolve(strict=False)
        if path == canonical_root or path.is_relative_to(canonical_root):
            workspace_is_nested = (
                workspace != canonical_root and workspace.is_relative_to(canonical_root)
            )
            if workspace_is_nested and inside_workspace:
                return None
            return "mandatory_node_data_boundary"
    if inside_workspace:
        return None
    return None


def _minimum_constraint(
    snapshot: ResolvedPermissionSnapshot,
    matched_rule_ids: tuple[str, ...],
    field_name: str,
) -> int | None:
    values = [
        value
        for rule in snapshot.rules
        if rule.rule_id in matched_rule_ids
        and rule.effect == "allow"
        and isinstance(rule.constraints, PathConstraints)
        and (value := getattr(rule.constraints, field_name)) is not None
    ]
    return min(values) if values else None


def _path_resource(
    snapshot: ResolvedPermissionSnapshot,
    *,
    workspace: Path,
    path: Path,
) -> tuple[dict[str, object], str]:
    try:
        relative = path.relative_to(workspace)
    except ValueError:
        owner = _workspace_owner(snapshot.agent_workspaces, path)
        resource: dict[str, object] = {
            "kind": "external_path",
            "path": str(path),
        }
        if owner is not None:
            resource["ownerAgentId"] = owner.agent_id
            resource["ownerPrivilegeLevel"] = owner.privilege_level
        return resource, "external_path"
    return {
        "kind": "workspace_path",
        "path": relative.as_posix() or ".",
    }, "workspace"


def _workspace_owner(
    boundaries: tuple[AgentWorkspaceBoundary, ...],
    path: Path,
) -> AgentWorkspaceBoundary | None:
    matches: list[tuple[int, AgentWorkspaceBoundary]] = []
    for boundary in boundaries:
        root = Path(boundary.workspace).expanduser().resolve(strict=False)
        if path == root or path.is_relative_to(root):
            matches.append((len(root.parts), boundary))
    if not matches:
        return None
    return max(matches, key=lambda item: item[0])[1]


__all__ = ["AuthorizedPath", "authorize_path"]
