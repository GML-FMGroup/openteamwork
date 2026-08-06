"""Durable Automation definition and run fact tests."""

from __future__ import annotations

import pytest

from openppx.automation import AutomationConflictError, AutomationNotFoundError, AutomationStore


def _create(store: AutomationStore, *, key: str = "request-1"):
    return store.create_definition(
        name="Morning brief",
        description="A concise start-of-day brief.",
        instructions="Summarize today's priorities.",
        output_requirements=["Cite sources"],
        agent_id="main",
        user_id="local:user",
        workspace_ref="/workspace",
        context_mode="isolated",
        model_profile_ref="primary",
        extension_policy={},
        permission_policy={"confirmed": True},
        delivery_policy={},
        concurrency_policy={"mode": "skip", "limit": 1},
        missed_run_policy={"mode": "run-latest", "maxCatchUp": 1},
        retry_policy={"maxAttempts": 2, "backoffSeconds": 1},
        budget_policy={"timeoutSeconds": 60},
        monitor_policy={},
        schedule={"kind": "every", "everySeconds": 3600, "timezone": ""},
        idempotency_key=key,
        actor_id="local:user",
        correlation_id="corr-1",
    )


def test_definition_is_idempotent_revision_safe_and_restart_durable(tmp_path) -> None:
    path = tmp_path / "automations.db"
    store = AutomationStore(path)

    definition, trigger = _create(store)
    replayed, replayed_trigger = _create(store)

    assert replayed.automation_id == definition.automation_id
    assert replayed_trigger is not None and trigger is not None
    assert replayed_trigger.trigger_id == trigger.trigger_id
    assert trigger.every_seconds == 3600
    updated = store.update_definition(
        definition.automation_id,
        expected_revision=definition.revision,
        actor_id="local:user",
        correlation_id="corr-2",
        name="Daily brief",
    )
    assert updated.revision == 2
    with pytest.raises(AutomationConflictError):
        store.update_definition(
            definition.automation_id,
            expected_revision=definition.revision,
            actor_id="local:user",
            correlation_id="corr-stale",
            description="stale",
        )

    reopened = AutomationStore(path)
    assert reopened.read_definition(definition.automation_id).name == "Daily brief"
    assert [event.event_type for event in reversed(reopened.history(definition.automation_id))] == [
        "automation.created",
        "automation.updated",
    ]


def test_definition_name_is_unique_per_visible_user(tmp_path) -> None:
    store = AutomationStore(tmp_path / "automations.db")
    _create(store, key="request-1")

    with pytest.raises(AutomationConflictError, match="name"):
        _create(store, key="request-2")


def test_run_occurrence_is_once_only_and_freezes_definition_revision(tmp_path) -> None:
    store = AutomationStore(tmp_path / "automations.db")
    definition, _trigger = _create(store)
    payload = dict(
        definition=definition,
        trigger_type="schedule",
        occurrence_id="schedule:1700000000000",
        started_by=f"automation:{definition.automation_id}",
        input_snapshot={"scheduledForMs": 1700000000000},
        principal_snapshot={"principalId": f"automation:{definition.automation_id}"},
        permission_revision="permission-r1",
        agent_revision="agent-r1",
        model_profile_revision="model-r1",
        extension_snapshot_digest="extensions-r1",
        task_id="task-1",
        correlation_id="corr-run",
    )

    run, created = store.create_run(**payload)
    replayed, replayed_created = store.create_run(**payload)

    assert created is True
    assert replayed_created is False
    assert replayed.automation_run_id == run.automation_run_id
    assert run.definition_revision == definition.revision
    running = store.update_run(run.automation_run_id, status="running")
    completed = store.update_run(running.automation_run_id, status="completed", output_summary="done")
    unchanged = store.update_run(completed.automation_run_id, status="failed", error_summary="late")
    assert completed.status == "completed"
    assert completed.output_summary == "done"
    assert unchanged.status == "completed"


def test_delete_hides_definition_but_keeps_audit_facts(tmp_path) -> None:
    store = AutomationStore(tmp_path / "automations.db")
    definition, _trigger = _create(store)

    deleted = store.set_status(
        definition.automation_id,
        status="deleted",
        expected_revision=definition.revision,
        actor_id="local:user",
        correlation_id="corr-delete",
    )

    assert deleted.status == "deleted"
    with pytest.raises(AutomationNotFoundError):
        store.read_definition(definition.automation_id)
    assert store.read_definition(definition.automation_id, include_deleted=True).status == "deleted"
    assert store.list_definitions(user_id="local:user") == []
    assert store.history(definition.automation_id)[0].event_type == "automation.deleted"
