"""Explicit, non-widening migration from legacy coarse execution overrides."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from openppx.config.models import AgentConfig


_SAFE_TOOL_IDS = [
    "openppx.builtin.preload_memory",
    "openppx.builtin.load_artifacts",
    "openppx.builtin.list_skills",
    "openppx.builtin.read_skill",
    "openppx.builtin.list_skill_api_runners",
    "openppx.builtin.read_file",
    "openppx.builtin.list_dir",
    "openppx.builtin.glob",
    "openppx.builtin.grep",
    "openppx.builtin.get_goal",
    "openppx.builtin.list_tasks",
    "openppx.builtin.show_task",
    "openppx.builtin.task_control_snapshot",
    "openppx.builtin.task_output",
]
_NODE_MAINTENANCE_TOOL_IDS = [
    "openppx.builtin.remediate_stuck_tasks",
    "openppx.builtin.cleanup_terminal_tasks",
    "openppx.builtin.cleanup_orphan_runtime_facts",
    "openppx.builtin.cleanup_checkpoint_retention",
]


def migrate_legacy_execution_permissions(agent: AgentConfig) -> AgentConfig:
    """Convert the five legacy execution fields into explicit permission rules.

    Non-execution legacy fields such as delegation and high-risk confirmation stay
    in ``permissionOverrides`` until their own permission objects are designed.
    The migration never enables enforce; rollout remains an explicit operator step.
    """

    from openppx.config.models import AgentConfig

    raw = agent.model_dump(mode="json", by_alias=True)
    spec = raw["spec"]
    legacy = dict(spec.get("permissionOverrides") or {})
    permissions = dict(spec.get("permissions") or {})
    if any(permissions.get(key) for key in ("objectDefaults", "defaults", "rules")):
        if any(key in legacy for key in _legacy_aliases()):
            raise ValueError("legacy execution overrides cannot be migrated into non-empty permissions")
        return agent

    object_defaults: dict[str, str] = {}
    defaults: dict[str, dict[str, str]] = {}
    rules: list[dict[str, Any]] = []
    if legacy.pop("workspaceScope", None) is not None:
        pass
    if legacy.pop("filesystemAccess", None) == "read_only":
        defaults["workspace"] = {
            action: "deny"
            for action in ("create", "write", "edit", "rename", "delete", "execute")
        }
    shell_access = legacy.pop("shellExec", None)
    if shell_access == "denied":
        object_defaults["command"] = "deny"
        object_defaults["process"] = "deny"
    elif shell_access == "restricted":
        rules.append(
            {
                "ruleId": "migrated-deny-shell-command",
                "effect": "deny",
                "object": "command",
                "actions": ["execute"],
                "selector": {"kind": "command", "shell": True},
                "description": "Migrated from permissionOverrides.shellExec=restricted.",
            }
        )
    network_access = legacy.pop("networkAccess", None)
    if network_access == "denied":
        object_defaults["network"] = "deny"
    elif network_access == "restricted":
        defaults["network"] = {"private_access": "deny", "listen": "deny"}
    tool_access = legacy.pop("toolAccess", None)
    if tool_access == "safe":
        object_defaults["tool"] = "deny"
        rules.append(
            {
                "ruleId": "migrated-allow-safe-tools",
                "effect": "allow",
                "object": "tool",
                "actions": ["invoke"],
                "selector": {"kind": "tool", "toolIds": _SAFE_TOOL_IDS},
                "description": "Migrated from permissionOverrides.toolAccess=safe.",
            }
        )
    elif tool_access == "task_scoped":
        rules.append(
            {
                "ruleId": "migrated-deny-node-maintenance-tools",
                "effect": "deny",
                "object": "tool",
                "actions": ["invoke"],
                "selector": {"kind": "tool", "toolIds": _NODE_MAINTENANCE_TOOL_IDS},
                "description": "Migrated from permissionOverrides.toolAccess=task_scoped.",
            }
        )
    permissions.update(
        {
            "objectDefaults": object_defaults,
            "defaults": defaults,
            "rules": rules,
        }
    )
    spec["permissionOverrides"] = legacy
    spec["permissions"] = permissions
    return AgentConfig.model_validate(raw)


def _legacy_aliases() -> tuple[str, ...]:
    return ("workspaceScope", "filesystemAccess", "shellExec", "networkAccess", "toolAccess")


__all__ = ["migrate_legacy_execution_permissions"]
