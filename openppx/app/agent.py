"""Google ADK root agent for openppx."""

from __future__ import annotations

import json
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools import LongRunningFunctionTool
from google.adk.tools import load_artifacts
from google.adk.tools.preload_memory_tool import PreloadMemoryTool

from ..config import normalize_agent_privilege_level
from ..core.mcp_registry import summarize_mcp_toolsets
from ..extensions import ExtensionError, SkillSnapshot
from ..runtime.goal_store import GoalStore
from ..tooling.goal_tools import GoalToolRuntimeSnapshot, build_goal_tools
from ..tooling.skills_adapter import list_skills, read_skill
from ..tooling.registry import (
    browser,
    check_browser_remote_job_protocol,
    computer_task,
    computer_use,
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
    list_tasks,
    pause_task,
    read_file,
    process_session,
    restart_task,
    resume_task,
    rollup_context_summaries,
    send_task_input,
    show_task,
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
    write_context_summary,
    write_file,
    cancel_task,
)
from .prompt import (
    build_startup_runtime_context,
    build_static_policy_instruction,
)

if False:  # pragma: no cover - import-only typing without a runtime cycle
    from openppx.config.layers import ConfigSnapshot


def _gui_builtin_tools_enabled() -> bool:
    """Return the explicit default for direct internal tool assembly."""
    return True


def _agent_privilege_level(explicit: str | None = None) -> str:
    """Return the explicit privilege level supplied by the Config snapshot."""
    return normalize_agent_privilege_level(explicit, default="")


def _can_delegate(explicit: bool | None = None) -> bool:
    """Return an explicit delegation policy with a deterministic default."""
    return True if explicit is None else explicit


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


def _build_static_instruction() -> str:
    """Build stable root-agent policy for ADK ``static_instruction``."""
    return build_static_policy_instruction()


def _build_tools(
    *,
    privilege_level: str | None = None,
    can_delegate: bool | None = None,
    include_gui_tools: bool | None = None,
    extension_tools: tuple[Any, ...] | None = None,
    skill_tools: tuple[Any, ...] | None = None,
    goal_tools: tuple[Any, ...] = (),
) -> list[Any]:
    """Assemble tools from explicit snapshot policy and extension resources."""
    resolved_skill_tools = (list_skills, read_skill) if skill_tools is None else skill_tools
    base_tools: list[Any] = [
        PreloadMemoryTool(),
        load_artifacts,
        *resolved_skill_tools,
        list_skill_api_runners,
        read_file,
        write_file,
        edit_file,
        list_dir,
        glob,
        grep,
        invoke_skill_api,
        *goal_tools,
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
            "get_goal",
            "list_tasks",
            "show_task",
            "task_control_snapshot",
            "task_output",
            "resume_task",
            "load_artifacts",
        }
        tools = [tool for tool in base_tools if _tool_name(tool) in allowed_names or isinstance(tool, PreloadMemoryTool)]
        tools.extend(extension_tools or ())
        return tools

    tools = list(base_tools)
    tools.extend(extension_tools or ())
    return tools


def build_root_agent(
    snapshot: "ConfigSnapshot",
    *,
    model: Any,
    extension_tools: tuple[Any, ...] = (),
    include_gui_tools: bool = False,
    skill_snapshot: SkillSnapshot | None = None,
    mcp_summaries: list[dict[str, str]] | None = None,
    goal_store: GoalStore | None = None,
    extension_snapshot_digest: str = "",
) -> LlmAgent:
    """Build one ADK Agent from an immutable Config snapshot.

    The function intentionally accepts the resolved model and extension tools
    so it never discovers Provider credentials or MCP configuration itself.
    """
    agent_config = snapshot.agent
    resolved_skill_snapshot = skill_snapshot or SkillSnapshot.empty()
    delegation_override = agent_config.spec.permission_overrides.can_delegate
    if delegation_override is None:
        delegation_override = agent_config.spec.privilege_level in {"medium", "high", "root"}
    resolved_goal_tools = ()
    if goal_store is not None:
        resolved_goal_tools = build_goal_tools(
            goal_store,
            GoalToolRuntimeSnapshot(
                agent_id=agent_config.metadata.name,
                workspace_ref=agent_config.spec.workspace,
                permission_revision=snapshot.revision,
                model_profile_revision=snapshot.model.revision,
                extension_snapshot_digest=extension_snapshot_digest,
            ),
        )
    return LlmAgent(
        name="openppx",
        model=model,
        static_instruction=_build_static_instruction(),
        instruction=build_startup_runtime_context(
            workspace=agent_config.spec.workspace,
            skills_summary=resolved_skill_snapshot.build_summary(),
            gui_tools_enabled=include_gui_tools,
            mcp_summaries=mcp_summaries or summarize_mcp_toolsets(list(extension_tools)),
            agent_instruction=agent_config.spec.instruction,
        ),
        tools=_build_tools(
            privilege_level=agent_config.spec.privilege_level,
            can_delegate=delegation_override,
            include_gui_tools=include_gui_tools,
            extension_tools=extension_tools,
            skill_tools=_snapshot_skill_tools(resolved_skill_snapshot),
            goal_tools=resolved_goal_tools,
        ),
    )


def _snapshot_skill_tools(snapshot: SkillSnapshot) -> tuple[Any, Any]:
    """Build Skill tools that cannot observe lifecycle changes after assembly."""

    def list_runtime_skills() -> str:
        """List Skills pinned to this Agent Runtime as JSON."""
        payload = [
            {
                "name": skill.name,
                "description": skill.description,
                "source": skill.source,
                "digest": skill.digest,
            }
            for skill in snapshot.skills
        ]
        return json.dumps(payload, ensure_ascii=False, indent=2)

    def read_runtime_skill(name: str) -> str:
        """Read one SKILL.md pinned to this Agent Runtime."""
        try:
            return snapshot.read_skill(name)
        except ExtensionError as exc:
            return json.dumps(
                {
                    "ok": False,
                    "error_type": exc.code,
                    "error": str(exc),
                    "available_skills": list(snapshot.names),
                },
                ensure_ascii=False,
                indent=2,
            )

    # Preserve the public tool names expected by the prompt and permission filters.
    list_runtime_skills.__name__ = "list_skills"
    read_runtime_skill.__name__ = "read_skill"
    return list_runtime_skills, read_runtime_skill


__all__ = ["build_root_agent"]
