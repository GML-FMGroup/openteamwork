"""Durable Goal and TaskFlow fact-store tests."""

from __future__ import annotations

import pytest

from openppx.runtime.goal_store import (
    GoalActiveExistsError,
    GoalConflictError,
    GoalStateError,
    GoalStore,
)


def test_create_goal_is_atomic_idempotent_and_session_unique(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")

    goal, flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Ship a verified release",
        completion_criteria=["All tests pass", "Release artifact exists"],
        constraints=["Do not publish without approval"],
        idempotency_key="req-create-1",
        created_by="local:user",
        correlation_id="corr-1",
    )
    replayed_goal, replayed_flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Ship a verified release",
        completion_criteria=["All tests pass", "Release artifact exists"],
        constraints=["Do not publish without approval"],
        idempotency_key="req-create-1",
        created_by="local:user",
        correlation_id="corr-1",
    )

    assert goal.goal_id.startswith("goal_")
    assert flow.flow_id == goal.active_flow_id
    assert flow.goal_id == goal.goal_id
    assert flow.steps == []
    assert replayed_goal.goal_id == goal.goal_id
    assert replayed_flow.flow_id == flow.flow_id
    assert [event.event_type for event in store.list_events(goal.goal_id)] == ["goal.created"]
    with pytest.raises(GoalActiveExistsError):
        store.create_goal(
            session_id="session-1",
            agent_id="main",
            user_id="local:user",
            objective="A second unfinished goal",
            created_by="local:user",
        )


def test_goal_updates_are_revision_safe_and_completion_requires_evidence(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Prepare the report",
        created_by="local:user",
    )

    updated = store.update_goal(
        goal.goal_id,
        objective="Prepare and verify the report",
        completion_criteria=["report.pdf is attached"],
        expected_revision=goal.revision,
        actor_id="local:user",
    )

    assert updated.revision == goal.revision + 1
    assert updated.objective == "Prepare and verify the report"
    with pytest.raises(GoalConflictError):
        store.update_goal(
            goal.goal_id,
            objective="stale update",
            expected_revision=goal.revision,
            actor_id="local:user",
        )
    with pytest.raises(GoalStateError, match="evidence"):
        store.transition_goal(
            goal.goal_id,
            status="completed",
            expected_revision=updated.revision,
            actor_id="local:user",
        )

    completed = store.transition_goal(
        goal.goal_id,
        status="completed",
        completion_evidence=[
            {
                "type": "artifact",
                "ref": "report.pdf@2",
                "label": "Verified report",
            }
        ],
        expected_revision=updated.revision,
        actor_id="local:user",
    )

    assert completed.status == "completed"
    assert completed.completion_evidence[0]["ref"] == "report.pdf@2"
    assert completed.completed_at_ms is not None


def test_task_flow_validates_dependencies_and_binds_execution_facts(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Build and test",
        created_by="local:user",
    )
    planned = store.update_flow(
        flow.flow_id,
        steps=[
            {
                "stepId": "implement",
                "title": "Implement",
                "status": "pending",
                "dependsOn": [],
                "expectedOutcome": "Code exists",
                "completionCriteria": ["Implementation tests pass"],
            },
            {
                "stepId": "verify",
                "title": "Verify",
                "status": "pending",
                "dependsOn": ["implement"],
                "expectedOutcome": "Full verification passes",
                "completionCriteria": ["verify.py passes"],
            },
        ],
        expected_revision=flow.revision,
        actor_id="local:user",
    )

    with pytest.raises(GoalStateError, match="dependencies"):
        store.advance_flow_step(
            planned.flow_id,
            step_id="verify",
            status="running",
            expected_revision=planned.revision,
            actor_id="local:user",
        )
    implement_running = store.advance_flow_step(
        planned.flow_id,
        step_id="implement",
        status="running",
        expected_revision=planned.revision,
        actor_id="local:user",
    )
    bound = store.bind_task(
        implement_running.flow_id,
        step_id="implement",
        task_id="task-1",
        expected_revision=implement_running.revision,
        actor_id="local:user",
    )
    implement_done = store.advance_flow_step(
        bound.flow_id,
        step_id="implement",
        status="completed",
        expected_revision=bound.revision,
        actor_id="local:user",
    )
    verify_done = store.advance_flow_step(
        implement_done.flow_id,
        step_id="verify",
        status="completed",
        expected_revision=implement_done.revision,
        actor_id="local:user",
    )
    finished = store.finish_flow(
        verify_done.flow_id,
        expected_revision=verify_done.revision,
        actor_id="local:user",
    )

    assert bound.task_run_refs == [{"stepId": "implement", "taskId": "task-1"}]
    assert finished.status == "completed"
    assert store.get_goal(goal.goal_id).active_flow_id == finished.flow_id  # type: ignore[union-attr]


