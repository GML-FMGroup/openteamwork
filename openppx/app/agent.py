"""Google ADK root agent for openppx."""

from __future__ import annotations

import os
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools import LongRunningFunctionTool
from google.adk.tools import load_artifacts
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from ..core.config import normalize_agent_privilege_level
from ..core.env_utils import env_enabled
from ..core.mcp_registry import build_mcp_toolsets_from_env
from ..core.provider import build_adk_model_from_env
from ..tooling.skills_adapter import list_skills, read_skill
from ..tooling.registry import (
    browser,
    check_browser_remote_job_protocol,
    computer_task,
    computer_use,
    advance_task_flow,
    complete_goal,
    audit_stuck_tasks,
    remediate_stuck_tasks,
    audit_orphan_runtime_facts,
    audit_checkpoint_retention,
    cleanup_terminal_tasks,
    cleanup_orphan_runtime_facts,
    cleanup_checkpoint_retention,
    cron,
    dispatch_task_action,
    edit_file,
    exec_command,
    finish_task_flow,
    glob,
    grep,
    high_risk_action_requires_confirmation,
    interrupt_task,
    invoke_skill_api,
    list_skill_api_runners,
    list_browser_remote_jobs,
    list_browser_remote_providers,
    list_dir,
    list_context_summaries,
    list_task_flows,
    list_tasks,
    long_task,
    message_file,
    message,
    message_image,
    pause_task,
    read_file,
    process_session,
    restart_task,
    resume_task,
    rollup_context_summaries,
    send_task_input,
    show_task,
    show_task_flow,
    exec_command_requires_confirmation,
    spawn_subagent,
    start_gui_task,
    task_control_snapshot,
    task_output,
    task_runtime_status,
    evaluate_staged_summary_quality_cases,
    summarize_context_text,
    summarize_staged_summary_quality_log,
    web_fetch,
    web_search,
    update_task_flow_step,
    write_task_flow,
    write_context_summary,
    write_todos,
    write_file,
    cancel_task,
)
from .prompt import (
    build_root_agent_instruction,
    build_startup_runtime_context,
    build_static_policy_instruction,
    gui_builtin_tools_enabled,
)

if False:  # pragma: no cover - import-only typing without a runtime cycle
    from openppx.config.layers import ConfigSnapshot


def _gui_builtin_tools_enabled() -> bool:
    """Return whether legacy builtin GUI tools should be exposed."""
    return gui_builtin_tools_enabled()


def _agent_privilege_level(explicit: str | None = None) -> str:
    """Return an explicit privilege level or the legacy environment value."""
    if explicit is not None:
        return normalize_agent_privilege_level(explicit)
    raw = os.getenv("OPENPPX_AGENT_PRIVILEGE_LEVEL", "").strip().lower()
    if not raw:
        return ""
    return normalize_agent_privilege_level(raw)


def _can_delegate(explicit: bool | None = None) -> bool:
    """Return an explicit delegation policy or the legacy environment value."""
    if explicit is not None:
        return explicit
    return env_enabled("OPENPPX_CAN_DELEGATE", default=True)


def _tool_name(tool: Any) -> str:
    """Return a stable tool name for filtering/debug output."""
    if hasattr(tool, "name") and isinstance(getattr(tool, "name"), str):
        return getattr(tool, "name")
    if hasattr(tool, "func"):
        func = getattr(tool, "func")
        return getattr(func, "__name__", str(tool))
    return getattr(tool, "__name__", str(tool))


def _confirm_high_risk_action(action_name: str) -> bool:
    """Return whether one high-risk action should use ADK confirmation."""
    return high_risk_action_requires_confirmation(action_name)


def _message_requires_confirmation(**_kwargs: Any) -> bool:
    """Return whether outbound message tools should request confirmation."""
    return _confirm_high_risk_action("message.send")


def _message_image_requires_confirmation(**_kwargs: Any) -> bool:
    """Return whether outbound image delivery should request confirmation."""
    return _confirm_high_risk_action("message_image.send")


def _message_file_requires_confirmation(**_kwargs: Any) -> bool:
    """Return whether outbound file delivery should request confirmation."""
    return _confirm_high_risk_action("message_file.send")


def _process_requires_confirmation(action: str = "list", **_kwargs: Any) -> bool:
    """Return whether a process-session operation should request confirmation."""
    normalized = str(action or "").strip().lower()
    return normalized in {"kill", "remove"} and _confirm_high_risk_action(f"process.{normalized}")


def _cron_requires_confirmation(action: str, **_kwargs: Any) -> bool:
    """Return whether a cron operation should request confirmation."""
    normalized = str(action or "").strip().lower()
    return normalized in {"add", "remove"} and _confirm_high_risk_action(f"cron.{normalized}")


def _confirmation_tool(func: Any, predicate: Any) -> FunctionTool:
    """Wrap a Python function in ADK's native confirmation tool wrapper."""
    return FunctionTool(func=func, require_confirmation=predicate)


def _build_instruction() -> str:
    """Build the root-agent instruction from layered prompt sections."""
    return build_root_agent_instruction()


def _build_static_instruction() -> str:
    """Build stable root-agent policy for ADK ``static_instruction``."""
    return build_static_policy_instruction()


def _build_dynamic_instruction() -> str:
    """Build startup/runtime context for ADK dynamic ``instruction``."""
    return build_startup_runtime_context()


