"""Runtime-scoped workspace isolation for ADK function tools."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from openppx import agent
from openppx.browser.runtime import (
    resolve_browser_artifact_path,
    validate_browser_upload_paths,
)
from openppx.runtime.tool_execution_context import (
    ToolExecutionContext,
    activate_tool_execution_context,
    current_tool_execution_context,
)


def _tool_callable(tools: list[object], name: str):
    """Return the callable exposed by one assembled ADK tool entry."""
    for tool in tools:
        candidate = getattr(tool, "func", tool)
        if getattr(candidate, "__name__", "") == name:
            return candidate
    raise AssertionError(f"Tool {name!r} was not assembled")


def _bound_tools(workspace: Path, *, agent_id: str) -> list[object]:
    """Build the medium-privilege core tools for one immutable Agent runtime."""
    return agent._build_tools(
        privilege_level="medium",
        can_delegate=False,
        include_gui_tools=False,
        tool_execution_context=ToolExecutionContext.for_agent(
            agent_id=agent_id,
            workspace_root=workspace,
        ),
    )


def test_bound_file_and_exec_tools_ignore_process_cwd_and_environment(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Agent tools must use their snapshot workspace rather than ambient state."""
    workspace = tmp_path / "agent-workspace"
    ambient = tmp_path / "ambient-workspace"
    process_cwd = tmp_path / "node-cwd"
    workspace.mkdir()
    ambient.mkdir()
    process_cwd.mkdir()
    monkeypatch.setenv("OPENPPX_WORKSPACE", str(ambient))
    monkeypatch.chdir(process_cwd)

    tools = _bound_tools(workspace, agent_id="writer")
    write = _tool_callable(tools, "write_file")
    execute = _tool_callable(tools, "exec")

    write_result = write("reports/result.md", "workspace-bound")
    exec_result = execute("pwd")

    assert "Successfully wrote" in write_result
    assert (workspace / "reports" / "result.md").read_text(encoding="utf-8") == "workspace-bound"
    assert not (ambient / "reports" / "result.md").exists()
    assert not (process_cwd / "reports" / "result.md").exists()
    assert exec_result.strip() == str(workspace)
    assert current_tool_execution_context() is None


def test_two_agent_tool_sets_keep_concurrent_relative_writes_isolated(tmp_path: Path) -> None:
    """Concurrent Agents hosted by one Node must never share a workspace root."""
    workspace_a = tmp_path / "workspace-a"
    workspace_b = tmp_path / "workspace-b"
    workspace_a.mkdir()
    workspace_b.mkdir()
    write_a = _tool_callable(_bound_tools(workspace_a, agent_id="agent-a"), "write_file")
    write_b = _tool_callable(_bound_tools(workspace_b, agent_id="agent-b"), "write_file")

    with ThreadPoolExecutor(max_workers=2) as pool:
        result_a = pool.submit(write_a, "result.txt", "from-a")
        result_b = pool.submit(write_b, "result.txt", "from-b")

    assert "Successfully wrote" in result_a.result()
    assert "Successfully wrote" in result_b.result()
    assert (workspace_a / "result.txt").read_text(encoding="utf-8") == "from-a"
    assert (workspace_b / "result.txt").read_text(encoding="utf-8") == "from-b"


def test_browser_relative_paths_use_active_agent_workspace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    """Browser inputs and outputs must share the Agent-scoped path boundary."""
    workspace = tmp_path / "agent-workspace"
    process_cwd = tmp_path / "node-cwd"
    workspace.mkdir()
    process_cwd.mkdir()
    upload = workspace / "inputs" / "brief.pdf"
    upload.parent.mkdir()
    upload.write_bytes(b"pdf")
    monkeypatch.chdir(process_cwd)
    monkeypatch.delenv("OPENPPX_BROWSER_UPLOAD_ROOT", raising=False)
    monkeypatch.delenv("OPENPPX_BROWSER_ARTIFACT_ROOT", raising=False)
    context = ToolExecutionContext.for_agent(
        agent_id="browser-agent",
        workspace_root=workspace,
    )

    with activate_tool_execution_context(context):
        artifact = resolve_browser_artifact_path(
            "captures/page.png",
            default_filename="screenshot.png",
        )
        uploads = validate_browser_upload_paths(["inputs/brief.pdf"])

    assert artifact == str(
        workspace / ".openteamwork" / "browser_artifacts" / "captures" / "page.png"
    )
    assert uploads == [str(upload)]
    assert current_tool_execution_context() is None
