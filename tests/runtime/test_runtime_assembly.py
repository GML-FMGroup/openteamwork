"""Immutable Config snapshot to real ADK runtime integration tests."""

from __future__ import annotations

import asyncio
import os
import threading
from pathlib import Path

import pytest
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types

from openppx.config import (
    AgentConfig,
    ConfigService,
    FilesystemConfigRepository,
    InMemorySecretStore,
    NodeConfig,
    SecretRef,
    SecretValue,
)
from openppx.actions import ActionContext
from openppx.control_plane import build_control_plane
from openppx.modeling import ModelCatalog, ModelProfile, ModelProfileRepository, ModelProfileSelector
from openppx.runtime.assembly import RuntimeAssembler
from openppx.runtime.node_runtime import NodeRuntimeSupervisor, RunNotActiveError, RunNotFoundError


class _HelloLlm(BaseLlm):
    """Deterministic ADK model used to prove the real Runner path offline."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        del llm_request, stream
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Hello from immutable snapshot")],
            )
        )


def _configured(tmp_path: Path) -> tuple[ConfigService, InMemorySecretStore]:
    repository = FilesystemConfigRepository(tmp_path)
    profiles = ModelProfileRepository(tmp_path)
    secrets = InMemorySecretStore()
    secrets.put(SecretRef(store="system", name="primary-key"), SecretValue("never-visible"))
    selector = ModelProfileSelector(profiles, ModelCatalog(), secrets)
    service = ConfigService(repository, profiles, selector)
    service.apply_node(
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
                        "port": 18765,
                        "authentication": "required",
                    },
                },
            }
        ),
        expected_revision=None,
    )
    service.apply_agent(
        "low-main",
        AgentConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AgentConfig",
                "metadata": {"name": "low-main"},
                "spec": {
                    "displayName": "Low Main",
                    "workspace": str(tmp_path / "workspace"),
                    "ownerPrincipalId": "local:owner",
                    "privilegeLevel": "low",
                    "permissionOverrides": {},
                    "modelPolicy": {"defaultProfile": "primary", "roleProfiles": {}},
                },
            }
        ),
        expected_revision=None,
    )
    profiles.write_profile(
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
    return service, secrets


def test_snapshot_builds_real_adk_runner_and_completes_hello_without_env_projection(tmp_path: Path) -> None:
    config_service, secrets = _configured(tmp_path)
    snapshot = config_service.snapshot("low-main")
    before = dict(os.environ)
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
    )

    runtime = assembler.assemble(snapshot)
    response = asyncio.run(
        runtime.run_text(
            "Hello",
            user_id="local:test",
            session_id="hello-session",
        )
    )

    assert response == "Hello from immutable snapshot"
    assert runtime.metadata.snapshot_revision == snapshot.revision
    assert runtime.metadata.agent_id == "low-main"
    assert runtime.metadata.model_profile_id == "primary"
    assert dict(os.environ) == before


def test_supervisor_reuses_exact_snapshot_and_refreshes_after_config_change(tmp_path: Path) -> None:
    config_service, secrets = _configured(tmp_path)
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)

    first = supervisor.runtime_for("low-main")
    same = supervisor.runtime_for("low-main")
    current = config_service.repository.read_agent("low-main")
    updated = current.document.model_copy(
        update={"spec": current.document.spec.model_copy(update={"display_name": "Updated"})}
    )
    config_service.apply_agent("low-main", updated, expected_revision=current.revision)
    refreshed = supervisor.runtime_for("low-main")

    assert same is first
    assert refreshed is not first
    assert refreshed.metadata.snapshot_revision != first.metadata.snapshot_revision


def test_supervisor_owns_run_stop_state_and_close_is_idempotent(tmp_path: Path) -> None:
    config_service, secrets = _configured(tmp_path)
    supervisor = NodeRuntimeSupervisor(
        config_service=config_service,
        assembler=RuntimeAssembler(
            node_root=tmp_path,
            secret_store=secrets,
            model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        ),
    )
    cancelled: list[str] = []
    supervisor.register_run(
        run_id="run-1",
        agent_id="low-main",
        session_id="session-1",
        snapshot_revision="sha256:" + "2" * 64,
        cancel=lambda: cancelled.append("run-1"),
    )

    stopped = supervisor.stop_run("run-1")
    assert stopped.state == "cancelling"
    assert cancelled == ["run-1"]
    supervisor.complete_run("run-1", state="cancelled")
    with pytest.raises(RunNotActiveError):
        supervisor.stop_run("run-1")
    with pytest.raises(RunNotFoundError):
        supervisor.stop_run("missing")

    supervisor.close()
    supervisor.close()
    assert supervisor.status()["state"] == "stopped"


def test_session_and_run_actions_use_the_same_runtime_supervisor(tmp_path: Path) -> None:
    config_service, secrets = _configured(tmp_path)
    supervisor = NodeRuntimeSupervisor(
        config_service=config_service,
        assembler=RuntimeAssembler(
            node_root=tmp_path,
            secret_store=secrets,
            model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        ),
    )
    application = build_control_plane(tmp_path, secret_store=secrets, product_version="test")
    application.attach_runtime(supervisor)
    permissions = frozenset({"session.write", "run.control"})
    context = ActionContext(
        request_id="req-runtime",
        correlation_id="corr-runtime",
        actor_id="local:test",
        capabilities=permissions,
        permissions=permissions,
    )

    created = application.invoke(
        "session.new",
        {"agentId": "low-main", "userId": "local:test"},
        context,
    )
    assert created.ok is True
    assert created.data["session"]["agentId"] == "low-main"

    supervisor.register_run(
        run_id="run-action",
        agent_id="low-main",
        session_id=created.data["session"]["id"],
        snapshot_revision=supervisor.runtime_for("low-main").metadata.snapshot_revision,
        cancel=lambda: None,
    )
    stopped = application.invoke("run.stop", {"runId": "run-action"}, context)
    missing = application.invoke("run.stop", {"runId": "missing"}, context)

    assert stopped.ok is True
    assert stopped.data["run"]["state"] == "cancelling"
    assert missing.error is not None
    assert missing.error.code == "run_not_found"


def test_supervisor_executes_background_run_on_the_snapshot_runtime(tmp_path: Path) -> None:
    config_service, secrets = _configured(tmp_path)
    supervisor = NodeRuntimeSupervisor(
        config_service=config_service,
        assembler=RuntimeAssembler(
            node_root=tmp_path,
            secret_store=secrets,
            model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        ),
    )
    session = supervisor.create_session_sync("low-main", user_id="local:test")
    completed = threading.Event()
    replies: list[str] = []

    started = supervisor.start_run(
        run_id="run-background",
        agent_id="low-main",
        session_id=session.id,
        user_id="local:test",
        text="Hello",
        on_complete=lambda text: (replies.append(text), completed.set()),
    )

    assert started.snapshot_revision == supervisor.runtime_for("low-main").metadata.snapshot_revision
    assert completed.wait(timeout=5)
    assert replies == ["Hello from immutable snapshot"]
    assert supervisor.run_status("run-background").state == "completed"
