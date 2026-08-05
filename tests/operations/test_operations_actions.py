"""Node Operations Action and lifecycle integration tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from openppx.config import AgentConfig, FilesystemConfigRepository, InMemorySecretStore, NodeConfig, SecretRef, SecretValue
from openppx.modeling import ModelProfile, ModelProfileRepository
from openppx.operations import NodeOperationsRuntime
from openppx.runtime.node_host import OpenPpxNodeHost
from openppx.runtime.task_store import TaskStore


class _Server:
    def __init__(self, _address, coordinator, *, access_token: str) -> None:
        self.coordinator = coordinator
        self.access_token = access_token

    def serve_forever(self) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def server_close(self) -> None:
        return None


class _Service:
    def __init__(self, name: str, events: list[str]) -> None:
        self.name = name
        self.events = events

    async def start(self) -> None:
        self.events.append(f"{self.name}.start")

    async def stop(self) -> None:
        self.events.append(f"{self.name}.stop")


def _configure(root: Path) -> InMemorySecretStore:
    workspace = root / "workspace"
    workspace.mkdir()
    repository = FilesystemConfigRepository(root)
    repository.write_node(
        NodeConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "NodeConfig",
                "metadata": {"name": "test-node"},
                "spec": {
                    "displayName": "Test Node",
                    "enabledAgents": ["main"],
                    "clientApi": {"listenHost": "127.0.0.1", "port": 19457, "authentication": "required"},
                    "operations": {
                        "taskSchedulerEnabled": True,
                        "cronEnabled": True,
                        "heartbeat": {"enabled": False},
                    },
                },
            }
        ),
        expected_revision=None,
    )
    repository.write_agent(
        "main",
        AgentConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AgentConfig",
                "metadata": {"name": "main"},
                "spec": {
                    "displayName": "Main",
                    "workspace": str(workspace),
                    "ownerPrincipalId": "principal:owner",
                    "privilegeLevel": "low",
                    "modelPolicy": {"defaultProfile": "primary"},
                },
            }
        ),
        expected_revision=None,
    )
    ModelProfileRepository(root).write_profile(
        "primary",
        ModelProfile.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "ModelProfile",
                "metadata": {"name": "primary"},
                "spec": {
                    "displayName": "Primary",
                    "provider": "openai",
                    "model": "openai/gpt-test",
                    "credential": {"store": "system", "name": "primary-key"},
                    "executionLocation": "remote",
                    "capabilities": ["text", "tool_calling"],
                    "fallbackProfiles": [],
                    "enabled": True,
                },
            }
        ),
        expected_revision=None,
    )
    secrets = InMemorySecretStore()
    secrets.put(SecretRef(store="system", name="primary-key"), SecretValue("not-a-real-key"))
    return secrets


def test_node_operations_runtime_orders_start_and_reverse_stop() -> None:
    events: list[str] = []
    runtime = NodeOperationsRuntime(
        task_scheduler=_Service("tasks", events),
        cron=_Service("cron", events),
        heartbeat=_Service("heartbeat", events),
        task_scheduler_enabled=True,
        cron_enabled=True,
        heartbeat_enabled=True,
    )

    async def exercise() -> None:
        await runtime.start()
        assert runtime.status()["running"] is True
        await runtime.stop()

    asyncio.run(exercise())

    assert events == [
        "tasks.start",
        "cron.start",
        "heartbeat.start",
        "heartbeat.stop",
        "cron.stop",
        "tasks.stop",
    ]


def test_operations_actions_share_policy_audit_and_node_facts(tmp_path: Path) -> None:
    host = OpenPpxNodeHost.build(
        tmp_path,
        access_token="node-token",
        secret_store=_configure(tmp_path),
        server_factory=_Server,
    )

    health = host.coordinator.invoke_action(
        "operations.health",
        {},
        request_id="req-health",
        correlation_id="corr-health",
        confirmed=False,
    )
    denied = host.coordinator.invoke_action(
        "operations.cron.create",
        {
            "name": "Daily review",
            "agentId": "main",
            "userId": "principal:owner",
            "message": "secret-canary-in-job-message",
            "schedule": {"kind": "every", "everySeconds": 3600},
            "deleteAfterRun": False,
        },
        request_id="req-cron-denied",
        correlation_id="corr-cron-denied",
        confirmed=False,
    )
    created = host.coordinator.invoke_action(
        "operations.cron.create",
        {
            "name": "Daily review",
            "agentId": "main",
            "userId": "principal:owner",
            "message": "secret-canary-in-job-message",
            "schedule": {"kind": "every", "everySeconds": 3600},
            "deleteAfterRun": False,
        },
        request_id="req-cron-create",
        correlation_id="corr-cron-create",
        confirmed=True,
    )
    listed = host.coordinator.invoke_action(
        "operations.cron.list",
        {"includeDisabled": True, "historyLimit": 10},
        request_id="req-cron-list",
        correlation_id="corr-cron-list",
        confirmed=False,
    )
    audit = host.coordinator.invoke_action(
        "operations.audit.list",
        {"limit": 20, "actionId": "operations.cron.create"},
        request_id="req-audit-list",
        correlation_id="corr-audit-list",
        confirmed=False,
    )
    command_catalog = host.coordinator.action_catalog(namespace="operations", projection="slash")
    cron_command = host.coordinator.invoke_action(
        "system.command.invoke",
        {"rawCommand": "/cron", "userId": "principal:client-api"},
        request_id="req-command-cron",
        correlation_id="corr-command-cron",
        confirmed=False,
    )
    heartbeat_command = host.coordinator.invoke_action(
        "system.command.invoke",
        {"rawCommand": "/heartbeat", "userId": "principal:client-api"},
        request_id="req-command-heartbeat",
        correlation_id="corr-command-heartbeat",
        confirmed=False,
    )
    usage_command = host.coordinator.invoke_action(
        "system.command.invoke",
        {"rawCommand": "/usage", "userId": "principal:client-api"},
        request_id="req-command-usage",
        correlation_id="corr-command-usage",
        confirmed=False,
    )

    assert health["ok"] is True
    components = {item["component"] for item in health["result"]["components"]}
    assert {"setup", "runtime", "extensions", "taskScheduler", "cron", "heartbeat", "secret", "audit", "stores", "sandbox"} <= components
    assert denied["ok"] is False
    assert denied["error"]["code"] == "confirmation_required"
    assert created["ok"] is True
    assert created["result"]["job"]["agentId"] == "main"
    assert listed["result"]["items"][0]["name"] == "Daily review"
    assert {item["outcomeCode"] for item in audit["result"]["items"]} == {"confirmation_required", "success"}
    commands = {
        command["command"]
        for item in command_catalog["result"]["items"]
        for command in item["slashCommands"]
    }
    assert commands == {"/cron", "/heartbeat", "/usage"}
    assert cron_command["result"]["targetActionId"] == "operations.cron.list"
    assert heartbeat_command["result"]["targetActionId"] == "operations.heartbeat.status"
    assert usage_command["result"]["targetActionId"] == "operations.usage.read"
    assert b"secret-canary-in-job-message" not in (tmp_path / "database" / "audit.db").read_bytes()
    host.close()


def test_operations_task_actions_project_controls_and_retained_output(tmp_path: Path) -> None:
    host = OpenPpxNodeHost.build(
        tmp_path,
        access_token="node-token",
        secret_store=_configure(tmp_path),
        server_factory=_Server,
    )
    task = TaskStore(db_path=tmp_path / "database" / "tasks.db").create_task(
        kind="manual",
        status="completed",
        title="Completed report",
        terminal_summary="The report is ready.",
        runner_capabilities={"output": True},
    )

    listed = host.coordinator.invoke_action(
        "operations.task.list",
        {"limit": 20, "sessionId": None},
        request_id="req-task-list",
        correlation_id="corr-task-list",
        confirmed=False,
    )
    detail = host.coordinator.invoke_action(
        "operations.task.get",
        {"taskId": task.task_id},
        request_id="req-task-get",
        correlation_id="corr-task-get",
        confirmed=False,
    )
    output = host.coordinator.invoke_action(
        "operations.task.output",
        {"taskId": task.task_id},
        request_id="req-task-output",
        correlation_id="corr-task-output",
        confirmed=False,
    )
    unconfirmed = host.coordinator.invoke_action(
        "operations.task.control",
        {"taskId": task.task_id, "action": "pause", "content": "", "inlineBudgetMs": None},
        request_id="req-task-pause",
        correlation_id="corr-task-pause",
        confirmed=False,
    )

    assert listed["ok"] is True
    assert listed["result"]["items"][0]["taskId"] == task.task_id
    assert {item["action"] for item in listed["result"]["items"][0]["actions"]} >= {"pause", "inspect_output"}
    assert detail["result"]["task"]["taskId"] == task.task_id
    assert "The report is ready" in output["result"]["output"]
    assert unconfirmed["ok"] is False
    assert unconfirmed["error"]["code"] == "confirmation_required"
    host.close()


def test_operations_updates_cron_and_persists_heartbeat_policy(tmp_path: Path) -> None:
    host = OpenPpxNodeHost.build(
        tmp_path,
        access_token="node-token",
        secret_store=_configure(tmp_path),
        server_factory=_Server,
    )
    created = host.coordinator.invoke_action(
        "operations.cron.create",
        {
            "name": "Initial schedule",
            "agentId": "main",
            "userId": "principal:owner",
            "message": "Initial instruction",
            "schedule": {"kind": "every", "everySeconds": 3600},
            "deleteAfterRun": False,
        },
        request_id="req-create-editable-cron",
        correlation_id="corr-create-editable-cron",
        confirmed=True,
    )
    job_id = created["result"]["job"]["id"]
    updated = host.coordinator.invoke_action(
        "operations.cron.update",
        {
            "jobId": job_id,
            "name": "Updated schedule",
            "agentId": "main",
            "userId": "principal:owner",
            "message": "Updated instruction",
            "schedule": {"kind": "cron", "cronExpression": "0 9 * * *", "timezone": "UTC"},
            "deleteAfterRun": False,
        },
        request_id="req-update-cron",
        correlation_id="corr-update-cron",
        confirmed=True,
    )
    heartbeat = host.coordinator.invoke_action(
        "operations.heartbeat.configure",
        {
            "enabled": True,
            "everySeconds": 900,
            "prompt": "Review tasks requiring operator attention.",
            "activeHours": {"start": "09:00", "end": "18:00", "timezone": "UTC"},
        },
        request_id="req-configure-heartbeat",
        correlation_id="corr-configure-heartbeat",
        confirmed=True,
    )
    node = FilesystemConfigRepository(tmp_path).read_node().document

    assert updated["ok"] is True, updated
    assert updated["result"]["job"]["name"] == "Updated schedule"
    assert updated["result"]["job"]["schedule"]["cronExpr"] == "0 9 * * *"
    assert heartbeat["result"]["effect"] == "restart_required"
    assert node.spec.operations.heartbeat.enabled is True
    assert node.spec.operations.heartbeat.every_seconds == 900
    assert node.spec.operations.heartbeat.active_hours.start == "09:00"
    host.close()
