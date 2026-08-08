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
    assert flow.steps[0]["managedBy"] == "runtime"
    assert flow.steps[0]["status"] == "pending"
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
                "criteria": ["report.pdf is attached"],
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
    assert store.flow_for_goal(goal.goal_id).status == "cancelled"  # type: ignore[union-attr]
    with pytest.raises(GoalStateError):
        store.transition_goal(
            goal.goal_id,
            status="active",
            expected_revision=cancelled.revision,
            actor_id="local:user",
        )


def test_goal_start_failure_blocks_goal_and_flow(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Long work",
        created_by="local:user",
    )

    blocked = store.block_current_goal("session-1", reason="provider unavailable")

    assert blocked is not None
    assert blocked.status == "blocked"
    flow = store.flow_for_goal(goal.goal_id)
    assert flow is not None
    assert flow.status == "blocked"
    assert flow.wait_reason == {"kind": "blocked", "message": "provider unavailable"}


def test_runtime_wait_and_pause_leave_goal_resumable_with_reason(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Long work",
        created_by="local:user",
    )

    waiting = store.wait_current_goal("session-1", reason="continuation budget exhausted")
    assert waiting is not None and waiting.status == "waiting"
    waiting_flow = store.flow_for_goal(goal.goal_id)
    assert waiting_flow is not None
    assert waiting_flow.wait_reason["message"] == "continuation budget exhausted"

    resumed = store.transition_goal(
        goal.goal_id,
        status="active",
        expected_revision=waiting.revision,
        actor_id="local:user",
    )
    assert store.flow_for_goal(goal.goal_id).wait_reason == {}  # type: ignore[union-attr]

    paused = store.pause_current_goal("session-1", reason="run stopped")
    assert paused is not None and paused.status == "paused"
    paused_flow = store.flow_for_goal(goal.goal_id)
    assert paused_flow is not None
    assert paused_flow.wait_reason == {"kind": "paused", "message": "run stopped"}


def test_runtime_reconciliation_moves_orphaned_active_goal_to_waiting(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-orphaned",
        agent_id="main",
        user_id="local:user",
        objective="Continue after a Node restart",
        created_by="local:user",
    )
    store.record_run_fact(
        session_id=goal.session_id,
        run_id="run-finished-before-restart",
        status="completed",
    )

    reconciled = store.reconcile_runtime()

    assert [item.goal_id for item in reconciled] == [goal.goal_id]
    current = store.current_goal(goal.session_id)
    assert current is not None and current.status == "waiting"
    flow = store.flow_for_goal(goal.goal_id)
    assert flow is not None
    assert flow.status == "waiting"
    assert "Node restarted" in flow.wait_reason["message"]


def test_runtime_reconciliation_moves_persisted_process_owned_run_to_waiting(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-running",
        agent_id="main",
        user_id="local:user",
        objective="Keep the active Run",
        created_by="local:user",
    )
    store.record_run_fact(
        session_id=goal.session_id,
        run_id="run-active",
        status="running",
    )

    reconciled = store.reconcile_runtime()

    assert [item.goal_id for item in reconciled] == [goal.goal_id]
    restored = store.get_goal(goal.goal_id)
    assert restored is not None and restored.status == "waiting"
    flow = store.flow_for_goal(goal.goal_id)
    assert flow is not None
    assert flow.recovery_state["orphanedRunId"] == "run-active"
    assert flow.recovery_state["currentRunId"] == ""