def _build_tools(
    *,
    privilege_level: str | None = None,
    can_delegate: bool | None = None,
    include_gui_tools: bool | None = None,
    extension_tools: tuple[Any, ...] | None = None,
) -> list[Any]:
    """Assemble tools from explicit snapshot policy or the legacy env path."""
    base_tools: list[Any] = [
        PreloadMemoryTool(),
        load_artifacts,
        list_skills,
        read_skill,
        list_skill_api_runners,
        read_file,
        write_file,
        edit_file,
        list_dir,
        glob,
        grep,
        invoke_skill_api,
        long_task,
        write_todos,
        complete_goal,
        write_task_flow,
        show_task_flow,
        list_task_flows,
        update_task_flow_step,
        advance_task_flow,
        finish_task_flow,
        write_context_summary,
        summarize_context_text,
        evaluate_staged_summary_quality_cases,
        summarize_staged_summary_quality_log,
        list_context_summaries,
        rollup_context_summaries,
        list_tasks,
        show_task,
        task_control_snapshot,
        task_output,
        task_runtime_status,
        audit_stuck_tasks,
        remediate_stuck_tasks,
        audit_orphan_runtime_facts,
        audit_checkpoint_retention,
        cleanup_terminal_tasks,
        cleanup_orphan_runtime_facts,
        cleanup_checkpoint_retention,
        restart_task,
        dispatch_task_action,
        resume_task,
        pause_task,
        send_task_input,
        interrupt_task,
        cancel_task,
        _confirmation_tool(exec_command, exec_command_requires_confirmation),
        _confirmation_tool(process_session, _process_requires_confirmation),
        browser,
        check_browser_remote_job_protocol,
        list_browser_remote_jobs,
        list_browser_remote_providers,
        web_search,
        web_fetch,
        _confirmation_tool(message, _message_requires_confirmation),
        _confirmation_tool(message_image, _message_image_requires_confirmation),
        _confirmation_tool(message_file, _message_file_requires_confirmation),
        _confirmation_tool(cron, _cron_requires_confirmation),
    ]
    if _can_delegate(can_delegate):
        base_tools.append(LongRunningFunctionTool(func=spawn_subagent))
    gui_enabled = _gui_builtin_tools_enabled() if include_gui_tools is None else include_gui_tools
    if gui_enabled:
        base_tools.extend([start_gui_task, computer_task, computer_use])

    resolved_privilege_level = _agent_privilege_level(privilege_level)
    if resolved_privilege_level == "low":
        allowed_names = {
            "list_skills",
            "read_skill",
            "list_skill_api_runners",
            "read_file",
            "list_dir",
            "glob",
            "grep",
            "list_tasks",
            "show_task",
            "task_control_snapshot",
            "task_output",
            "resume_task",
            "load_artifacts",
        }
        tools = [tool for tool in base_tools if _tool_name(tool) in allowed_names or isinstance(tool, PreloadMemoryTool)]
        return tools

    if resolved_privilege_level == "medium":
        blocked_names = {"message", "message_image", "message_file"}
        tools = [tool for tool in base_tools if _tool_name(tool) not in blocked_names]
        tools.extend(build_mcp_toolsets_from_env() if extension_tools is None else extension_tools)
        return tools

    tools = list(base_tools)
    tools.extend(build_mcp_toolsets_from_env() if extension_tools is None else extension_tools)
    return tools


def build_root_agent(
    snapshot: "ConfigSnapshot",
    *,
    model: Any,
    extension_tools: tuple[Any, ...] = (),
    include_gui_tools: bool = False,
) -> LlmAgent:
    """Build one ADK Agent from an immutable Config snapshot.

    The function intentionally accepts the resolved model and extension tools
    so it never discovers Provider credentials or MCP configuration itself.
    """
    agent_config = snapshot.agent
    delegation_override = agent_config.spec.permission_overrides.can_delegate
    if delegation_override is None:
        delegation_override = agent_config.spec.privilege_level in {"medium", "high", "root"}
    return LlmAgent(
        name="openppx",
        model=model,
        static_instruction=_build_static_instruction(),
        instruction=build_startup_runtime_context(
            workspace=agent_config.spec.workspace,
            gui_tools_enabled=include_gui_tools,
        ),
        tools=_build_tools(
            privilege_level=agent_config.spec.privilege_level,
            can_delegate=delegation_override,
            include_gui_tools=include_gui_tools,
            extension_tools=extension_tools,
        ),
    )


_legacy_root_agent: LlmAgent | None = None


def build_legacy_root_agent() -> LlmAgent:
    """Build the legacy single-Agent runtime only when an old surface requests it."""
    return LlmAgent(
        name="openppx",
        model=build_adk_model_from_env(),
        static_instruction=_build_static_instruction(),
        instruction=_build_dynamic_instruction(),
        tools=_build_tools(),
    )


def __getattr__(name: str) -> Any:
    """Lazily expose ADK's legacy ``root_agent`` discovery contract."""
    global _legacy_root_agent
    if name != "root_agent":
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    if _legacy_root_agent is None:
        _legacy_root_agent = build_legacy_root_agent()
    return _legacy_root_agent


__all__ = ["build_legacy_root_agent", "build_root_agent", "root_agent"]
