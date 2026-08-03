"""Node composition-root and lifecycle ownership tests."""

from __future__ import annotations

import threading
from pathlib import Path

from openppx.config import AgentConfig, FilesystemConfigRepository, InMemorySecretStore, NodeConfig, SecretRef, SecretValue
from openppx.modeling import ModelProfile, ModelProfileRepository
from openppx.runtime.node_host import OpenPpxNodeHost


class _Server:
    def __init__(self, address, coordinator, *, access_token: str) -> None:
        self.address = address
        self.coordinator = coordinator
        self.access_token = access_token
        self.events: list[str] = []

    def serve_forever(self) -> None:
        self.events.append("server.start")

    def shutdown(self) -> None:
        self.events.append("server.shutdown")

    def server_close(self) -> None:
        self.events.append("server.close")


class _Scheduler:
    def __init__(self) -> None:
        self.events: list[str] = []
        self.started = threading.Event()

    async def start(self) -> None:
        self.events.append("scheduler.start")
        self.started.set()

    async def stop(self) -> None:
        self.events.append("scheduler.stop")


def _configure(root: Path) -> InMemorySecretStore:
    repository = FilesystemConfigRepository(root)
    repository.write_node(
        NodeConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "NodeConfig",
                "metadata": {"name": "test-node"},
                "spec": {
                    "displayName": "Test Node",
                    "enabledAgents": ["low-main"],
                    "clientApi": {
                        "listenHost": "127.0.0.1",
                        "port": 19455,
                        "authentication": "required",
                    },
                },
            }
        ),
        expected_revision=None,
    )
    repository.write_agent(
        "low-main",
        AgentConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AgentConfig",
                "metadata": {"name": "low-main"},
                "spec": {
                    "displayName": "Low Main",
                    "workspace": str(root / "workspace"),
                    "ownerPrincipalId": "local:owner",
                    "privilegeLevel": "low",
                    "permissionOverrides": {},
                    "modelPolicy": {"defaultProfile": "primary", "roleProfiles": {}},
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
    secrets.put(SecretRef(store="system", name="primary-key"), SecretValue("hidden"))
    return secrets


def test_node_host_uses_strict_listener_and_one_shared_component_graph(tmp_path: Path) -> None:
    secrets = _configure(tmp_path)
    scheduler = _Scheduler()
    servers: list[_Server] = []

    host = OpenPpxNodeHost.build(
        tmp_path,
        access_token="node-token",
        secret_store=secrets,
        scheduler=scheduler,
        server_factory=lambda address, coordinator, access_token: servers.append(
            _Server(address, coordinator, access_token=access_token)
        ) or servers[-1],
    )

    assert host.address == ("127.0.0.1", 19455)
    assert servers[0].access_token == "node-token"
    assert host.coordinator._control_plane is host.control_plane
    assert host.coordinator._runtime_supervisor is host.runtime_supervisor
    extension_payload = host.coordinator.invoke_action(
        "extension.list",
        {"kind": "skill", "agentId": None},
        request_id="req_node_extensions",
        correlation_id="corr_node_extensions",
        confirmed=False,
    )
    assert host.control_plane.extension_registry is not None
    assert extension_payload["ok"] is True
    assert isinstance(extension_payload["result"]["items"], list)


def test_node_host_starts_scheduler_before_server_and_stops_once(tmp_path: Path) -> None:
    secrets = _configure(tmp_path)
    scheduler = _Scheduler()
    servers: list[_Server] = []
    host = OpenPpxNodeHost.build(
        tmp_path,
        access_token="node-token",
        secret_store=secrets,
        scheduler=scheduler,
        server_factory=lambda address, coordinator, access_token: servers.append(
            _Server(address, coordinator, access_token=access_token)
        ) or servers[-1],
    )

    host.serve_forever()
    host.close()

    assert scheduler.events == ["scheduler.start", "scheduler.stop"]
    assert servers[0].events == ["server.start", "server.close"]
    assert host.runtime_supervisor.status()["state"] == "stopped"


def test_unconfigured_node_starts_safe_loopback_bootstrap_surface(tmp_path: Path) -> None:
    servers: list[_Server] = []
    host = OpenPpxNodeHost.build(
        tmp_path,
        secret_store=InMemorySecretStore(),
        scheduler=_Scheduler(),
        server_factory=lambda address, coordinator, access_token: servers.append(
            _Server(address, coordinator, access_token=access_token)
        ) or servers[-1],
    )

    assert host.address == ("127.0.0.1", 18765)
    assert host.authentication_required is False
    assert host.coordinator.health()["data"]["state"] == "needs_configuration"
    setup = host.coordinator.invoke_action(
        "setup.status",
        {},
        request_id="req-bootstrap",
        correlation_id="corr-bootstrap",
        confirmed=False,
    )
    assert setup["ok"] is True
    assert setup["result"]["state"] == "needs_configuration"
    host.close()


def test_unconfigured_non_loopback_bootstrap_requires_token(tmp_path: Path) -> None:
    import pytest

    with pytest.raises(ValueError, match="OPENPPX_CLIENT_API_TOKEN"):
        OpenPpxNodeHost.build(
            tmp_path,
            host="0.0.0.0",
            secret_store=InMemorySecretStore(),
            scheduler=_Scheduler(),
            server_factory=_Server,
        )