def test_goal_creation_owns_one_runtime_managed_execution_step(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")

    goal, flow = store.create_goal(
        session_id="session-managed",
        agent_id="main",
        user_id="local:user",
        objective="Produce a verified report",
        completion_criteria=["Final report exists"],
        created_by="local:user",
    )

    assert flow.steps == [
        {
            "stepId": "goal-execution",
            "title": "Produce a verified report",
            "status": "pending",
            "dependsOn": [],
            "expectedOutcome": "Produce a verified report",
            "completionCriteria": ["Final report exists"],
            "managedBy": "runtime",
        }
    ]
    assert store.get_goal(goal.goal_id) is not None


def test_retry_blocked_step_resets_supervisor_and_restores_active_goal(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, flow = store.create_goal(
        session_id="session-retry",
        agent_id="main",
        user_id="local:user",
        objective="Finish reliable work",
        created_by="local:user",
    )
    running = store.advance_flow_step(
        flow.flow_id,
        step_id="goal-execution",
        status="running",
        expected_revision=flow.revision,
        actor_id="system:test",
    )
    blocked_flow = store.advance_flow_step(
        running.flow_id,
        step_id="goal-execution",
        status="blocked",
        expected_revision=running.revision,
        actor_id="system:test",
    )
    blocked_goal = store.transition_goal(
        goal.goal_id,
        status="blocked",
        expected_revision=goal.revision,
        actor_id="system:test",
        reason="Repeated the same action without progress.",
    )

    retried = store.retry_blocked_step(
        goal.goal_id,
        step_id="goal-execution",
        expected_revision=blocked_goal.revision,
        actor_id="local:user",
    )

    assert retried.status == "active"
    retried_flow = store.get_flow(blocked_flow.flow_id)
    assert retried_flow is not None
    assert retried_flow.status == "active"
    assert retried_flow.steps[0]["status"] == "running"
    assert retried_flow.wait_reason == {}
    assert retried_flow.recovery_state["supervisor"]["noProgressCount"] == 0
    assert store.list_events(goal.goal_id)[-1].event_type == "goal.step.retry_requested"


def test_repeated_goal_actions_are_blocked_with_retryable_reason(tmp_path) -> None:
    """Consecutive identical ADK action slices must not spin forever."""
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-loop",
        agent_id="main",
        user_id="local:user",
        objective="Research and write a report",
        budget_policy={"maxRepeatedActionContinuations": 2},
        created_by="local:user",
    )
    before = store.progress_signature(goal.goal_id)

    first = store.record_progress_observation(
        session_id=goal.session_id,
        run_id="run-loop",
        continuation_index=0,
        before_signature=before,
        action_fingerprint="same-search",
        action_names=["web_search"],
        max_no_progress=3,
        max_repeated_actions=2,
    )
    second_before = store.progress_signature(goal.goal_id)
    second = store.record_progress_observation(
        session_id=goal.session_id,
        run_id="run-loop",
        continuation_index=1,
        before_signature=second_before,
        action_fingerprint="same-search",
        action_names=["web_search"],
        max_no_progress=3,
        max_repeated_actions=2,
    )

    assert first is not None and first.status == "active"
    assert second is not None and second.status == "blocked"
    blocked_flow = store.flow_for_goal(goal.goal_id)
    assert blocked_flow is not None and blocked_flow.status == "blocked"
    assert blocked_flow.wait_reason == {
        "kind": "repeated_actions",
        "message": "The Goal repeated the same actions without durable progress.",
        "stepId": "goal-execution",
        "actions": ["web_search"],
        "canRetry": True,
    }
    assert blocked_flow.steps[0]["status"] == "blocked"
    supervisor = blocked_flow.recovery_state["supervisor"]
    assert supervisor["repeatedActionCount"] == 2
    assert supervisor["noProgressCount"] == 2
    assert store.list_events(goal.goal_id)[-1].event_type == "goal.progress.blocked"


def test_new_goal_action_resets_repetition_counter(tmp_path) -> None:
    """A changed ADK action signature is evidence that the loop evolved."""
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-evolving",
        agent_id="main",
        user_id="local:user",
        objective="Research and write a report",
        created_by="local:user",
    )

    before = store.progress_signature(goal.goal_id)
    store.record_progress_observation(
        session_id=goal.session_id,
        run_id="run-evolving",
        continuation_index=0,
        before_signature=before,
        action_fingerprint="search-a",
        action_names=["web_search"],
        max_no_progress=2,
        max_repeated_actions=2,
    )
    before = store.progress_signature(goal.goal_id)
    current = store.record_progress_observation(
        session_id=goal.session_id,
        run_id="run-evolving",
        continuation_index=1,
        before_signature=before,
        action_fingerprint="fetch-b",
        action_names=["web_fetch"],
        max_no_progress=2,
        max_repeated_actions=2,
    )

    assert current is not None and current.status == "active"
    flow = store.flow_for_goal(goal.goal_id)
    assert flow is not None
    assert flow.recovery_state["supervisor"]["repeatedActionCount"] == 1
    assert flow.recovery_state["supervisor"]["noProgressCount"] == 1


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


