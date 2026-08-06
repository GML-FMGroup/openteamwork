"""ADK Goal tool tests against the formal Node GoalStore."""

from __future__ import annotations

import json
from types import SimpleNamespace

from openppx.runtime.goal_store import GoalStore
from openppx.tooling.goal_tools import GoalToolRuntimeSnapshot, build_goal_tools


def _context() -> SimpleNamespace:
    return SimpleNamespace(
        user_id="local:user",
        invocation_id="invocation-1",
        function_call_id="function-1",
        session=SimpleNamespace(id="session-1"),
    )


def _tools(store: GoalStore) -> dict[str, object]:
    built = build_goal_tools(
        store,
        GoalToolRuntimeSnapshot(
            agent_id="main",
            workspace_ref="workspace",
            permission_revision="permission-1",
            model_profile_revision="model-1",
            extension_snapshot_digest="extension-1",
        ),
    )
    return {tool.__name__: tool for tool in built}


def test_goal_tools_share_formal_store_and_preserve_snapshot(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    tools = _tools(store)

    created = json.loads(
        tools["create_goal"](  # type: ignore[operator]
            "Ship the report",
            ["Final PDF exists"],
            ["Do not publish"],
            tool_context=_context(),
        )
    )
    goal_id = created["goal"]["goalId"]
    read = json.loads(tools["get_goal"](tool_context=_context()))  # type: ignore[operator]

    goal = store.get_goal(goal_id)
    assert created["ok"] is True
    assert read["goal"]["goalId"] == goal_id
    assert goal is not None
    assert goal.permission_revision == "permission-1"
    assert goal.model_profile_revision == "model-1"
    assert goal.extension_snapshot_digest == "extension-1"


def test_goal_tools_plan_and_completion_require_persisted_evidence(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    tools = _tools(store)
    created = json.loads(
        tools["create_goal"]("Build a file", tool_context=_context())  # type: ignore[operator]
    )
    flow_id = created["goal"]["flow"]["flowId"]
    planned = json.loads(
        tools["update_task_flow"](  # type: ignore[operator]
            [{"stepId": "write", "title": "Write file", "status": "pending"}],
            tool_context=_context(),
        )
    )
    advanced = json.loads(
        tools["advance_task_flow_step"](  # type: ignore[operator]
            "write",
            "completed",
            tool_context=_context(),
        )
    )
    rejected = json.loads(
        tools["complete_goal"](  # type: ignore[operator]
            [{"type": "artifact", "ref": "missing.txt"}],
            tool_context=_context(),
        )
    )
    store.record_artifact_fact(
        session_id="session-1",
        run_id="run-1",
        artifact_ref="result.txt",
        version=1,
    )
    completed = json.loads(
        tools["complete_goal"](  # type: ignore[operator]
            [{"type": "artifact", "ref": "result.txt"}],
            tool_context=_context(),
        )
    )

    assert planned["flow"]["flowId"] == flow_id
    assert advanced["flow"]["steps"][0]["status"] == "completed"
    assert rejected["ok"] is False
    assert completed["ok"] is True
    assert completed["goal"]["status"] == "completed"


def test_goal_tool_stages_current_run_completion_until_runtime_success(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    tools = _tools(store)
    created = json.loads(
        tools["create_goal"]("Answer the question", tool_context=_context())  # type: ignore[operator]
    )
    goal_id = created["goal"]["goalId"]

    pending = json.loads(
        tools["complete_goal"](tool_context=_context())  # type: ignore[operator]
    )
    still_active = store.get_goal(goal_id)
    store.record_run_fact(
        session_id="session-1",
        run_id="run-1",
        status="running",
        invocation_id="invocation-1",
    )
    store.record_run_fact(
        session_id="session-1",
        run_id="run-1",
        status="completed",
        invocation_id="invocation-1",
    )
    completed = store.get_goal(goal_id)
    flow = store.flow_for_goal(goal_id)

    assert pending["ok"] is True
    assert pending["pending"] is True
    assert still_active is not None and still_active.status == "active"
    assert completed is not None and completed.status == "completed"
    assert completed.completion_evidence[0]["ref"] == "run-1"
    assert flow is not None and flow.status == "completed"


def test_goal_tool_does_not_complete_when_requesting_run_fails(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    tools = _tools(store)
    created = json.loads(
        tools["create_goal"]("Answer safely", tool_context=_context())  # type: ignore[operator]
    )
    goal_id = created["goal"]["goalId"]
    json.loads(tools["complete_goal"](tool_context=_context()))  # type: ignore[operator]

    flow = store.record_run_fact(
        session_id="session-1",
        run_id="run-failed",
        status="failed",
        invocation_id="invocation-1",
    )
    goal = store.get_goal(goal_id)

    assert goal is not None and goal.status == "active"
    assert flow is not None
    assert "pendingCompletion" not in flow.recovery_state
    assert flow.recovery_state["lastCompletionRequest"]["status"] == "rejected"


def test_completed_run_can_be_cited_as_persisted_evidence(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    tools = _tools(store)
    created = json.loads(
        tools["create_goal"]("Use earlier evidence", tool_context=_context())  # type: ignore[operator]
    )
    goal_id = created["goal"]["goalId"]
    store.record_run_fact(
        session_id="session-1",
        run_id="run-complete",
        status="completed",
        invocation_id="invocation-earlier",
    )

    completed = json.loads(
        tools["complete_goal"](  # type: ignore[operator]
            [{"type": "run", "ref": "run-complete"}],
            tool_context=_context(),
        )
    )

    assert completed["ok"] is True
    assert store.get_goal(goal_id).status == "completed"  # type: ignore[union-attr]
