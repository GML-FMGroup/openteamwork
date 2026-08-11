from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from openppx.runtime.tool_execution_context import (
    ToolExecutionContext,
    activate_tool_execution_context,
)
from openppx.tooling.history_tools import build_history_tools


class _HistoryService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def resolve_agent(self, **kwargs):
        self.calls.append(("resolve", kwargs))
        return {"ok": True}

    async def list_sessions(self, **kwargs):
        self.calls.append(("list", kwargs))
        return {"ok": True}

    async def search(self, **kwargs):
        self.calls.append(("search", kwargs))
        return {"ok": True}

    async def read(self, **kwargs):
        self.calls.append(("read", kwargs))
        return {"ok": True}


def test_history_tools_derive_source_user_and_agent_from_trusted_context(tmp_path: Path) -> None:
    service = _HistoryService()
    resolve, list_sessions, search, read = build_history_tools(service)  # type: ignore[arg-type]
    runtime_context = ToolExecutionContext.for_agent(
        agent_id="trusted-agent",
        workspace_root=tmp_path,
    )
    adk_context = SimpleNamespace(user_id="trusted-user")

    with activate_tool_execution_context(runtime_context):
        resolve("Larry", adk_context)
        asyncio.run(list_sessions("target-agent", adk_context))
        asyncio.run(search("target-agent", "大模型创业", adk_context))
        asyncio.run(read("target-agent", "session-1", adk_context))

    assert [name for name, _kwargs in service.calls] == ["resolve", "list", "search", "read"]
    assert all(kwargs["source_user_id"] == "trusted-user" for _name, kwargs in service.calls)
    assert all(kwargs["source_agent_id"] == "trusted-agent" for _name, kwargs in service.calls)


def test_history_tools_fail_closed_without_node_owned_agent_context() -> None:
    service = _HistoryService()
    resolve, *_rest = build_history_tools(service)  # type: ignore[arg-type]

    result = resolve("Larry", SimpleNamespace(user_id="user"))

    assert result["ok"] is False
    assert result["error"]["code"] == "history_context_unavailable"
    assert service.calls == []