def test_explicit_completion_request_is_reconciled_by_matching_invocation_only(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-1",
        agent_id="main",
        user_id="local:user",
        objective="Finish one verified run",
        created_by="local:user",
    )
    store.request_completion(
        goal.goal_id,
        expected_revision=goal.revision,
        actor_id="local:user",
        invocation_id="invocation-target",
    )

    store.record_run_fact(
        session_id="session-1",
        run_id="run-other",
        status="completed",
        invocation_id="invocation-other",
    )
    assert store.get_goal(goal.goal_id).status == "active"  # type: ignore[union-attr]

    store.record_run_fact(
        session_id="session-1",
        run_id="run-target",
        status="completed",
        invocation_id="invocation-target",
    )
    assert store.get_goal(goal.goal_id).status == "completed"  # type: ignore[union-attr]


def test_completion_criteria_require_exact_evidence_coverage(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-criteria",
        agent_id="main",
        user_id="local:user",
        objective="Ship a verified artifact",
        completion_criteria=["Tests pass", "Artifact exists"],
        created_by="local:user",
    )

    with pytest.raises(GoalStateError, match="missing completion evidence"):
        store.request_completion(
            goal.goal_id,
            expected_revision=goal.revision,
            actor_id="local:user",
            invocation_id="invocation-missing",
            completion_evidence=[
                {
                    "type": "task_run",
                    "ref": "tests-1",
                    "label": "Tests passed",
                    "criteria": ["Tests pass"],
                }
            ],
        )

    pending = store.request_completion(
        goal.goal_id,
        expected_revision=goal.revision,
        actor_id="local:user",
        invocation_id="invocation-complete",
        completion_evidence=[
            {
                "type": "task_run",
                "ref": "tests-1",
                "label": "Tests passed",
                "criteria": ["Tests pass"],
            },
            {
                "type": "artifact",
                "ref": "release.zip",
                "label": "Release artifact",
                "criteria": ["Artifact exists"],
            },
        ],
    )

    assessment = pending.recovery_state["completionAssessment"]
    assert assessment["satisfiedCriteria"] == ["Tests pass", "Artifact exists"]
    assert assessment["missingCriteria"] == []


def test_goal_continuation_facts_accumulate_budget_and_recovery_state(tmp_path) -> None:
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = store.create_goal(
        session_id="session-continuation",
        agent_id="main",
        user_id="local:user",
        objective="Complete a long task",
        budget_policy={"maxContinuations": 3, "maxLlmCallsPerInvocation": 12},
        created_by="local:user",
    )

    updated = store.record_continuation_fact(
        session_id=goal.session_id,
        run_id="run-long",
        continuation_index=1,
        max_continuations=3,
        max_llm_calls_per_invocation=12,
    )

    assert updated is not None
    assert updated.status == "active"
    assert updated.budget_state["continuationCount"] == 1
    assert updated.budget_state["continuationExhausted"] is False
    flow = store.flow_for_goal(goal.goal_id)
    assert flow is not None
    assert flow.recovery_state["continuations"][0]["runId"] == "run-long"
    assert store.list_events(goal.goal_id)[-1].event_type == "goal.continuation.started"
