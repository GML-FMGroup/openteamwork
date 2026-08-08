"""Command profiles and Tool catalog enforcement tests."""

from __future__ import annotations

import subprocess
from pathlib import Path
from unittest import mock

import pytest

from openppx import agent
from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import ProcessFacts, authorize_command, authorize_process, compile_permission_snapshot
from openppx.runtime.tool_execution_context import ToolExecutionContext, activate_tool_execution_context
from openppx.tooling.registry import exec_command


def _snapshot(
    preset: str,
    workspace: Path,
    *objects: str,
    agent_rules: list[dict[str, object]] | None = None,
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
                "permissions": {"rules": agent_rules or []},
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
