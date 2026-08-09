"""Context-safe Google ADK MCP Resource integration."""

from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from google.adk.agents.readonly_context import ReadonlyContext
from google.adk.tools.load_mcp_resource_tool import LoadMcpResourceTool


_RESOURCE_CONTEXT: ContextVar[ReadonlyContext | None] = ContextVar(
    "openppx_mcp_resource_context",
    default=None,
)


def current_mcp_resource_context() -> ReadonlyContext | None:
    """Return the invocation context bound while ADK projects MCP Resources."""
    return _RESOURCE_CONTEXT.get()


class ContextBoundLoadMcpResourceTool(LoadMcpResourceTool):
    """Preserve the active ADK identity while listing and reading Resources.

    ADK's native loader calls the Toolset Resource methods without forwarding
    its ToolContext. Binding a ReadonlyContext here keeps dynamic tenant/user
    headers on the same authenticated MCP session boundary as ordinary tools.
    """

    async def process_llm_request(self, *, tool_context: Any, llm_request: Any) -> None:
        """Delegate to ADK while making the current invocation context available."""
        invocation_context = getattr(tool_context, "_invocation_context", None)
        token: Token[ReadonlyContext | None] | None = None
        if invocation_context is not None:
            token = _RESOURCE_CONTEXT.set(ReadonlyContext(invocation_context))
        try:
            await super().process_llm_request(
                tool_context=tool_context,
                llm_request=llm_request,
            )
        finally:
            if token is not None:
                _RESOURCE_CONTEXT.reset(token)


__all__ = [
    "ContextBoundLoadMcpResourceTool",
    "current_mcp_resource_context",
]