def test_goal_pause_resume_cancel_state_machine_is_explicit(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Long work",
        created_by="local:user",
    )

    paused = store.transition_goal(
        goal.goal_id,
        status="paused",
        expected_revision=goal.revision,
        actor_id="local:user",
    )
    resumed = store.transition_goal(
        goal.goal_id,
        status="active",
        expected_revision=paused.revision,
        actor_id="local:user",
    )
    cancelled = store.transition_goal(
        goal.goal_id,
        status="cancelled",
        expected_revision=resumed.revision,
        actor_id="local:user",
    )

    assert [paused.status, resumed.status, cancelled.status] == ["paused", "active", "cancelled"]
    with pytest.raises(GoalStateError):
        store.transition_goal(
            goal.goal_id,
            status="active",
            expected_revision=cancelled.revision,
            actor_id="local:user",
        )


def test_advancing_waiting_flow_restores_active_state(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    _goal, flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Wait and continue",
        created_by="local:user",
    )
    planned = store.update_flow(
        flow.flow_id,
        steps=[{"stepId": "one", "title": "One", "status": "pending"}],
        expected_revision=flow.revision,
        actor_id="local:user",
    )
    waiting = store.advance_flow_step(
        planned.flow_id,
        step_id="one",
        status="waiting",
        expected_revision=planned.revision,
        actor_id="local:user",
    )
    resumed = store.advance_flow_step(
        waiting.flow_id,
        step_id="one",
        status="running",
        expected_revision=waiting.revision,
        actor_id="local:user",
    )

    assert waiting.status == "waiting"
    assert resumed.status == "active"


def test_goal_history_returns_latest_window_in_chronological_order(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Track recent history",
        created_by="local:user",
    )
    first = store.update_goal(
        goal.goal_id,
        objective="First update",
        expected_revision=goal.revision,
        actor_id="local:user",
    )
    store.update_goal(
        goal.goal_id,
        objective="Second update",
        expected_revision=first.revision,
        actor_id="local:user",
    )

    events = store.list_events(goal.goal_id, limit=2)

    assert [event.event_type for event in events] == ["goal.updated", "goal.updated"]
    assert events[0].event_id < events[1].event_id
    assert all(event.event_type != "goal.created" for event in events)


def test_runtime_and_artifact_facts_attach_without_completing_goal(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Produce a report",
        created_by="local:user",
    )

    running = store.record_run_fact(
        session_id="session-1",
        run_id="run-1",
        status="running",
        snapshot={"modelProfileId": "primary"},
    )
    with_artifact = store.record_artifact_fact(
        session_id="session-1",
        run_id="run-1",
        artifact_ref="reports/final.pdf",
        version=2,
    )
    completed_run = store.record_run_fact(
        session_id="session-1",
        run_id="run-1",
        status="completed",
    )

    assert running is not None and running.recovery_state["currentRunId"] == "run-1"
    assert with_artifact is not None and with_artifact.artifact_refs[0]["version"] == 2
    assert completed_run is not None and completed_run.recovery_state["latestRunStatus"] == "completed"
    assert completed_run.recovery_state["currentRunId"] == ""
    assert store.get_goal(goal.goal_id).status == "active"  # type: ignore[union-attr]
    assert flow.flow_id == completed_run.flow_id
