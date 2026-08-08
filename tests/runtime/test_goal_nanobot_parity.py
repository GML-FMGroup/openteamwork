"""Deterministic Goal contract covering nanobot parity and OpenPPX advances.

The matrix intentionally avoids a live model. It verifies the product facts
that must remain true regardless of provider: session-scoped sustained goals,
bounded/recoverable execution, restart recovery, and evidence-based completion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from openppx.runtime.goal_store import GoalStateError, GoalStore


def _create_goal(store: GoalStore, *, session_id: str = "session-parity"):
    """Create the shared deterministic Goal fixture used by the contract."""
    return store.create_goal(
        session_id=session_id,
        agent_id="main",
        user_id="local:user",
        objective="Research, verify, and publish a report",
        completion_criteria=["Sources verified", "Report published"],
        budget_policy={
            "maxContinuations": 4,
            "maxLlmCallsPerInvocation": 12,
            "maxRepeatedActionContinuations": 2,
        },
        created_by="local:user",
    )


def test_parity_goal_is_session_scoped_durable_and_runtime_managed(tmp_path: Path) -> None:
    """OpenPPX preserves nanobot's sustained Goal while adding durable TaskFlow facts."""
    database = tmp_path / "goals.db"
    store = GoalStore(db_path=database)
    goal, flow = _create_goal(store)

    reopened = GoalStore(db_path=database)
    current = reopened.current_goal(goal.session_id)
    persisted_flow = reopened.flow_for_goal(goal.goal_id)

    assert current is not None
    assert current.goal_id == goal.goal_id
    assert current.objective == "Research, verify, and publish a report"
    assert current.budget_policy["maxContinuations"] == 4
    assert persisted_flow is not None
    assert persisted_flow.flow_id == flow.flow_id
    assert len(persisted_flow.steps) == 1
    runtime_step = persisted_flow.steps[0]
    assert runtime_step == {
        "stepId": "goal-execution",
        "title": "Research, verify, and publish a report",
        "expectedOutcome": "Research, verify, and publish a report",
        "status": "pending",
        "dependsOn": [],
        "managedBy": "runtime",
        "completionCriteria": ["Sources verified", "Report published"],
    }
    assert reopened.current_goal("another-session") is None


def test_parity_goal_restart_turns_orphaned_run_into_resumable_wait(tmp_path: Path) -> None:
    """A Node restart keeps the Goal and exposes an explicit retryable wait state."""
    database = tmp_path / "goals.db"
    store = GoalStore(db_path=database)
    goal, _flow = _create_goal(store)
    store.record_run_fact(
        session_id=goal.session_id,
        run_id="run-before-restart",
        status="running",
    )

    restarted = GoalStore(db_path=database)
    reconciled = restarted.reconcile_runtime(active_run_ids=())
    recovered = restarted.get_goal(goal.goal_id)
    recovered_flow = restarted.flow_for_goal(goal.goal_id)

    assert [item.goal_id for item in reconciled] == [goal.goal_id]
    assert recovered is not None and recovered.status == "waiting"
    assert recovered_flow is not None and recovered_flow.status == "waiting"
    assert recovered_flow.wait_reason["kind"] == "runtime_restart"
    assert recovered_flow.wait_reason["canRetry"] is True
    assert recovered_flow.recovery_state["orphanedRunId"] == "run-before-restart"


def test_parity_goal_blocks_repeated_actions_and_retries_same_step(tmp_path: Path) -> None:
    """Identical no-progress slices stop deterministically and remain recoverable."""
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = _create_goal(store)
    before = store.progress_signature(goal.goal_id)
    store.record_progress_observation(
        session_id=goal.session_id,
        run_id="run-loop",
        continuation_index=0,
        before_signature=before,
        action_fingerprint="same-action",
        action_names=["web_search"],
        max_no_progress=3,
        max_repeated_actions=2,
    )
    before = store.progress_signature(goal.goal_id)
    blocked = store.record_progress_observation(
        session_id=goal.session_id,
        run_id="run-loop",
        continuation_index=1,
        before_signature=before,
        action_fingerprint="same-action",
        action_names=["web_search"],
        max_no_progress=3,
        max_repeated_actions=2,
    )

    assert blocked is not None and blocked.status == "blocked"
    flow = store.flow_for_goal(goal.goal_id)
    assert flow is not None
    assert flow.wait_reason["kind"] == "repeated_actions"
    assert flow.wait_reason["stepId"] == "goal-execution"

    retried = store.retry_blocked_step(
        goal.goal_id,
        step_id="goal-execution",
        expected_revision=blocked.revision,
        actor_id="local:user",
    )
    retried_flow = store.flow_for_goal(goal.goal_id)

    assert retried.status == "active"
    assert retried_flow is not None and retried_flow.status == "active"
    assert retried_flow.wait_reason == {}
    assert retried_flow.steps[0]["status"] == "running"


def test_parity_goal_completion_requires_evidence_and_successful_invocation(
    tmp_path: Path,
) -> None:
    """Completion requires exact evidence and the same successful ADK invocation."""
    store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = _create_goal(store)
    partial_evidence = [
        {
            "type": "source_set",
            "ref": "sources-v1",
            "criteria": ["Sources verified"],
        }
    ]
    with pytest.raises(GoalStateError, match="missing completion evidence"):
        store.request_completion(
            goal.goal_id,
            expected_revision=goal.revision,
            actor_id="agent:main",
            invocation_id="invocation-target",
            completion_evidence=partial_evidence,
        )

    complete_evidence = [
        *partial_evidence,
        {
            "type": "artifact",
            "ref": "report.docx",
            "criteria": ["Report published"],
        },
    ]
    store.request_completion(
        goal.goal_id,
        expected_revision=goal.revision,
        actor_id="agent:main",
        invocation_id="invocation-target",
        completion_evidence=complete_evidence,
    )
    store.record_run_fact(
        session_id=goal.session_id,
        run_id="run-other",
        status="completed",
        invocation_id="invocation-other",
    )
    assert store.get_goal(goal.goal_id).status == "active"  # type: ignore[union-attr]

    store.record_run_fact(
        session_id=goal.session_id,
        run_id="run-target",
        status="completed",
        invocation_id="invocation-target",
    )
    completed = store.get_goal(goal.goal_id)

    assert completed is not None and completed.status == "completed"
    assert completed.completion_evidence[:2] == complete_evidence
    assert completed.completion_evidence[-1] == {
        "type": "task_run",
        "ref": "run-target",
        "label": "Completed ADK Run",
        "invocationId": "invocation-target",
    }
