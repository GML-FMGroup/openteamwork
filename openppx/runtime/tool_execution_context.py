"""Immutable, Agent-scoped context for OpenPPX tool execution."""

from __future__ import annotations

import contextlib
import functools
import inspect
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, ParamSpec, TypeVar, cast

from openppx.core.security import SecurityPolicy, load_security_policy
from openppx.permissions import PermissionAuditSink, ResolvedPermissionSnapshot


P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class ToolExecutionContext:
    """Node-owned security facts pinned to one immutable Agent Runtime.

    The context is intentionally separate from ADK Session state: Workspace and
    security policy are authorization facts and must not be model-writable.
    """

    agent_id: str
    workspace_root: Path
    security_policy: SecurityPolicy
    permission_snapshot: ResolvedPermissionSnapshot | None = None
    permission_audit: PermissionAuditSink | None = None

    @classmethod
    def for_agent(
        cls,
        *,
        agent_id: str,
        workspace_root: str | Path,
        permission_snapshot: ResolvedPermissionSnapshot | None = None,
        permission_audit: PermissionAuditSink | None = None,
    ) -> "ToolExecutionContext":
        """Build a context using an explicit Agent Workspace and ambient Node limits."""
        normalized_agent_id = str(agent_id or "").strip()
        if not normalized_agent_id:
            raise ValueError("agent_id is required for tool execution")
        normalized_workspace = Path(workspace_root).expanduser().resolve(strict=False)
        return cls(
            agent_id=normalized_agent_id,
            workspace_root=normalized_workspace,
            security_policy=load_security_policy(workspace_root=normalized_workspace),
            permission_snapshot=permission_snapshot,
            permission_audit=permission_audit,
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
            with activate_tool_execution_context(context):
                return await func(*args, **kwargs)

        return cast(Callable[P, R], async_bound)

    @functools.wraps(func)
    def bound(*args: P.args, **kwargs: P.kwargs) -> R:
        with activate_tool_execution_context(context):
            return func(*args, **kwargs)

    return bound


__all__ = [
    "ToolExecutionContext",
    "activate_tool_execution_context",
    "bind_tool_callable",
    "current_tool_execution_context",
]
