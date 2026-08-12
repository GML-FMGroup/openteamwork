"""Command profiles and Tool catalog enforcement tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from openppx import agent
from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import (
    PermissionAuditQuery,
    PermissionAuditStore,
    ProcessFacts,
    authorize_command,
    authorize_process,
    compile_permission_snapshot,
)
from openppx.runtime.tool_execution_context import ToolExecutionContext, activate_tool_execution_context
from openppx.tooling.registry import exec_command


def _snapshot(
    preset: str,
    workspace: Path,
    *objects: str,
    agent_rules: list[dict[str, object]] | None = None,
    rollout_mode: str | None = None,
):
    rollout = {name: "enforce" for name in objects}
    node_permissions: dict[str, object] = {"rolloutModes": rollout}
    if preset in {"medium", "high"} and "command" in objects:
        policy_directory = workspace.parent / "egress-policies"
        node_permissions["codeEgressProxy"] = {
            "url": "http://openppx-egress-proxy:3128",
            "dockerNetwork": "openppx-egress-internal",
            "policyDirectory": str(policy_directory),
        }
    if preset == "high" and "command" in objects:
        protected = workspace.parent / "protected"
        protected.mkdir(exist_ok=True)
        node_permissions["highProtectedWriteRoots"] = [str(protected)]
    node = NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {
                "displayName": "Node",
                "enabledAgents": ["worker"],
                "permissions": node_permissions,
            },
        }
    )
    worker = AgentConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "worker"},
            "spec": {
                "displayName": "Worker",
                "workspace": str(workspace),
                "ownerPrincipalId": "local:owner",
                "privilegeLevel": preset,
                "permissions": {
                    **({"rolloutMode": rollout_mode} if rollout_mode is not None else {}),
                    "rules": agent_rules or [],
                },
            },
        }
    )
    return compile_permission_snapshot(node=node, agent=worker)


def test_low_command_profile_allows_direct_rg_and_denies_shell_or_external_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = _snapshot("low", workspace, "command")

    authorized = authorize_command(
        snapshot,
        workspace_root=workspace,
        argv=["rg", "answer", "."],
        cwd=workspace,
        shell=False,
        background=False,
        pty=False,
        timeout_seconds=60,
    )

    assert authorized.execution_profile == "low-workspace-readonly"
    assert authorized.required_backend == "docker"
    assert authorized.timeout_seconds == 30
    with pytest.raises(PermissionError, match="denied|Shell"):
        authorize_command(
            snapshot,
            workspace_root=workspace,
            argv=["rg", "answer", ".", ">", "result.txt"],
            cwd=workspace,
            shell=True,
            background=False,
            pty=False,
            timeout_seconds=60,
        )
    with pytest.raises(PermissionError, match="outside the Agent Workspace|must stay inside"):
        authorize_command(
            snapshot,
            workspace_root=workspace,
            argv=["rg", "answer", str(tmp_path.parent)],
            cwd=workspace,
            shell=False,
            background=False,
            pty=False,
            timeout_seconds=60,
        )


def test_low_observe_cannot_run_sqlite_or_fall_back_to_the_host(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = _snapshot("low", workspace, rollout_mode="observe")

    with pytest.raises(PermissionError, match="denied"):
        authorize_command(
            snapshot,
            workspace_root=workspace,
            argv=["sqlite3", "sessions.db", ".tables"],
            cwd=workspace,
            shell=False,
            background=False,
            pty=False,
            timeout_seconds=60,
        )

    authorized = authorize_command(
        snapshot,
        workspace_root=workspace,
        argv=["rg", "answer", "."],
        cwd=workspace,
        shell=False,
        background=False,
        pty=False,
        timeout_seconds=60,
    )
    assert authorized.required_backend == "docker"
    assert authorized.timeout_seconds == 30


@pytest.mark.parametrize(("preset", "profile"), [("medium", "medium-task-sandbox"), ("high", "high-protected-sandbox")])
def test_medium_and_high_commands_require_docker(
    tmp_path: Path,
    preset: str,
    profile: str,
) -> None:
    workspace = tmp_path / preset
    workspace.mkdir()
    authorized = authorize_command(
        _snapshot(preset, workspace, "command"),
        workspace_root=workspace,
        argv=["python", "task.py"],
        cwd=workspace,
        shell=False,
        background=False,
        pty=False,
        timeout_seconds=60,
    )

    assert authorized.execution_profile == profile
    assert authorized.required_backend == "docker"


def test_medium_offline_command_does_not_require_an_egress_proxy(tmp_path: Path) -> None:
    """Medium commands stay Docker-isolated and usable when the Node has no proxy."""

    workspace = tmp_path / "medium"
    workspace.mkdir()
    node = NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {"displayName": "Node", "enabledAgents": ["worker"]},
        }
    )
    worker = AgentConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "worker"},
            "spec": {
                "displayName": "Worker",
                "workspace": str(workspace),
                "ownerPrincipalId": "local:owner",
                "privilegeLevel": "medium",
            },
        }
    )
    snapshot = compile_permission_snapshot(node=node, agent=worker)
    assert "medium-code-egress-proxy" not in snapshot.blocking_gates

    authorized = authorize_command(
        snapshot,
        workspace_root=workspace,
        argv=["node", "make_deck.js"],
        cwd=workspace,
        shell=False,
        background=False,
        pty=False,
        timeout_seconds=60,
    )

    assert authorized.execution_profile == "medium-task-sandbox"
    assert authorized.required_backend == "docker"


@pytest.mark.parametrize(
    "argv",
    [
        ["npm", "install", "pptxgenjs"],
        ["pnpm", "add", "pptxgenjs"],
        ["pip", "install", "python-pptx"],
        ["python", "-m", "pip", "install", "python-pptx"],
        ["uv", "pip", "install", "python-pptx"],
        ["uvx", "ruff"],
        ["corepack", "pnpm", "install"],
        ["env", "MODE=prod", "npm", "ci"],
        ["MODE=prod", "npm", "ci"],
        ["sh", "-c", "node make_deck.js && npm install pptxgenjs"],
        ["bash", "-lc", "python -m uv pip install python-pptx"],
        ["sudo", "npm", "exec", "cowsay"],
    ],
)
def test_non_root_command_profiles_reject_runtime_package_installation(
    tmp_path: Path,
    argv: list[str],
) -> None:
    """Reviewed dependencies come from the image, never an Agent package install."""

    workspace = tmp_path / "medium"
    workspace.mkdir()
    snapshot = _snapshot("medium", workspace, "command")

    with pytest.raises(PermissionError, match="Runtime package installation"):
        authorize_command(
            snapshot,
            workspace_root=workspace,
            argv=argv,
            cwd=workspace,
            shell=False,
            background=False,
            pty=False,
            timeout_seconds=60,
        )


def test_runtime_package_install_denial_is_audited(tmp_path: Path) -> None:
    """The hard runtime floor must not leave an audited allow decision behind."""

    workspace = tmp_path / "medium"
    workspace.mkdir()
    snapshot = _snapshot("medium", workspace, "command")
    audit = PermissionAuditStore(tmp_path / "permission-audit.db")

    with pytest.raises(PermissionError, match="Runtime package installation"):
        authorize_command(
            snapshot,
            workspace_root=workspace,
            argv=["npm", "install", "pptxgenjs"],
            cwd=workspace,
            shell=False,
            background=False,
            pty=False,
            timeout_seconds=60,
            audit=audit,
        )

    rows = audit.list(PermissionAuditQuery(object="command"))
    assert len(rows) == 1
    assert rows[0]["outcome"] == "deny"
    assert rows[0]["reasonCode"] == "runtime_package_install_denied"


def test_root_command_profile_may_manage_its_own_runtime_packages(tmp_path: Path) -> None:
    """The runtime-install floor applies to non-root Agents only."""

    workspace = tmp_path / "root"
    workspace.mkdir()
    authorized = authorize_command(
        _snapshot("root", workspace, "command"),
        workspace_root=workspace,
        argv=["python", "-m", "pip", "install", "python-pptx"],
        cwd=workspace,
        shell=False,
        background=False,
        pty=False,
        timeout_seconds=60,
    )

    assert authorized.execution_profile == "root-host"
    assert authorized.required_backend is None


def test_command_constraints_are_intersected_and_cap_runtime_limits(tmp_path: Path) -> None:
    workspace = tmp_path / "medium"
    workspace.mkdir()
    snapshot = _snapshot(
        "medium",
        workspace,
        "command",
        agent_rules=[
            {
                "ruleId": "bounded-python",
                "effect": "allow",
                "object": "command",
                "actions": ["execute"],
                "selector": {
                    "kind": "command",
                    "executables": ["python"],
                    "executionProfiles": ["medium-task-sandbox"],
                },
                "constraints": {
                    "kind": "command",
                    "executionProfile": "medium-task-sandbox",
                    "allowShell": False,
                    "timeoutSeconds": 12,
                    "maxOutputBytes": 4096,
                },
            }
        ],
    )

    authorized = authorize_command(
        snapshot,
        workspace_root=workspace,
        argv=["python", "task.py"],
        cwd=workspace,
        shell=False,
        background=False,
        pty=False,
        timeout_seconds=60,
    )

    assert authorized.timeout_seconds == 12
    assert authorized.max_output_bytes == 4096
    with pytest.raises(PermissionError, match="Shell"):
        authorize_command(
            snapshot,
            workspace_root=workspace,
            argv=["python", "task.py"],
            cwd=workspace,
            shell=True,
            background=False,
            pty=False,
            timeout_seconds=60,
        )


def test_low_runtime_exec_can_search_but_cannot_use_redirection(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "answers.txt").write_text("the answer is 42\n", encoding="utf-8")
    snapshot = _snapshot("low", workspace, "command")
    context = ToolExecutionContext.for_agent(
        agent_id="worker",
        workspace_root=workspace,
        permission_snapshot=snapshot,
    )

    completed = subprocess.CompletedProcess(
        args=[],
        returncode=0,
        stdout="the answer is 42\n",
        stderr="",
    )
    with mock.patch(
        "openppx.tooling.registry._run_limited_foreground_process",
        return_value=completed,
    ) as run_process, activate_tool_execution_context(context):
        allowed = exec_command("grep answer answers.txt")
        denied = exec_command("grep answer answers.txt > copied.txt")

    assert "the answer is 42" in allowed
    assert run_process.call_args.args[0][0] == "docker"
    assert "Error:" in denied
    assert not (workspace / "copied.txt").exists()


def test_enforced_low_tool_catalog_filters_extensions_and_write_tools(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def extension_tool() -> str:
        return "extension"

    context = ToolExecutionContext.for_agent(
        agent_id="worker",
        workspace_root=workspace,
        permission_snapshot=_snapshot("low", workspace, "tool"),
    )
    tools = agent._build_tools(
        privilege_level="low",
        can_delegate=False,
        include_gui_tools=False,
        extension_tools=(extension_tool,),
        tool_execution_context=context,
    )
    names = {
        getattr(tool, "name", getattr(getattr(tool, "func", tool), "__name__", ""))
        for tool in tools
    }

    assert "read_file" in names
    assert "exec" in names
    assert "write_file" not in names
    assert "extension_tool" not in names


def test_low_observe_tool_catalog_still_filters_extensions_and_includes_history(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    def extension_tool() -> str:
        return "extension"

    def resolve_agent_history_target() -> None:
        return None

    def list_agent_history_sessions() -> None:
        return None

    def search_agent_history() -> None:
        return None

    def read_agent_history() -> None:
        return None

    for history_tool in (
        resolve_agent_history_target,
        list_agent_history_sessions,
        search_agent_history,
        read_agent_history,
    ):
        history_tool.__module__ = "openppx.tooling.history_tools"

    context = ToolExecutionContext.for_agent(
        agent_id="worker",
        workspace_root=workspace,
        permission_snapshot=_snapshot("low", workspace, rollout_mode="observe"),
    )
    tools = agent._build_tools(
        privilege_level="low",
        can_delegate=False,
        include_gui_tools=False,
        extension_tools=(extension_tool,),
        history_tools=(
            resolve_agent_history_target,
            list_agent_history_sessions,
            search_agent_history,
            read_agent_history,
        ),
        tool_execution_context=context,
    )
    names = {
        getattr(tool, "name", getattr(getattr(tool, "func", tool), "__name__", ""))
        for tool in tools
    }

    assert {
        "resolve_agent_history_target",
        "list_agent_history_sessions",
        "search_agent_history",
        "read_agent_history",
    } <= names
    assert "extension_tool" not in names
    assert "write_file" not in names


@pytest.mark.parametrize("preset", ["low", "medium", "high"])
def test_non_root_catalog_never_exposes_unsandboxed_skill_api_execution(
    tmp_path: Path,
    preset: str,
) -> None:
    workspace = tmp_path / preset
    workspace.mkdir()
    context = ToolExecutionContext.for_agent(
        agent_id="worker",
        workspace_root=workspace,
        permission_snapshot=_snapshot(preset, workspace, rollout_mode="observe"),
    )

    tools = agent._build_tools(
        privilege_level=preset,
        can_delegate=False,
        include_gui_tools=False,
        tool_execution_context=context,
    )
    names = {
        getattr(tool, "name", getattr(getattr(tool, "func", tool), "__name__", ""))
        for tool in tools
    }

    assert "invoke_skill_api" not in names


def test_medium_process_management_uses_current_task_provenance(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = _snapshot("medium", workspace, "process")
    facts = ProcessFacts(
        process_id=123,
        created_by_agent_id="worker",
        created_by_task_id="task-a",
        created_by_allowed_command=True,
    )

    assert authorize_process(snapshot, action="stop", facts=facts, task_id="task-a") is True
    with pytest.raises(PermissionError, match="Process action 'stop'"):
        authorize_process(snapshot, action="stop", facts=facts, task_id="task-b")


def test_high_cannot_manage_protected_processes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    snapshot = _snapshot("high", workspace, "process")

    with pytest.raises(PermissionError, match="explicit_deny"):
        authorize_process(
            snapshot,
            action="stop",
            facts=ProcessFacts(process_id=1, protected=True),
            task_id="task-a",
        )
