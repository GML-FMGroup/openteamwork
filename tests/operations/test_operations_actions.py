"""Node Operations Action and lifecycle integration tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

from openppx.config import AgentConfig, FilesystemConfigRepository, InMemorySecretStore, NodeConfig, SecretRef, SecretValue
from openppx.modeling import ModelProfile, ModelProfileRepository
from openppx.operations import NodeOperationsRuntime
from openppx.runtime.node_host import OpenPpxNodeHost


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

    assert health["ok"] is True
    components = {item["component"] for item in health["result"]["components"]}
    assert {"setup", "runtime", "extensions", "taskScheduler", "cron", "heartbeat", "secret", "audit", "stores", "sandbox"} <= components
    assert denied["ok"] is False
    assert denied["error"]["code"] == "confirmation_required"
    assert created["ok"] is True
    assert created["result"]["job"]["agentId"] == "main"
    assert listed["result"]["items"][0]["name"] == "Daily review"
    assert {item["outcomeCode"] for item in audit["result"]["items"]} == {"confirmation_required", "success"}
    assert b"secret-canary-in-job-message" not in (tmp_path / "database" / "audit.db").read_bytes()
    host.close()
