"""GUI MCP detection and tool-name resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
_DEFAULT_GUI_SERVER_NAME = "openppx_gui"


@dataclass(frozen=True)
class GuiMcpRouting:
    """Resolved GUI MCP routing details."""

    server_name: str
    tool_prefix: str
    action_tool_name: str
    task_tool_name: str


def _prefixed_tool_name(prefix: str, tool_name: str) -> str:
    """Return the ADK-rendered tool name for a toolset prefix."""
    return f"{prefix}_{tool_name}" if prefix else tool_name


def resolve_gui_mcp_from_summaries(summaries: list[dict[str, str]]) -> GuiMcpRouting | None:
    """Best-effort GUI MCP routing fallback from toolset summaries."""
    if not summaries:
        return None

    candidates: list[GuiMcpRouting] = []
    for item in summaries:
        name = str(item.get("name", "")).strip()
        if not name:
            continue
        prefix = (str(item.get("prefix", "")).strip() or f"mcp_{name}").rstrip("_")
        lowered_name = name.lower()
        lowered_prefix = prefix.lower()
        if lowered_name == _DEFAULT_GUI_SERVER_NAME or lowered_prefix == "mcp_gui" or lowered_prefix.startswith("mcp_gui_"):
            candidates.append(
                GuiMcpRouting(
                    server_name=name,
                    tool_prefix=prefix,
                    action_tool_name=_prefixed_tool_name(prefix, "gui_action"),
                    task_tool_name=_prefixed_tool_name(prefix, "gui_task"),
                )
            )
    if not candidates:
        return None
    for candidate in candidates:
        if candidate.server_name.lower() == _DEFAULT_GUI_SERVER_NAME:
            return candidate
    return candidates[0]


__all__ = [
    "GuiMcpRouting",
    "resolve_gui_mcp_from_summaries",
]
