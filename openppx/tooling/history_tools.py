"""Read-only ADK tools for authorized historical Session access."""

from __future__ import annotations

from typing import Any

from google.adk.tools.tool_context import ToolContext

from openppx.runtime.historical_session_service import HistoricalSessionService
from openppx.runtime.tool_execution_context import current_tool_execution_context


def _source(tool_context: ToolContext) -> tuple[str, str, str | None] | dict[str, Any]:
    """Return trusted invocation identities or a fail-closed tool error."""
    runtime_context = current_tool_execution_context()
    source_user_id = str(getattr(tool_context, "user_id", "") or "").strip()
    if runtime_context is None or not source_user_id:
        return {
            "ok": False,
            "error": {
                "code": "history_context_unavailable",
                "message": "Trusted Agent history context is unavailable.",
            },
        }
    permission_snapshot = runtime_context.current_permission_snapshot()
    privilege_level = str(getattr(permission_snapshot, "preset", "") or "").strip() or None
    return source_user_id, runtime_context.agent_id, privilege_level


def build_history_tools(service: HistoricalSessionService) -> tuple[Any, ...]:
    """Build Agent-bound history tools backed by one Node-owned service."""

    def resolve_agent_history_target(
        agent_name: str,
        tool_context: ToolContext,
    ) -> dict[str, Any]:
        """Resolve an exact Agent display name within your authorized history scope.

        Call this before listing, searching, or reading another Agent's history.
        If the name is ambiguous, ask the user to choose from the returned bounded
        candidates and never guess an immutable Agent ID.
        """
        source = _source(tool_context)
        if isinstance(source, dict):
            return source
        source_user_id, source_agent_id, source_agent_privilege_level = source
        return service.resolve_agent(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            display_name=agent_name,
            source_agent_privilege_level=source_agent_privilege_level,
        )

    async def list_agent_history_sessions(
        agent_id: str,
        tool_context: ToolContext,
        start_time: str = "",
        end_time: str = "",
        limit: int = 20,
        cursor: str = "",
    ) -> dict[str, Any]:
        """List an authorized Agent's retained Sessions in a half-open time range.

        Use ISO 8601 timestamps. Active, archived, and removed Sessions can be
        returned. Continue with ``nextCursor`` until it is null when the request
        requires every matching Session.
        """
        source = _source(tool_context)
        if isinstance(source, dict):
            return source
        source_user_id, source_agent_id, source_agent_privilege_level = source
        return await service.list_sessions(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target_agent_id=agent_id,
            start_time=start_time or None,
            end_time=end_time or None,
            limit=limit,
            cursor=cursor or None,
            source_agent_privilege_level=source_agent_privilege_level,
        )

    async def search_agent_history(
        agent_id: str,
        query: str,
        tool_context: ToolContext,
        match_mode: str = "and",
        start_time: str = "",
        end_time: str = "",
        limit: int = 20,
        cursor: str = "",
    ) -> dict[str, Any]:
        """Search authorized message text and attachment names by exact substring.

        Space-separated terms use ``and`` by default; set ``match_mode`` to
        ``or`` when any term may match. Attachment contents and Artifacts are not
        searched. Follow ``nextCursor`` for exhaustive results.
        """
        source = _source(tool_context)
        if isinstance(source, dict):
            return source
        source_user_id, source_agent_id, source_agent_privilege_level = source
        return await service.search(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target_agent_id=agent_id,
            query=query,
            match_mode=match_mode,
            start_time=start_time or None,
            end_time=end_time or None,
            limit=limit,
            cursor=cursor or None,
            source_agent_privilege_level=source_agent_privilege_level,
        )

    async def read_agent_history(
        agent_id: str,
        session_id: str,
        tool_context: ToolContext,
        start_time: str = "",
        end_time: str = "",
        limit: int = 20,
        cursor: str = "",
    ) -> dict[str, Any]:
        """Read authorized messages from one retained Session with citations.

        This is the only ordinary Agent path for reading a removed Session.
        Continue with ``nextCursor`` until it is null when the request requires
        the complete time-bounded conversation.
        """
        source = _source(tool_context)
        if isinstance(source, dict):
            return source
        source_user_id, source_agent_id, source_agent_privilege_level = source
        return await service.read(
            source_user_id=source_user_id,
            source_agent_id=source_agent_id,
            target_agent_id=agent_id,
            session_id=session_id,
            start_time=start_time or None,
            end_time=end_time or None,
            limit=limit,
            cursor=cursor or None,
            source_agent_privilege_level=source_agent_privilege_level,
        )

    return (
        resolve_agent_history_target,
        list_agent_history_sessions,
        search_agent_history,
        read_agent_history,
    )


__all__ = ["build_history_tools"]
