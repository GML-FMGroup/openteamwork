"""ADK-native Automation execution and policy tests."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from openppx.automation import AutomationService, AutomationStore
from openppx.runtime.task_store import TaskStore


class _ConfigRepository:
    def __init__(self, workspace: str) -> None:
        self.workspace = workspace

    def read_agent(self, agent_id: str):
        spec = SimpleNamespace(
            workspace=self.workspace,
            model_policy=SimpleNamespace(default_profile="primary"),
        )
        return SimpleNamespace(revision="agent-r1", document=SimpleNamespace(spec=spec))

    def read_node(self):
        return SimpleNamespace(document=SimpleNamespace(spec=SimpleNamespace(enabled_agents=["main"])))


class _Profiles:
    def read_profile(self, profile_id: str):
        return SimpleNamespace(revision="model-r1", profile_id=profile_id)


class _Supervisor:
    def __init__(self, outputs: list[object] | None = None) -> None:
        self.outputs = list(outputs or ["completed output"])
        self.sessions: dict[tuple[str, str, str], object] = {}
        self.prompts: list[str] = []
        self.assembler = SimpleNamespace(
            extension_snapshot_for_agent=lambda _agent_id: SimpleNamespace(revision="extensions-r1")
        )

    def runtime_for(self, _agent_id: str):
        return SimpleNamespace(metadata=SimpleNamespace(model_profile_revision="model-r1", extension_revision="extensions-r1"))

    async def get_session(self, agent_id: str, *, user_id: str, session_id: str):
        return self.sessions.get((agent_id, user_id, session_id))

    async def create_session(self, agent_id: str, *, user_id: str, session_id: str):
        session = SimpleNamespace(events=[])
        self.sessions[(agent_id, user_id, session_id)] = session
        return session

    async def hello(self, agent_id: str, _text: str, *, user_id: str, session_id: str) -> str:
        self.prompts.append(_text)
        value = self.outputs.pop(0)
        if isinstance(value, BaseException):
            raise value
        session = self.sessions[(agent_id, user_id, session_id)]
        session.events.append(SimpleNamespace(invocation_id=f"invocation-{len(session.events) + 1}"))
        return str(value)


class _Cron:
    def __init__(self) -> None:
        self.jobs: dict[str, object] = {}
        self.sequence = 0

    def add_job(self, **kwargs):
        self.sequence += 1
        job = SimpleNamespace(
            id=f"cron-{self.sequence}",
            schedule=kwargs.get("schedule"),
            enabled=True,
            state=SimpleNamespace(next_run_at_ms=1_700_000_000_000, last_run_at_ms=None),
            payload=SimpleNamespace(**{key: kwargs.get(key) for key in ("source_kind", "source_id", "source_revision")}),
        )
        self.jobs[job.id] = job
        return job

    def update_job(self, job_id: str, **_kwargs):
        job = self.jobs.get(job_id)
        if job is not None:
            job.payload.source_revision = _kwargs.get(
                "source_revision", job.payload.source_revision
            )
        return job

    def enable_job(self, job_id: str, *, enabled: bool):
        job = self.jobs.get(job_id)
        if job is not None:
            job.enabled = enabled
        return job

    def remove_job(self, job_id: str):
        return self.jobs.pop(job_id, None) is not None

    def list_jobs(self, *, include_disabled: bool = False):
        del include_disabled
        return list(self.jobs.values())


class _OperationsRuntime:
    def __init__(self) -> None:
        self.submitted = []

    def submit(self, operation):
        self.submitted.append(operation)


def _service(tmp_path, *, outputs: list[object] | None = None) -> AutomationService:
    task_store = TaskStore(db_path=tmp_path / "tasks.db")
    return AutomationService(
        node_root=tmp_path,
        store=AutomationStore(tmp_path / "automations.db"),
        config_repository=_ConfigRepository(str(tmp_path)),  # type: ignore[arg-type]
        profile_repository=_Profiles(),  # type: ignore[arg-type]
        supervisor=_Supervisor(outputs),  # type: ignore[arg-type]
        cron=_Cron(),  # type: ignore[arg-type]
        task_store=task_store,
        operations_runtime=_OperationsRuntime(),
    )


def _create(service: AutomationService, **overrides):
    data = {
        "user_id": "local:user",
        "agent_id": "main",
        "name": "Morning brief",
        "description": "",
        "instructions": "Summarize the day.",
        "output_requirements": ["Be concise"],
        "workspace_ref": str(service.node_root),
        "context_mode": "isolated",
        "model_profile_ref": "primary",
        "extension_policy": {},
        "permission_policy": {},
        "permissions_confirmed": True,
        "delivery_policy": {"type": "session"},
        "concurrency_policy": {"mode": "skip", "limit": 1},
        "missed_run_policy": {"mode": "run-latest", "maxCatchUp": 1},
        "retry_policy": {"maxAttempts": 1, "backoffSeconds": 0},
        "budget_policy": {"timeoutSeconds": 10},
        "monitor_policy": {},
        "schedule": None,
        "local_event": None,
    }
    data.update(overrides)
    return service.create_definition(
        actor_id="local:user",
        request_id=f"request-{data['name']}",
        correlation_id="corr-create",
        **data,
    )


def test_run_executes_through_independent_adk_session_and_records_delivery(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(service)
    run = service.run_now(
        definition,
        actor_id="local:user",
        correlation_id="corr-run",
        input_snapshot={"topic": "today"},
    )

    completed = asyncio.run(service._execute_run(run.automation_run_id))

    assert completed.status == "completed"
    assert completed.adk_run_id == "invocation-1"
    assert completed.output_summary == "completed output"
    assert completed.delivery_refs[0]["status"] == "delivered"
    assert completed.session_id.startswith("automation-arun_")
    assert completed.principal_snapshot["principalId"] == f"automation:{definition.automation_id}"


def test_retry_is_bounded_and_attempt_is_persisted(tmp_path) -> None:
    service = _service(tmp_path, outputs=[RuntimeError("transient secret"), "recovered"])
    definition = _create(
        service,
        retry_policy={"maxAttempts": 2, "backoffSeconds": 0},
    )
    run = service.run_now(definition, actor_id="local:user", correlation_id="corr-run", input_snapshot={})

    completed = asyncio.run(service._execute_run(run.automation_run_id))

    assert completed.status == "completed"
    assert completed.attempt == 2
    events = service.task_events.list_events(completed.task_run_refs[0]["taskId"])
    assert [event.event_type for event in events].count("automation.attempt.failed") == 1


def test_monitor_completes_quietly_when_observation_did_not_change(tmp_path) -> None:
    service = _service(tmp_path, outputs=["same observation", "same observation"])
    definition = _create(
        service,
        monitor_policy={"enabled": True, "notifyOnChangeOnly": True},
    )
    first = service.run_now(definition, actor_id="local:user", correlation_id="corr-1", input_snapshot={})
    first_completed = asyncio.run(service._execute_run(first.automation_run_id))
    second = service.run_now(definition, actor_id="local:user", correlation_id="corr-2", input_snapshot={})
    second_completed = asyncio.run(service._execute_run(second.automation_run_id))

    assert first_completed.output_summary == "same observation"
    assert second_completed.output_summary == "No change detected."
    assert second_completed.delivery_refs == []


def test_deleting_definition_removes_derived_scheduler_job(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(
        service,
        schedule={"kind": "every", "everySeconds": 3600, "timezone": ""},
    )
    cron = service.cron
    assert definition.scheduler_job_id in cron.jobs

    deleted = service.transition(
        definition,
        status="deleted",
        expected_revision=definition.revision,
        actor_id="local:user",
        correlation_id="corr-delete",
    )

    assert deleted.scheduler_job_id == ""
    assert cron.jobs == {}


def test_reconcile_preserves_due_occurrence_for_unchanged_definition(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(
        service,
        schedule={"kind": "every", "everySeconds": 3600, "timezone": ""},
    )
    job = service.cron.jobs[definition.scheduler_job_id]
    due_at = 1_600_000_000_000
    job.state.next_run_at_ms = due_at

    asyncio.run(service.reconcile_runtime())

    assert service.cron.jobs[definition.scheduler_job_id].state.next_run_at_ms == due_at


def test_update_can_remove_schedule_without_deleting_automation(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(
        service,
        schedule={"kind": "every", "everySeconds": 3600, "timezone": ""},
    )
    assert definition.scheduler_job_id

    updated = service.update_definition(
        definition,
        actor_id="local:user",
        correlation_id="corr-update",
        expected_revision=definition.revision,
        schedule=None,
    )

    assert service.store.trigger_for(updated.automation_id) is None
    assert updated.scheduler_job_id == ""
    assert service.cron.jobs == {}


def test_local_event_requires_declared_key_and_validates_typed_payload(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(
        service,
        local_event={
            "eventKey": "source-changed",
            "inputSchema": {
                "type": "object",
                "properties": {"path": {"type": "string", "maxLength": 32}},
                "required": ["path"],
                "additionalProperties": False,
            },
        },
    )

    run = service.trigger_local_event(
        definition,
        actor_id="local:user",
        correlation_id="corr-event",
        event_key="source-changed",
        event_id="event-1",
        input_snapshot={"path": "report.md"},
    )

    assert run.trigger_type == "local_event"
    assert run.input_snapshot == {"eventKey": "source-changed", "payload": {"path": "report.md"}}
    assert len(service.operations_runtime.submitted) == 1

    with pytest.raises(Exception, match="does not match"):
        service.trigger_local_event(
            definition,
            actor_id="local:user",
            correlation_id="corr-wrong-key",
            event_key="other-event",
            event_id="event-2",
            input_snapshot={"path": "report.md"},
        )
    with pytest.raises(Exception, match="missing required field"):
        service.trigger_local_event(
            definition,
            actor_id="local:user",
            correlation_id="corr-invalid-input",
            event_key="source-changed",
            event_id="event-3",
            input_snapshot={},
        )


def test_local_event_occurrence_is_idempotent(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(
        service,
        local_event={
            "eventKey": "source-changed",
            "inputSchema": {"type": "object", "properties": {}, "additionalProperties": True},
        },
    )

    first = service.trigger_local_event(
        definition,
        actor_id="local:user",
        correlation_id="corr-first",
        event_key="source-changed",
        event_id="event-1",
        input_snapshot={"revision": 1},
    )
    repeated = service.trigger_local_event(
        definition,
        actor_id="local:user",
        correlation_id="corr-repeat",
        event_key="source-changed",
        event_id="event-1",
        input_snapshot={"revision": 2},
    )

    assert repeated.automation_run_id == first.automation_run_id
    assert len(service.operations_runtime.submitted) == 1


def test_manual_request_is_idempotent_without_allocating_duplicate_task(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(service)

    first = service.run_now(
        definition,
        actor_id="local:user",
        correlation_id="corr-first",
        input_snapshot={},
        request_id="request-1",
    )
    repeated = service.run_now(
        definition,
        actor_id="local:user",
        correlation_id="corr-repeat",
        input_snapshot={"ignored": True},
        request_id="request-1",
    )

    assert repeated.automation_run_id == first.automation_run_id
    assert len(service.task_store.list_tasks(limit=20)) == 1
    assert len(service.operations_runtime.submitted) == 1


def test_queue_one_keeps_exactly_one_successor_and_drains_it(tmp_path) -> None:
    service = _service(tmp_path, outputs=["first", "second"])
    definition = _create(service, concurrency_policy={"mode": "queue-one", "limit": 1})

    first = service.run_now(definition, actor_id="local:user", correlation_id="c1", input_snapshot={})
    second = service.run_now(definition, actor_id="local:user", correlation_id="c2", input_snapshot={})
    third = service.run_now(definition, actor_id="local:user", correlation_id="c3", input_snapshot={})

    assert first.status == "queued"
    assert second.status == "queued"
    assert third.status == "skipped"
    assert len(service.operations_runtime.submitted) == 1

    asyncio.run(service._execute_run(first.automation_run_id))

    assert service.store.read_run(first.automation_run_id).status == "completed"
    assert service.store.read_run(second.automation_run_id).status == "completed"


def test_run_uses_frozen_definition_after_definition_is_edited(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(service, instructions="Original instructions")
    run = service.run_now(definition, actor_id="local:user", correlation_id="c1", input_snapshot={})
    service.update_definition(
        definition,
        actor_id="local:user",
        correlation_id="update",
        expected_revision=definition.revision,
        instructions="Changed instructions",
    )

    asyncio.run(service._execute_run(run.automation_run_id))

    assert "Original instructions" in service.supervisor.prompts[0]
    assert "Changed instructions" not in service.supervisor.prompts[0]


def test_delivery_failure_does_not_rewrite_successful_execution(tmp_path, monkeypatch) -> None:
    service = _service(tmp_path)
    definition = _create(service)
    run = service.run_now(definition, actor_id="local:user", correlation_id="c1", input_snapshot={})

    def fail_delivery(_delivery_key):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(service.delivery_store, "mark_delivered", fail_delivery)
    completed = asyncio.run(service._execute_run(run.automation_run_id))

    assert completed.status == "completed"
    assert completed.delivery_refs[0]["status"] == "failed"


def test_missed_schedule_skip_policy_records_skipped_run(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(
        service,
        schedule={"kind": "every", "everySeconds": 10, "timezone": ""},
        missed_run_policy={"mode": "skip", "maxCatchUp": 1},
    )
    job = service.cron.jobs[definition.scheduler_job_id]
    job.state.next_run_at_ms = int(__import__("time").time() * 1000) - 60_000

    result = asyncio.run(service.run_scheduled(job))

    assert result == "skipped"
    assert service.store.list_runs(definition.automation_id)[0].status == "skipped"


def test_missed_schedule_bounded_catch_up_runs_only_latest_occurrences(tmp_path) -> None:
    service = _service(tmp_path, outputs=["catch-up-1", "catch-up-2", "catch-up-3"])
    definition = _create(
        service,
        schedule={"kind": "every", "everySeconds": 10, "timezone": ""},
        missed_run_policy={"mode": "bounded-catch-up", "maxCatchUp": 3},
    )
    job = service.cron.jobs[definition.scheduler_job_id]
    job.state.next_run_at_ms = int(__import__("time").time() * 1000) - 95_000

    result = asyncio.run(service.run_scheduled(job))

    runs = service.store.list_runs(definition.automation_id)
    assert result == "catch-up-3"
    assert len(runs) == 3
    assert all(run.status == "completed" for run in runs)
    assert all(run.input_snapshot["catchUpCount"] == 3 for run in runs)


def test_reconcile_blocks_interrupted_running_run(tmp_path) -> None:
    service = _service(tmp_path)
    definition = _create(service)
    run = service.run_now(definition, actor_id="local:user", correlation_id="c1", input_snapshot={})
    service.store.update_run(run.automation_run_id, status="running")

    asyncio.run(service.reconcile_runtime())

    recovered = service.store.read_run(run.automation_run_id)
    assert recovered.status == "blocked"
    assert "restarted" in recovered.blocked_reason
