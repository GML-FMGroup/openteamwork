"""Immutable, Agent-scoped context for OpenPPX tool execution."""

from __future__ import annotations

import contextlib
import functools
import inspect
from contextvars import ContextVar
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Callable, Iterator, ParamSpec, TypeVar, cast

from openppx.core.security import SecurityPolicy, load_security_policy
from openppx.permissions import (
    PermissionAuditSink,
    PermissionSnapshotAuthority,
    ResolvedPermissionSnapshot,
)


P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Node-owned security facts for one immutable Agent identity boundary.

    The context is intentionally separate from ADK Session state: Workspace and
    security policy are authorization facts and must not be model-writable.
    Compatible permissions may be refreshed and are frozen for one Tool Action.
    """

    agent_id: str
    workspace_root: Path
    security_policy: SecurityPolicy
    permission_snapshot: ResolvedPermissionSnapshot | None = None
    permission_authority: PermissionSnapshotAuthority | None = None
    permission_audit: PermissionAuditSink | None = None

    @classmethod
    def for_agent(
        cls,
        *,
        agent_id: str,
        workspace_root: str | Path,
        permission_snapshot: ResolvedPermissionSnapshot | None = None,
        permission_authority: PermissionSnapshotAuthority | None = None,
        permission_audit: PermissionAuditSink | None = None,
    ) -> "ToolExecutionContext":
        """Build a context using an explicit Agent Workspace and ambient Node limits."""
        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            raise ValueError("agent_id is required for tool execution")
        normalized_workspace = Path(workspace_root).expanduser().resolve(strict=False)
        baseline = permission_snapshot
        if permission_authority is not None:
            baseline = permission_authority.baseline
        return cls(
            agent_id=normalized_agent_id,
            workspace_root=normalized_workspace,
            security_policy=load_security_policy(workspace_root=normalized_workspace),
            permission_snapshot=baseline,
            permission_authority=permission_authority,
            permission_audit=permission_audit,
        )

    def current_permission_snapshot(self) -> ResolvedPermissionSnapshot | None:
        """Resolve permissions for the next side effect from the trusted authority."""

        if self.permission_authority is not None:
            return self.permission_authority.current()
        return self.permission_snapshot

    def pin_current_permissions(self) -> "ToolExecutionContext":
        """Freeze current permissions for one already-starting Tool Action."""

        snapshot = self.current_permission_snapshot()
        if snapshot is self.permission_snapshot and self.permission_authority is None:
            return self
        return replace(
            self,
            permission_snapshot=snapshot,
            permission_authority=None,
        )


_ACTIVE_TOOL_EXECUTION_CONTEXT: ContextVar[ToolExecutionContext | None] = ContextVar(
    "openppx_tool_execution_context",
    default=None,
)


def current_tool_execution_context() -> ToolExecutionContext | None:
    """Return the context active for the current tool call, if any."""
    return _ACTIVE_TOOL_EXECUTION_CONTEXT.get()


@contextlib.contextmanager
def activate_tool_execution_context(context: ToolExecutionContext) -> Iterator[None]:
    """Activate one immutable context for exactly one nested execution scope."""
    token = _ACTIVE_TOOL_EXECUTION_CONTEXT.set(context)
    try:
        yield
    finally:
        _ACTIVE_TOOL_EXECUTION_CONTEXT.reset(token)


def bind_tool_callable(
    func: Callable[P, R],
    context: ToolExecutionContext,
) -> Callable[P, R]:
    """Bind a callable to an Agent context while preserving its ADK schema."""
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        async def async_bound(*args: P.args, **kwargs: P.kwargs) -> Any:
            with activate_tool_execution_context(context.pin_current_permissions()):
                return await func(*args, **kwargs)

        return cast(Callable[P, R], async_bound)

    @functools.wraps(func)
    def bound(*args: P.args, **kwargs: P.kwargs) -> R:
        with activate_tool_execution_context(context.pin_current_permissions()):
            return func(*args, **kwargs)

    return bound


__all__ = [
    "ToolExecutionContext",
    "activate_tool_execution_context",
    "bind_tool_callable",
    "current_tool_execution_context",
]
