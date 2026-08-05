"""Google ADK lifecycle bridge for trusted Codex-compatible Plugin Hooks."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from google.adk.plugins.base_plugin import BasePlugin

from openppx.extensions import PluginHookExecutor, PluginHookSnapshot


_SESSION_STARTED_KEY = "openppx:plugin_hooks:session_started"


class OpenPpxPluginHookBridge(BasePlugin):
    """Project supported ADK lifecycle callbacks into trusted Plugin Hooks."""

    def __init__(
        self,
        snapshot: PluginHookSnapshot,
        *,
        workspace: Path,
        root_agent_name: str,
    ) -> None:
        super().__init__(name="openppx_plugin_hooks")
        self._executor = PluginHookExecutor(snapshot)
        self._workspace = workspace.expanduser().resolve(strict=False)
        # Hooks run early in ADK's lifecycle, before a later bootstrap plugin can
        # guarantee that a first-use workspace already exists.
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._root_agent_name = root_agent_name

    async def before_run_callback(self, *, invocation_context: Any) -> None:
        """Emit the turn's user input before model or tool work begins."""
        content = getattr(invocation_context, "user_content", None)
        await self._executor.emit(
            "UserPromptSubmit",
            _context_payload(invocation_context, content=content),
            cwd=self._workspace,
        )
        return None

    async def before_agent_callback(self, *, agent: Any, callback_context: Any) -> None:
        """Emit first-session or subagent entry events."""
        agent_name = _agent_name(agent)
        if agent_name != self._root_agent_name:
            await self._executor.emit(
                "SubagentStart",
                _context_payload(callback_context, agent_name=agent_name),
                match_value=agent_name,
                cwd=self._workspace,
            )
            return None
        state = getattr(callback_context, "state", None)
        if state is not None and not state.get(_SESSION_STARTED_KEY):
            await self._executor.emit(
                "SessionStart",
                _context_payload(callback_context, agent_name=agent_name),
                match_value="startup",
                cwd=self._workspace,
            )
            state[_SESSION_STARTED_KEY] = True
        return None

    async def after_agent_callback(self, *, agent: Any, callback_context: Any) -> None:
        """Emit root completion or subagent completion events."""
        agent_name = _agent_name(agent)
        event = "Stop" if agent_name == self._root_agent_name else "SubagentStop"
        await self._executor.emit(
            event,
            _context_payload(callback_context, agent_name=agent_name),
            match_value=agent_name,
            cwd=self._workspace,
        )
        return None

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> None:
        """Run matching pre-tool commands before execution."""
        tool_name = _tool_name(tool)
        await self._executor.emit(
            "PreToolUse",
            _context_payload(tool_context, tool_name=tool_name, tool_input=tool_args),
            match_value=tool_name,
            cwd=self._workspace,
        )
        return None

    async def after_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
        result: Any,
    ) -> None:
        """Run matching post-tool commands with a bounded result projection."""
        tool_name = _tool_name(tool)
        await self._executor.emit(
            "PostToolUse",
            _context_payload(
                tool_context,
                tool_name=tool_name,
                tool_input=tool_args,
                tool_response=result,
            ),
            match_value=tool_name,
            cwd=self._workspace,
        )
        return None


def _context_payload(context: Any, **extra: Any) -> dict[str, Any]:
    """Build a stable, non-magical subset of ADK lifecycle context."""
    session = getattr(context, "session", None)
    values: dict[str, Any] = {
        "session_id": getattr(session, "id", None) or getattr(context, "session_id", None),
        "user_id": getattr(session, "user_id", None) or getattr(context, "user_id", None),
        "invocation_id": getattr(context, "invocation_id", None),
        "cwd": str(Path.cwd()),
    }
    values.update(extra)
    return values


def _agent_name(agent: Any) -> str:
    value = getattr(agent, "name", None)
    return value if isinstance(value, str) else ""


def _tool_name(tool: Any) -> str:
    value = getattr(tool, "name", None)
    return value if isinstance(value, str) and value else type(tool).__name__


__all__ = ["OpenPpxPluginHookBridge"]
