"""Stable permission identities for Google ADK Tool entries."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from .evaluator import evaluate_permission
from .models import PermissionRequest, ResolvedPermissionSnapshot


ToolSource = Literal["builtin", "extension", "mcp", "native_app", "skill"]


@dataclass(frozen=True, slots=True)
class PermissionToolDescriptor:
    """Trusted Tool identity used by catalog and invocation authorization."""

    tool_id: str
    operation: str
    source: ToolSource
    name: str


def describe_adk_tool(tool: Any) -> PermissionToolDescriptor:
    """Derive a stable identity from an assembled ADK Tool instance."""

    name = _tool_name(tool)
    module = _tool_module(tool)
    source = _tool_source(name=name, module=module)
    namespace = {
        "builtin": "builtin",
        "extension": "extension",
        "mcp": "mcp",
        "native_app": "app",
        "skill": "skill",
    }[source]
    return PermissionToolDescriptor(
        tool_id=f"openppx.{namespace}.{name}",
        operation=name,
        source=source,
        name=name,
    )


def filter_authorized_tools(
    tools: list[Any],
    snapshot: ResolvedPermissionSnapshot,
) -> list[Any]:
    """Filter an assembled ADK catalog with the same Tool evaluator used at call time."""

    if snapshot.rollout_for("tool") != "enforce":
        return tools
    snapshot.assert_enforce_ready("tool")
    allowed: list[Any] = []
    for tool in tools:
        descriptor = describe_adk_tool(tool)
        request = PermissionRequest.model_validate(
            {
                "requestId": f"catalog:{descriptor.tool_id}",
                "permissionRevision": snapshot.revision,
                "subject": {"agentId": snapshot.agent_id},
                "object": "tool",
                "action": "invoke",
                "resource": {
                    "kind": "tool",
                    "toolId": descriptor.tool_id,
                    "operation": descriptor.operation,
                    "source": descriptor.source,
                },
            }
        )
        if evaluate_permission(snapshot, request).outcome == "allow":
            allowed.append(tool)
    return allowed


def _tool_name(tool: Any) -> str:
    value = getattr(tool, "name", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    func = getattr(tool, "func", None)
    value = getattr(func, "__name__", None) if func is not None else getattr(tool, "__name__", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return type(tool).__name__


def _tool_module(tool: Any) -> str:
    func = getattr(tool, "func", None)
    target = func if func is not None else tool
    value = getattr(target, "__module__", None)
    if not isinstance(value, str) or not value:
        value = getattr(type(tool), "__module__", "")
    return str(value).lower()


def _tool_source(*, name: str, module: str) -> ToolSource:
    if "mcp_tool" in module or ".mcp" in module:
        return "mcp"
    if "native_app" in module or "app_runtime" in module:
        return "native_app"
    if name == "invoke_skill_api":
        return "skill"
    if module.startswith("openppx.") or module.startswith("google.adk.tools"):
        return "builtin"
    return "extension"


__all__ = ["PermissionToolDescriptor", "ToolSource", "describe_adk_tool", "filter_authorized_tools"]
