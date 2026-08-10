"""Immutable Config snapshot to real ADK runtime integration tests."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.agents.invocation_context import LlmCallsLimitExceededError
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.genai import types
from pydantic import PrivateAttr

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
from openppx.extensions import (
    AppManager,
    ExtensionSourceRef,
    McpManager,
    McpServer,
    PluginManager,
    SkillManager,
    default_extension_starter_catalog,
    default_native_app_adapter_registry,
)
from openppx.extensions.app_models import AppConnection, AppDefinition
from openppx.core.mcp_registry import ManagedMcpToolset
from openppx.modeling import ModelCatalog, ModelProfile, ModelProfileRepository, ModelProfileSelector
from openppx.permissions import AgentPermissionSpec
from openppx.runtime.assembly import RuntimeAssembler
from openppx.runtime.goal_store import GoalStore
from openppx.runtime.node_runtime import NodeRuntimeSupervisor, RunNotActiveError, RunNotFoundError
from openppx.runtime.task_store import TaskStore
from openppx.tooling.registry import SubagentSpawnRequest

from tests.extensions.test_plugin_resources import _write_plugin


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


class _BlockingLlm(BaseLlm):
    """Deterministic model that remains active until cancelled or released."""

    _started: threading.Event = PrivateAttr(default_factory=threading.Event)
    _release: threading.Event = PrivateAttr(default_factory=threading.Event)

    @property
    def started(self) -> threading.Event:
        """Return the signal raised when generation starts."""
        return self._started

    @property
    def release(self) -> threading.Event:
        """Return the signal that allows generation to finish."""
        return self._release

    async def generate_content_async(self, llm_request, stream: bool = False):
        del llm_request, stream
        self._started.set()
        await asyncio.to_thread(self._release.wait)
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="released")],
            )
        )


class _FailingLlm(BaseLlm):
    """Raise one sensitive-looking provider error for redaction tests."""

    async def generate_content_async(self, llm_request, stream: bool = False):
        del llm_request, stream
        if False:  # pragma: no cover - keeps this method an async generator
            yield LlmResponse()
        raise RuntimeError("provider-secret-value")


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
                    "controls": {},
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
    return service, secrets


def _set_agent_privilege(config_service: ConfigService, privilege_level: str) -> None:
    """Update the configured test Agent without changing its identity boundary."""
    current = config_service.repository.read_agent("low-main")
    updated = current.document.model_copy(
        update={
            "spec": current.document.spec.model_copy(
                update={"privilege_level": privilege_level}
            )
        }
    )
    config_service.apply_agent(
        "low-main",
        updated,
        expected_revision=current.revision,
    )


def _wait_for_task_status(
    store: TaskStore,
    task_id: str,
    statuses: set[str],
    *,
    timeout_seconds: float = 3.0,
):
    """Poll one TaskRun until it reaches a requested status."""
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        task = store.get_task(task_id)
        if task is not None and task.status in statuses:
            return task
        time.sleep(0.01)
    task = store.get_task(task_id)
    raise AssertionError(
        f"Task {task_id!r} did not reach {sorted(statuses)}; "
        f"last status={getattr(task, 'status', None)!r}."
    )


def _skill(root: Path, *, description: str, body: str) -> Path:
    """Write one installable Skill fixture."""
    root.mkdir(parents=True, exist_ok=True)
    root.joinpath("SKILL.md").write_text(
        f"---\nname: runtime-skill\ndescription: {description}\n---\n\n{body}\n",
        encoding="utf-8",
    )
    return root


def _tool_function(agent: object, name: str):
    """Return a callable from one ADK Agent tool declaration."""
    for tool in agent.tools:  # type: ignore[attr-defined]
        candidate = getattr(tool, "func", tool)
        if getattr(candidate, "__name__", "") == name:
            return candidate
    raise AssertionError(f"Tool {name!r} was not attached to the Agent.")


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
    assert runtime.metadata.permission_revision == snapshot.permissions.revision
    assert runtime.metadata.agent_id == "low-main"
    assert runtime.metadata.model_profile_id == "primary"
    assert dict(os.environ) == before


def test_subagent_runtime_removes_recursive_delegation_and_pins_spawn_snapshot(
    tmp_path: Path,
) -> None:
    config_service, secrets = _configured(tmp_path)
    _set_agent_privilege(config_service, "medium")
    snapshot = config_service.snapshot("low-main")
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        permission_snapshot_provider=config_service.permission_snapshot,
    )

    runtime = assembler.assemble_subagent(snapshot)

    tool_names = {
        getattr(getattr(tool, "func", tool), "__name__", getattr(tool, "name", ""))
        for tool in runtime.agent.tools
    }
    assert "spawn_subagent" not in tool_names
    assert runtime.metadata.permission_revision == snapshot.permissions.revision
    assert runtime.permission_refresh_policy == "fail_on_change"


def test_supervisor_dispatch_subagent_completes_real_adk_run_and_bridges_result(
    tmp_path: Path,
) -> None:
    config_service, secrets = _configured(tmp_path)
    _set_agent_privilege(config_service, "medium")
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        permission_snapshot_provider=config_service.permission_snapshot,
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)
    parent_session_id = "parent-subagent-session"
    supervisor.create_session_sync(
        "low-main",
        user_id="local:owner",
        session_id=parent_session_id,
    )
    snapshot = config_service.snapshot("low-main")
    extension_revision = assembler.extension_snapshot_for_agent("low-main").revision
    request = SubagentSpawnRequest(
        task_id="subagent-real-run",
        prompt="Summarize the repository",
        agent_id="low-main",
        user_id="local:owner",
        session_id=parent_session_id,
        invocation_id="inv-parent-1",
        function_call_id="fc-parent-1",
        route="client",
        scope_id=parent_session_id,
        snapshot_revision=snapshot.revision,
        permission_revision=snapshot.permissions.revision,
        extension_revision=extension_revision,
        notify_on_complete=True,
    )

    supervisor.dispatch_subagent(request)

    task = _wait_for_task_status(
        assembler.services.task_store,
        request.task_id,
        {"completed"},
    )
    assert task.kind == "subagent"
    assert task.session_id == parent_session_id
    assert task.runner_payload["runner"] == "subagent"
    assert task.runner_payload["result"] == "Hello from immutable snapshot"
    assert task.runner_payload["permission_revision"] == snapshot.permissions.revision
    shown = supervisor.task_controller.show_task(request.task_id)
    assert shown["ok"] is True
    assert shown["task"]["status"] == "completed"

    parent = supervisor.get_session_sync(
        "low-main",
        user_id="local:owner",
        session_id=parent_session_id,
    )
    responses = [
        part.function_response
        for event in parent.events
        if event.content is not None
        for part in event.content.parts or []
        if part.function_response is not None
    ]
    bridged = next(response for response in responses if response.id == "fc-parent-1")
    assert bridged.name == "spawn_subagent"
    assert bridged.response["status"] == "completed"
    assert bridged.response["task_id"] == request.task_id
    assert bridged.response["result"] == "Hello from immutable snapshot"
    supervisor.close()


def test_subagent_failure_redacts_backend_exception_from_task_and_parent_session(
    tmp_path: Path,
) -> None:
    config_service, secrets = _configured(tmp_path)
    _set_agent_privilege(config_service, "medium")
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _FailingLlm(model="failing-model"),
        permission_snapshot_provider=config_service.permission_snapshot,
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)
    parent_session_id = "parent-subagent-failure"
    supervisor.create_session_sync(
        "low-main",
        user_id="local:owner",
        session_id=parent_session_id,
    )
    snapshot = config_service.snapshot("low-main")
    request = SubagentSpawnRequest(
        task_id="subagent-failed-run",
        prompt="Fail safely",
        agent_id="low-main",
        user_id="local:owner",
        session_id=parent_session_id,
        invocation_id="inv-parent-failed",
        function_call_id="fc-parent-failed",
        route="client",
        scope_id=parent_session_id,
        snapshot_revision=snapshot.revision,
        permission_revision=snapshot.permissions.revision,
        extension_revision=assembler.extension_snapshot_for_agent("low-main").revision,
    )

    supervisor.dispatch_subagent(request)

    task = _wait_for_task_status(
        assembler.services.task_store,
        request.task_id,
        {"failed"},
    )
    assert task.last_error == "Subagent task failed during execution."
    assert "provider-secret-value" not in str(task)

    bridged = None
    for _ in range(100):
        parent = supervisor.get_session_sync(
            "low-main",
            user_id="local:owner",
            session_id=parent_session_id,
        )
        bridged = next(
            (
                part.function_response
                for event in parent.events
                if event.content is not None
                for part in event.content.parts or []
                if part.function_response is not None
                and part.function_response.id == "fc-parent-failed"
            ),
            None,
        )
        if bridged is not None:
            break
        time.sleep(0.01)
    assert bridged is not None
    assert bridged.response["status"] == "failed"
    assert bridged.response["error"] == "Subagent task failed during execution."
    assert "provider-secret-value" not in str(bridged.response)
    supervisor.close()


def test_supervisor_rejects_subagent_when_spawn_snapshot_is_no_longer_current(
    tmp_path: Path,
) -> None:
    config_service, secrets = _configured(tmp_path)
    _set_agent_privilege(config_service, "medium")
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)
    snapshot = config_service.snapshot("low-main")
    request = SubagentSpawnRequest(
        task_id="subagent-stale-snapshot",
        prompt="Do not run",
        agent_id="low-main",
        user_id="local:owner",
        session_id="parent",
        invocation_id="inv-parent",
        function_call_id="fc-parent",
        route="client",
        scope_id="parent",
        snapshot_revision="stale-snapshot",
        permission_revision=snapshot.permissions.revision,
        extension_revision=assembler.extension_snapshot_for_agent("low-main").revision,
    )

    with pytest.raises(PermissionError, match="snapshot"):
        supervisor.dispatch_subagent(request)

    assert assembler.services.task_store.get_task(request.task_id) is None
    supervisor.close()


def test_subagent_task_can_be_cancelled_through_node_owned_task_controller(
    tmp_path: Path,
) -> None:
    config_service, secrets = _configured(tmp_path)
    _set_agent_privilege(config_service, "medium")
    model = _BlockingLlm(model="blocking-model")
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: model,
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)
    supervisor.create_session_sync(
        "low-main",
        user_id="local:owner",
        session_id="parent-cancel-session",
    )
    snapshot = config_service.snapshot("low-main")
    request = SubagentSpawnRequest(
        task_id="subagent-cancel-run",
        prompt="Wait until cancelled",
        agent_id="low-main",
        user_id="local:owner",
        session_id="parent-cancel-session",
        invocation_id="inv-cancel",
        function_call_id="fc-cancel",
        route="client",
        scope_id="parent-cancel-session",
        snapshot_revision=snapshot.revision,
        permission_revision=snapshot.permissions.revision,
        extension_revision=assembler.extension_snapshot_for_agent("low-main").revision,
    )
    supervisor.dispatch_subagent(request)
    assert model.started.wait(timeout=2)

    shown = supervisor.task_controller.show_task(request.task_id)
    assert shown["task"]["controls"]["can_interrupt"] is False
    assert shown["task"]["controls"]["can_cancel"] is True
    assert shown["task"]["controls"]["can_resume"] is False

    cancelled = supervisor.task_controller.cancel_task(request.task_id)

    assert cancelled["ok"] is True
    model.release.set()
    task = _wait_for_task_status(
        assembler.services.task_store,
        request.task_id,
        {"cancelled"},
    )
    assert task.status == "cancelled"
    assert supervisor.run_status(task.external_ref).state == "cancelled"
    supervisor.close()


def test_subagent_dispatch_is_idempotent_and_limits_parent_session_concurrency(
    tmp_path: Path,
) -> None:
    config_service, secrets = _configured(tmp_path)
    _set_agent_privilege(config_service, "medium")
    model = _BlockingLlm(model="blocking-model")
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: model,
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)
    parent_session_id = "parent-concurrency-session"
    supervisor.create_session_sync(
        "low-main",
        user_id="local:owner",
        session_id=parent_session_id,
    )
    snapshot = config_service.snapshot("low-main")
    extension_revision = assembler.extension_snapshot_for_agent("low-main").revision

    def request(index: int) -> SubagentSpawnRequest:
        return SubagentSpawnRequest(
            task_id=f"subagent-concurrency-{index}",
            prompt=f"Wait as worker {index}",
            agent_id="low-main",
            user_id="local:owner",
            session_id=parent_session_id,
            invocation_id="inv-concurrency",
            function_call_id=f"fc-concurrency-{index}",
            route="client",
            scope_id=parent_session_id,
            snapshot_revision=snapshot.revision,
            permission_revision=snapshot.permissions.revision,
            extension_revision=extension_revision,
        )

    first = supervisor.dispatch_subagent(request(0))
    duplicate = supervisor.dispatch_subagent(request(0))
    assert duplicate.task_id == first.task_id
    for index in range(1, 4):
        supervisor.dispatch_subagent(request(index))

    with pytest.raises(RuntimeError, match="maximum number"):
        supervisor.dispatch_subagent(request(4))

    assert supervisor.task_controller is not None
    for index in range(4):
        stopped = supervisor.task_controller.cancel_task(request(index).task_id)
        assert stopped["ok"] is True
    model.release.set()
    for index in range(4):
        _wait_for_task_status(
            assembler.services.task_store,
            request(index).task_id,
            {"cancelled"},
        )
    supervisor.close()


def test_assembled_runtime_binds_core_tools_to_agent_workspace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Production Agent assembly must not inherit the Node process directory."""
    config_service, secrets = _configured(tmp_path)
    current = config_service.repository.read_agent("low-main")
    workspace = tmp_path / "selected-workspace"
    workspace.mkdir()
    updated = current.document.model_copy(
        update={
            "spec": current.document.spec.model_copy(
                update={
                    "workspace": str(workspace),
                    "privilege_level": "medium",
                }
            )
        }
    )
    config_service.apply_agent(
        "low-main",
        updated,
        expected_revision=current.revision,
    )
    ambient = tmp_path / "ambient"
    node_cwd = tmp_path / "node-cwd"
    ambient.mkdir()
    node_cwd.mkdir()
    monkeypatch.setenv("OPENPPX_WORKSPACE", str(ambient))
    monkeypatch.chdir(node_cwd)
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
    )

    runtime = assembler.assemble(config_service.snapshot("low-main"))
    write = _tool_function(runtime.agent, "write_file")
    execute = _tool_function(runtime.agent, "exec")

    assert "Successfully wrote" in write("result.md", "runtime workspace")
    assert execute("pwd").strip() == str(workspace)
    assert (workspace / "result.md").read_text(encoding="utf-8") == "runtime workspace"
    assert not (ambient / "result.md").exists()
    assert not (node_cwd / "result.md").exists()


def test_long_lived_runtime_rechecks_config_service_before_next_side_effect(
    tmp_path: Path,
) -> None:
    """A held Runtime must honor compatible permission tightening without reassembly."""

    config_service, secrets = _configured(tmp_path)
    current = config_service.repository.read_agent("low-main")
    medium = current.document.model_copy(
        update={
            "spec": current.document.spec.model_copy(update={"privilege_level": "medium"})
        }
    )
    applied_medium = config_service.apply_agent(
        "low-main",
        medium,
        expected_revision=current.revision,
    )
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        permission_snapshot_provider=config_service.permission_snapshot,
    )
    runtime = assembler.assemble(config_service.snapshot("low-main"))
    write = _tool_function(runtime.agent, "write_file")
    assert "Successfully wrote" in write("before.txt", "allowed")

    low_enforced = applied_medium.resource.document.model_copy(
        update={
            "spec": applied_medium.resource.document.spec.model_copy(
                update={
                    "privilege_level": "low",
                    "permissions": AgentPermissionSpec.model_validate(
                        {"rolloutModes": {"workspace": "enforce"}}
                    ),
                }
            )
        }
    )
    config_service.apply_agent(
        "low-main",
        low_enforced,
        expected_revision=applied_medium.resource.revision,
    )

    denied = write("after.txt", "revoked")

    assert "denied by Agent permissions" in denied
    assert not (tmp_path / "workspace" / "after.txt").exists()


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


def test_supervisor_rebuilds_runtime_for_a_new_immutable_skill_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_service, secrets = _configured(tmp_path)
    source = _skill(tmp_path / "source", description="First runtime skill.", body="# First")
    manager = SkillManager(tmp_path)
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )
    enabled = manager.enable("runtime-skill", "low-main", expected_revision=installed.revision)
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        skill_manager=manager,
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)

    def _reject_ambient_discovery():
        raise AssertionError("snapshot-native Runtime must not discover ambient Skills")

    monkeypatch.setattr("openppx.tooling.skills_adapter.get_registry", _reject_ambient_discovery)
    first = supervisor.runtime_for("low-main")
    listed = json.loads(_tool_function(first.agent, "list_skills")())
    read_first = _tool_function(first.agent, "read_skill")("runtime-skill")

    assert first.metadata.extension_revision == assembler.extension_snapshot_for_agent("low-main").revision
    assert "First runtime skill." in first.agent.instruction
    assert listed[0]["name"] == "runtime-skill"
    assert "location" not in listed[0]
    assert "# First" in read_first

    _skill(tmp_path / "source", description="Second runtime skill.", body="# Second")
    updated = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=enabled.revision,
    )
    second = supervisor.runtime_for("low-main")

    assert updated.record.spec.enabled_agent_ids == ["low-main"]
    assert second is not first
    assert second.metadata.snapshot_revision == first.metadata.snapshot_revision
    assert second.metadata.extension_revision != first.metadata.extension_revision
    assert "# First" in _tool_function(first.agent, "read_skill")("runtime-skill")
    assert "# Second" in _tool_function(second.agent, "read_skill")("runtime-skill")


def test_supervisor_attaches_direct_mcp_and_rebuilds_for_resource_change(tmp_path: Path) -> None:
    config_service, secrets = _configured(tmp_path)
    manager = McpManager(tmp_path, secrets)
    record = McpServer.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "McpServer",
            "metadata": {"name": "runtime-mcp"},
            "spec": {
                "displayName": "Runtime MCP",
                "description": "Direct MCP runtime fixture.",
                "transport": {
                    "type": "stdio",
                    "command": sys.executable,
                    "args": [str(Path("tests/eval/mock_mcp_server.py").resolve())],
                    "environment": {},
                },
                "policy": {"toolNamePrefix": "mcp_runtime"},
                "risk": "low",
                "enabledAgentIds": [],
            },
        }
    )
    created = manager.create(record, expected_revision=None)
    enabled = manager.enable("runtime-mcp", "low-main", expected_revision=created.revision)
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        mcp_manager=manager,
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)

    first = supervisor.runtime_for("low-main")
    assert len(first.extension_toolsets) == 1
    assert isinstance(first.extension_toolsets[0], ManagedMcpToolset)
    assert first.extension_toolsets[0] in first.agent.tools
    assert first.metadata.mcp_diagnostics == ()

    candidate = record.model_copy(
        update={"spec": record.spec.model_copy(update={"description": "Updated direct MCP fixture."})}
    )
    manager.update(candidate, expected_revision=enabled.revision)
    second = supervisor.runtime_for("low-main")

    assert second is not first
    assert second.metadata.snapshot_revision == first.metadata.snapshot_revision
    assert second.metadata.extension_revision != first.metadata.extension_revision
    supervisor.close()


def test_supervisor_attaches_app_mcp_and_rebuilds_for_definition_change(tmp_path: Path) -> None:
    config_service, secrets = _configured(tmp_path)
    manager = AppManager(tmp_path, secrets)
    definition = AppDefinition.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AppDefinition",
            "metadata": {"name": "runtime-app"},
            "spec": {
                "displayName": "Runtime App",
                "description": "App runtime fixture.",
                "version": "1.0.0",
                "category": "testing",
                "developer": "OpenPPX",
                "source": {
                    "type": "builtin",
                    "locator": "runtime-app",
                    "version": "1.0.0",
                    "revision": "builtin:1.0.0",
                    "digest": "sha256:" + "c" * 64,
                },
                "auth": {"type": "none", "credentials": []},
                "implementation": {
                    "type": "mcp",
                    "transport": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(Path("tests/eval/mock_mcp_server.py").resolve())],
                        "environment": {},
                    },
                },
                "tools": [
                    {
                        "name": "echo_context",
                        "title": "Echo context",
                        "description": "Echo one token.",
                        "access": "read",
                        "risk": "low",
                    }
                ],
                "policy": {},
            },
        }
    )
    installed = manager.install_definition(definition, expected_revision=None)
    connection = manager.create_connection(
        AppConnection.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AppConnection",
                "metadata": {"name": "runtime-account"},
                "spec": {
                    "appId": "runtime-app",
                    "displayName": "Runtime account",
                },
            }
        ),
        expected_revision=None,
    )
    manager.enable_connection(
        "runtime-account",
        "low-main",
        expected_revision=connection.revision,
    )
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        app_manager=manager,
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)

    first = supervisor.runtime_for("low-main")
    assert len(first.extension_toolsets) == 1
    assert isinstance(first.extension_toolsets[0], ManagedMcpToolset)
    assert first.extension_toolsets[0] in first.agent.tools
    assert assembler.extension_snapshot_for_agent("low-main").apps.connection_ids == (
        "runtime-account",
    )

    candidate = definition.model_copy(
        update={
            "spec": definition.spec.model_copy(update={"description": "Updated App runtime fixture."})
        }
    )
    manager.update_definition(candidate, expected_revision=installed.revision)
    second = supervisor.runtime_for("low-main")

    assert second is not first
    assert second.metadata.snapshot_revision == first.metadata.snapshot_revision
    assert second.metadata.extension_revision != first.metadata.extension_revision
    supervisor.close()


def test_supervisor_attaches_branded_native_app_tools_to_the_same_agent_runtime(
    tmp_path: Path,
) -> None:
    """Prove a direct App starter reaches the regular immutable ADK Agent path."""
    config_service, secrets = _configured(tmp_path)
    token_ref = SecretRef(store="system", name="telegram-runtime-token")
    secrets.put(token_ref, SecretValue("private-runtime-token"))
    manager = AppManager(
        tmp_path,
        secrets,
        adapter_registry=default_native_app_adapter_registry(),
    )
    starter = default_extension_starter_catalog().get("app-telegram")
    definition = AppDefinition.model_validate(starter.template["definition"])
    manager.install_definition(definition, expected_revision=None)
    connection = manager.create_connection(
        AppConnection.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AppConnection",
                "metadata": {"name": "telegram-runtime"},
                "spec": {
                    "appId": definition.metadata.name,
                    "displayName": "Telegram runtime",
                    "credentialRefs": {
                        "bot-token": token_ref.model_dump(mode="json", by_alias=True)
                    },
                },
            }
        ),
        expected_revision=None,
    )
    manager.enable_connection(
        "telegram-runtime",
        "low-main",
        expected_revision=connection.revision,
        confirmed=True,
    )
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        app_manager=manager,
    )
    runtime = assembler.assemble(config_service.snapshot("low-main"))

    assert {getattr(tool, "name", "") for tool in runtime.agent.tools} >= {
        "telegram_get_updates",
        "telegram_send_message",
    }
    assert runtime.metadata.extension_revision == assembler.extension_snapshot_for_agent(
        "low-main"
    ).revision


def test_trusted_plugin_session_hook_executes_through_the_real_adk_runner(
    tmp_path: Path,
) -> None:
    """Prove trusted Plugin Hooks are wired through the production Runner path."""
    config_service, secrets = _configured(tmp_path)
    source = _write_plugin(tmp_path / "hook-source", include_hooks=True)
    manager = PluginManager(tmp_path, secrets)
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )
    manager.trust_hooks("plugin-fixture", expected_revision=installed.revision)
    enabled = manager.enable(
        "plugin-fixture",
        "low-main",
        expected_revision=installed.revision,
        confirmed=True,
    )
    runtime = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        plugin_manager=manager,
    ).assemble(config_service.snapshot("low-main"))

    response = asyncio.run(
        runtime.run_text("Hello", user_id="local:test", session_id="hook-session")
    )

    assert enabled.record.spec.enabled_agent_ids == ["low-main"]
    assert response == "Hello from immutable snapshot"
    assert manager.root.joinpath("data", "plugin-fixture", "session-started").read_text() == "started"
    asyncio.run(runtime.close())


def test_supervisor_merges_plugin_resources_and_rebuilds_for_plugin_update(tmp_path: Path) -> None:
    config_service, secrets = _configured(tmp_path)
    source = _write_plugin(tmp_path / "plugin-source")
    manager = PluginManager(
        tmp_path,
        secrets,
    )
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )
    enabled = manager.enable(
        "plugin-fixture",
        "low-main",
        expected_revision=installed.revision,
    )
    assembler = RuntimeAssembler(
        node_root=tmp_path,
        secret_store=secrets,
        model_factory=lambda _resolution: _HelloLlm(model="hello-model"),
        plugin_manager=manager,
    )
    supervisor = NodeRuntimeSupervisor(config_service=config_service, assembler=assembler)

    first = supervisor.runtime_for("low-main")
    assert len(first.extension_toolsets) == 1
    assert "Research using the fixture Plugin." in first.agent.instruction
    assert "# Plugin research" in _tool_function(first.agent, "read_skill")(
        "plugin-fixture--research"
    )

    _write_plugin(source, version="1.1.0", skill_body="# New Plugin research\n")
    manager.update(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=enabled.revision,
    )
    second = supervisor.runtime_for("low-main")

    assert second is not first
    assert second.metadata.snapshot_revision == first.metadata.snapshot_revision
    assert second.metadata.extension_revision != first.metadata.extension_revision
    assert "# Plugin research" in _tool_function(first.agent, "read_skill")(
        "plugin-fixture--research"
    )
    assert "# New Plugin research" in _tool_function(second.agent, "read_skill")(
        "plugin-fixture--research"
    )
    supervisor.close()


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
    assert supervisor.has_active_run(session_id="session-1") is True

    stopped = supervisor.stop_run("run-1")
    assert stopped.state == "cancelling"
    assert supervisor.has_active_run(session_id="session-1") is True
    assert cancelled == ["run-1"]
    supervisor.complete_run("run-1", state="cancelled")
    assert supervisor.has_active_run(session_id="session-1") is False
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
    permissions = frozenset(
        {"system.read", "session.read", "session.write", "run.control", "task.read"}
    )
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

    history = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/history 5",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": created.data["session"]["id"],
        },
        context,
    )
    tasks = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/tasks 5",
            "userId": "local:test",
            "sessionId": created.data["session"]["id"],
        },
        context,
    )
    assert history.ok is True
    assert history.data["result"] == {"items": []}
    assert tasks.ok is True
    assert tasks.data["result"] == {"items": []}

    supervisor.register_run(
        run_id="run-action",
        agent_id="low-main",
        session_id=created.data["session"]["id"],
        snapshot_revision=supervisor.runtime_for("low-main").metadata.snapshot_revision,
        cancel=lambda: None,
    )
    inspected = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/inspect",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": created.data["session"]["id"],
            "runId": "run-action",
        },
        context,
    )
    stopped = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/stop",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": created.data["session"]["id"],
            "runId": "run-action",
        },
        context,
    )
    missing = application.invoke("run.stop", {"runId": "missing"}, context)

    assert stopped.ok is True
    assert stopped.data["targetActionId"] == "run.stop"
    assert inspected.ok is True
    inspection = inspected.data["result"]
    assert inspection["effectiveRuntime"]["modelProfileId"] == "primary"
    assert inspection["effectiveRuntime"]["workspaceConfigured"] is True
    assert "workspace" not in inspection["effectiveRuntime"]
    assert str(tmp_path) not in str(inspection)
    assert inspection["runs"][0]["runId"] == "run-action"
    assert inspection["goals"] == []
    assert inspection["tasks"] == []
    assert stopped.data["result"]["run"]["state"] == "cancelling"
    assert missing.error is not None
    assert missing.error.code == "run_not_found"


def test_session_lifecycle_has_deterministic_goal_semantics(tmp_path: Path) -> None:
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
    permissions = frozenset({"session.read", "session.write", "goal.write"})
    action_context = ActionContext(
        request_id="req-session-goal",
        correlation_id="corr-session-goal",
        actor_id="local:test",
        capabilities=permissions,
        permissions=permissions,
        confirmed=True,
    )
    created = application.invoke(
        "session.new",
        {"agentId": "low-main", "userId": "local:test"},
        action_context,
    )
    session_id = created.data["session"]["id"]
    goal = application.invoke(
        "goal.create",
        {
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": session_id,
            "objective": "Finish durable work",
        },
        action_context,
    )
    assert goal.ok is True

    supervisor.register_run(
        run_id="run-session-archive",
        agent_id="low-main",
        session_id=session_id,
        snapshot_revision="sha256:" + "3" * 64,
        cancel=lambda: None,
    )
    blocked_archive = application.invoke(
        "session.archive",
        {
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": session_id,
            "archived": True,
        },
        action_context,
    )
    assert blocked_archive.ok is False
    assert blocked_archive.error is not None
    assert blocked_archive.error.code == "session_run_active"
    active_goal = application.goal_store.current_goal(session_id)
    assert active_goal is not None and active_goal.status == "active"
    supervisor.complete_run("run-session-archive", state="completed")

    archived = application.invoke(
        "session.archive",
        {
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": session_id,
            "archived": True,
        },
        action_context,
    )
    assert archived.ok is True
    paused = application.goal_store.current_goal(session_id)
    assert paused is not None and paused.status == "paused"

    forked = application.invoke(
        "session.fork",
        {"userId": "local:test", "agentId": "low-main", "sessionId": session_id},
        action_context,
    )
    assert forked.ok is True
    assert forked.data["goalInherited"] is False
    assert forked.data["sourceGoalId"] == goal.data["goalId"]
    assert application.goal_store.current_goal(forked.data["session"]["id"]) is None

    resumed = application.invoke(
        "goal.resume",
        {
            "goalId": goal.data["goalId"],
            "userId": "local:test",
            "expectedRevision": paused.revision,
        },
        action_context,
    )
    assert resumed.ok is True
    deleted = application.invoke(
        "session.delete",
        {"userId": "local:test", "agentId": "low-main", "sessionId": session_id},
        action_context,
    )
    assert deleted.ok is True
    assert application.goal_store.get_goal(goal.data["goalId"]).status == "cancelled"  # type: ignore[union-attr]


def test_model_command_persists_session_override_and_pins_new_run_snapshot(tmp_path: Path) -> None:
    config_service, secrets = _configured(tmp_path)
    config_service.profiles.write_profile(
        "reasoning",
        ModelProfile.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "ModelProfile",
                "metadata": {"name": "reasoning"},
                "spec": {
                    "displayName": "Reasoning",
                    "provider": "openai",
                    "model": "openai/gpt-reasoning",
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
    permissions = frozenset(
        {"system.read", "session.read", "session.write", "model.read", "model.use"}
    )
    action_context = ActionContext(
        request_id="req-model-session",
        correlation_id="corr-model-session",
        actor_id="local:test",
        capabilities=permissions,
        permissions=permissions,
    )
    created = application.invoke(
        "session.new",
        {"agentId": "low-main", "userId": "local:test"},
        action_context,
    )
    session_id = created.data["session"]["id"]

    selected = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/model reasoning",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": session_id,
        },
        action_context,
    )
    status = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/model status",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": session_id,
        },
        action_context,
    )
    started = supervisor.start_run(
        run_id="run-model-session",
        agent_id="low-main",
        session_id=session_id,
        user_id="local:test",
        text="Hello",
        run_override=application.session_metadata.get(session_id).model_profile_id,
    )

    assert selected.ok is True
    assert selected.data["result"]["sessionSelection"]["profileId"] == "reasoning"
    assert status.data["result"]["effectiveSelection"]["model"] == "openai/gpt-reasoning"
    assert started.model_profile_id == "reasoning"
    assert started.model == "openai/gpt-reasoning"

    reset = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/model reset",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": session_id,
        },
        action_context,
    )
    assert reset.data["result"]["sessionSelection"]["profileId"] is None


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
    persisted = supervisor.get_session_sync(
        "low-main",
        user_id="local:test",
        session_id=session.id,
    )
    assert persisted is not None
    assert any(
        (event.custom_metadata or {}).get("clientRunId") == "run-background"
        for event in persisted.events
    )


def test_supervisor_persists_client_run_identity_for_ordinary_runs(tmp_path: Path) -> None:
    """Every ADK event can be correlated back to its stable Client Run."""

    class _Runtime:
        metadata = SimpleNamespace(
            snapshot_revision="snapshot-r1",
            model_profile_id="primary",
            model_profile_revision="model-r1",
            provider="test",
            model="test/model",
        )

        def __init__(self) -> None:
            self.run_configs = []

        async def run_message(self, _message, **kwargs):
            self.run_configs.append(kwargs["run_config"])
            return "done"

    runtime = _Runtime()
    goal_store = GoalStore(db_path=tmp_path / "goals.db")

    class _Config:
        @staticmethod
        def snapshot(_agent_id, **_kwargs):
            return SimpleNamespace(revision="config-r1")

    class _Assembler:
        services = SimpleNamespace(goal_store=goal_store)

        @staticmethod
        def extension_snapshot_for_agent(_agent_id):
            return SimpleNamespace(revision="extensions-r1")

        @staticmethod
        def assemble(_snapshot, *, extension_snapshot):
            del extension_snapshot
            return runtime

    supervisor = NodeRuntimeSupervisor(config_service=_Config(), assembler=_Assembler())  # type: ignore[arg-type]
    completed = threading.Event()
    supervisor.start_run(
        run_id="run-ordinary",
        agent_id="main",
        session_id="session-ordinary",
        user_id="local:user",
        text="do work",
        on_complete=lambda _text: completed.set(),
    )

    assert completed.wait(timeout=5)
    assert runtime.run_configs[0].custom_metadata["clientRunId"] == "run-ordinary"


def test_goal_run_continues_in_fresh_adk_invocation_after_slice_limit(tmp_path: Path) -> None:
    """A Goal keeps one client Run while ADK receives bounded invocations."""

    class _Runtime:
        metadata = SimpleNamespace(
            snapshot_revision="snapshot-r1",
            model_profile_id="primary",
            model_profile_revision="model-r1",
            provider="test",
            model="test/model",
        )

        def __init__(self) -> None:
            self.initial_calls = 0
            self.continuation_calls = 0
            self.run_configs = []

        async def run_message(self, _message, **kwargs):
            self.initial_calls += 1
            self.run_configs.append(kwargs["run_config"])
            raise LlmCallsLimitExceededError()

        async def continue_message(self, **kwargs):
            self.continuation_calls += 1
            self.run_configs.append(kwargs["run_config"])
            return "continued result"

    runtime = _Runtime()
    goal_store = GoalStore(db_path=tmp_path / "goals.db")
    goal_store.create_goal(
        session_id="session-long",
        agent_id="main",
        user_id="local:user",
        objective="Finish long work",
        budget_policy={
            "maxContinuations": 2,
            "maxLlmCallsPerInvocation": 7,
            "autoContinue": False,
        },
        created_by="local:user",
    )

    class _Config:
        @staticmethod
        def snapshot(_agent_id, **_kwargs):
            return SimpleNamespace(revision="config-r1")

    class _Assembler:
        services = SimpleNamespace(goal_store=goal_store)

        @staticmethod
        def extension_snapshot_for_agent(_agent_id):
            return SimpleNamespace(revision="extensions-r1")

        @staticmethod
        def assemble(_snapshot, *, extension_snapshot):
            del extension_snapshot
            return runtime

    supervisor = NodeRuntimeSupervisor(config_service=_Config(), assembler=_Assembler())  # type: ignore[arg-type]
    completed = threading.Event()
    replies: list[str] = []
    errors: list[BaseException] = []

    supervisor.start_run(
        run_id="run-long",
        agent_id="main",
        session_id="session-long",
        user_id="local:user",
        text="continue until done",
        on_complete=lambda text: (replies.append(text), completed.set()),
        on_error=lambda exc: (errors.append(exc), completed.set()),
    )

    assert completed.wait(timeout=5)
    assert errors == []
    assert replies == ["continued result"]
    assert runtime.initial_calls == 1
    assert runtime.continuation_calls == 1
    assert runtime.run_configs[0].max_llm_calls == 7
    goal = goal_store.current_goal("session-long")
    assert goal is not None
    assert goal.budget_state["continuationCount"] == 1
    assert supervisor.run_status("run-long").state == "completed"


def test_active_goal_auto_continues_until_it_enters_waiting_state(tmp_path: Path) -> None:
    """A normal final reply does not silently stop an otherwise active Goal."""
    goal_store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = goal_store.create_goal(
        session_id="session-long",
        agent_id="main",
        user_id="local:user",
        objective="Finish long work",
        budget_policy={"maxContinuations": 3},
        created_by="local:user",
    )

    class _Runtime:
        metadata = SimpleNamespace(
            snapshot_revision="snapshot-r1",
            model_profile_id="primary",
            model_profile_revision="model-r1",
            provider="test",
            model="test/model",
        )

        def __init__(self) -> None:
            self.continuation_calls = 0

        async def run_message(self, _message, **_kwargs):
            return "first slice"

        async def continue_message(self, **_kwargs):
            self.continuation_calls += 1
            current = goal_store.get_goal(goal.goal_id)
            assert current is not None
            goal_store.transition_goal(
                current.goal_id,
                status="waiting",
                expected_revision=current.revision,
                actor_id="system:test",
            )
            return "waiting for user input"

    runtime = _Runtime()

    class _Config:
        @staticmethod
        def snapshot(_agent_id, **_kwargs):
            return SimpleNamespace(revision="config-r1")

    class _Assembler:
        services = SimpleNamespace(goal_store=goal_store)

        @staticmethod
        def extension_snapshot_for_agent(_agent_id):
            return SimpleNamespace(revision="extensions-r1")

        @staticmethod
        def assemble(_snapshot, *, extension_snapshot):
            del extension_snapshot
            return runtime

    supervisor = NodeRuntimeSupervisor(config_service=_Config(), assembler=_Assembler())  # type: ignore[arg-type]
    completed = threading.Event()
    replies: list[str] = []
    supervisor.start_run(
        run_id="run-long",
        agent_id="main",
        session_id="session-long",
        user_id="local:user",
        text="continue until waiting",
        on_complete=lambda text: (replies.append(text), completed.set()),
    )

    assert completed.wait(timeout=5)
    assert replies == ["waiting for user input"]
    assert runtime.continuation_calls == 1
    assert goal_store.get_goal(goal.goal_id).status == "waiting"  # type: ignore[union-attr]


def test_active_goal_enters_waiting_when_continuation_budget_is_exhausted(tmp_path: Path) -> None:
    goal_store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = goal_store.create_goal(
        session_id="session-long",
        agent_id="main",
        user_id="local:user",
        objective="Finish bounded work",
        budget_policy={"maxContinuations": 1},
        created_by="local:user",
    )

    class _Runtime:
        metadata = SimpleNamespace(
            snapshot_revision="snapshot-r1",
            model_profile_id="primary",
            model_profile_revision="model-r1",
            provider="test",
            model="test/model",
        )

        def __init__(self) -> None:
            self.continuation_calls = 0

        async def run_message(self, _message, **_kwargs):
            return "first slice"

        async def continue_message(self, **_kwargs):
            self.continuation_calls += 1
            return "bounded result"

    runtime = _Runtime()

    class _Config:
        @staticmethod
        def snapshot(_agent_id, **_kwargs):
            return SimpleNamespace(revision="config-r1")

    class _Assembler:
        services = SimpleNamespace(goal_store=goal_store)

        @staticmethod
        def extension_snapshot_for_agent(_agent_id):
            return SimpleNamespace(revision="extensions-r1")

        @staticmethod
        def assemble(_snapshot, *, extension_snapshot):
            del extension_snapshot
            return runtime

    supervisor = NodeRuntimeSupervisor(config_service=_Config(), assembler=_Assembler())  # type: ignore[arg-type]
    completed = threading.Event()
    replies: list[str] = []
    supervisor.start_run(
        run_id="run-long",
        agent_id="main",
        session_id="session-long",
        user_id="local:user",
        text="continue within a bounded budget",
        on_complete=lambda text: (replies.append(text), completed.set()),
    )

    assert completed.wait(timeout=5)
    assert replies == ["bounded result"]
    assert runtime.continuation_calls == 1
    waiting = goal_store.get_goal(goal.goal_id)
    assert waiting is not None and waiting.status == "waiting"
    flow = goal_store.flow_for_goal(goal.goal_id)
    assert flow is not None
    assert "continuation budget" in flow.wait_reason["message"]


def test_active_goal_blocks_repeated_adk_actions_before_budget_exhaustion(tmp_path: Path) -> None:
    """The native ADK continuation loop stops repeated action slices early."""
    goal_store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = goal_store.create_goal(
        session_id="session-loop",
        agent_id="main",
        user_id="local:user",
        objective="Research without looping",
        budget_policy={
            "maxContinuations": 6,
            "maxNoProgressContinuations": 3,
            "maxRepeatedActionContinuations": 2,
        },
        created_by="local:user",
    )

    class _Runtime:
        metadata = SimpleNamespace(
            snapshot_revision="snapshot-r1",
            model_profile_id="primary",
            model_profile_revision="model-r1",
            provider="test",
            model="test/model",
        )

        def __init__(self) -> None:
            self.continuation_calls = 0

        async def _slice(self, callback, invocation_id: str) -> str:
            event = SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "invocation_id": invocation_id,
                    "content": {
                        "parts": [
                            {
                                "function_call": {
                                    "id": invocation_id,
                                    "name": "web_search",
                                    "args": {"query": "same query"},
                                }
                            }
                        ]
                    },
                }
            )
            observed = callback(event)
            if asyncio.iscoroutine(observed):
                await observed
            return "bounded slice"

        async def run_message(self, _message, **kwargs):
            return await self._slice(kwargs["on_event"], "inv-1")

        async def continue_message(self, **kwargs):
            self.continuation_calls += 1
            return await self._slice(kwargs["on_event"], f"inv-{self.continuation_calls + 1}")

    runtime = _Runtime()

    class _Config:
        @staticmethod
        def snapshot(_agent_id, **_kwargs):
            return SimpleNamespace(revision="config-r1")

    class _Assembler:
        services = SimpleNamespace(goal_store=goal_store)

        @staticmethod
        def extension_snapshot_for_agent(_agent_id):
            return SimpleNamespace(revision="extensions-r1")

        @staticmethod
        def assemble(_snapshot, *, extension_snapshot):
            del extension_snapshot
            return runtime

    supervisor = NodeRuntimeSupervisor(config_service=_Config(), assembler=_Assembler())  # type: ignore[arg-type]
    completed = threading.Event()
    replies: list[str] = []
    supervisor.start_run(
        run_id="run-loop",
        agent_id="main",
        session_id=goal.session_id,
        user_id="local:user",
        text="research",
        on_complete=lambda text: (replies.append(text), completed.set()),
    )

    assert completed.wait(timeout=5)
    assert replies == ["bounded slice"]
    assert runtime.continuation_calls == 1
    blocked = goal_store.get_goal(goal.goal_id)
    assert blocked is not None and blocked.status == "blocked"
    flow = goal_store.flow_for_goal(goal.goal_id)
    assert flow is not None and flow.wait_reason["kind"] == "repeated_actions"


def test_goal_loop_block_without_model_text_finishes_as_retryable_pause(tmp_path: Path) -> None:
    """A controlled loop stop must not turn an already-blocked Goal into a failed Run."""
    goal_store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = goal_store.create_goal(
        session_id="session-loop-no-text",
        agent_id="main",
        user_id="local:user",
        objective="Research without looping",
        budget_policy={
            "maxContinuations": 6,
            "maxRepeatedActionContinuations": 2,
        },
        created_by="local:user",
    )

    class _Runtime:
        metadata = SimpleNamespace(
            snapshot_revision="snapshot-r1",
            model_profile_id="primary",
            model_profile_revision="model-r1",
            provider="test",
            model="test/model",
        )

        async def _slice(self, callback, invocation_id: str) -> None:
            event = SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "invocation_id": invocation_id,
                    "content": {
                        "parts": [
                            {
                                "function_call": {
                                    "id": invocation_id,
                                    "name": "web_search",
                                    "args": {"query": "same query"},
                                }
                            }
                        ]
                    },
                }
            )
            observed = callback(event)
            if asyncio.iscoroutine(observed):
                await observed
            raise LlmCallsLimitExceededError()

        async def run_message(self, _message, **kwargs):
            await self._slice(kwargs["on_event"], "invocation-1")

        async def continue_message(self, **kwargs):
            await self._slice(kwargs["on_event"], "invocation-2")

    runtime = _Runtime()

    class _Config:
        @staticmethod
        def snapshot(_agent_id, **_kwargs):
            return SimpleNamespace(revision="config-r1")

    class _Assembler:
        services = SimpleNamespace(goal_store=goal_store)

        @staticmethod
        def extension_snapshot_for_agent(_agent_id):
            return SimpleNamespace(revision="extensions-r1")

        @staticmethod
        def assemble(_snapshot, *, extension_snapshot):
            del extension_snapshot
            return runtime

    supervisor = NodeRuntimeSupervisor(config_service=_Config(), assembler=_Assembler())  # type: ignore[arg-type]
    completed = threading.Event()
    replies: list[str] = []
    errors: list[BaseException] = []
    supervisor.start_run(
        run_id="run-loop-no-text",
        agent_id="main",
        session_id=goal.session_id,
        user_id="local:user",
        text="continue until blocked",
        on_complete=lambda text: (replies.append(text), completed.set()),
        on_error=lambda exc: (errors.append(exc), completed.set()),
    )

    assert completed.wait(timeout=5)
    assert errors == []
    assert replies == [
        "Goal paused: The Goal repeated the same actions without durable progress."
    ]
    assert supervisor.run_status("run-loop-no-text").state == "completed"
    blocked = goal_store.get_goal(goal.goal_id)
    assert blocked is not None and blocked.status == "blocked"


def test_node_runtime_reconciles_explicit_goal_completion_by_adk_invocation(tmp_path: Path) -> None:
    """Goal completion is owned by Runtime facts, not the HTTP presentation layer."""
    goal_store = GoalStore(db_path=tmp_path / "goals.db")
    goal, _flow = goal_store.create_goal(
        session_id="session-complete",
        agent_id="main",
        user_id="local:user",
        objective="Finish one verified run",
        created_by="local:user",
    )

    class _Runtime:
        metadata = SimpleNamespace(
            snapshot_revision="snapshot-r1",
            model_profile_id="primary",
            model_profile_revision="model-r1",
            provider="test",
            model="test/model",
        )

        async def run_message(self, _message, **kwargs):
            event = SimpleNamespace(
                model_dump=lambda **_kwargs: {
                    "invocation_id": "inv-complete",
                    "content": {"parts": []},
                }
            )
            observed = kwargs["on_event"](event)
            if asyncio.iscoroutine(observed):
                await observed
            current = goal_store.get_goal(goal.goal_id)
            assert current is not None
            goal_store.request_completion(
                current.goal_id,
                expected_revision=current.revision,
                actor_id="agent:main",
                invocation_id="inv-complete",
            )
            return "verified result"

        async def continue_message(self, **_kwargs):
            raise AssertionError("completion should stop continuations")

    runtime = _Runtime()

    class _Config:
        @staticmethod
        def snapshot(_agent_id, **_kwargs):
            return SimpleNamespace(revision="config-r1")

    class _Assembler:
        services = SimpleNamespace(goal_store=goal_store)

        @staticmethod
        def extension_snapshot_for_agent(_agent_id):
            return SimpleNamespace(revision="extensions-r1")

        @staticmethod
        def assemble(_snapshot, *, extension_snapshot):
            del extension_snapshot
            return runtime

    supervisor = NodeRuntimeSupervisor(config_service=_Config(), assembler=_Assembler())  # type: ignore[arg-type]
    completed = threading.Event()
    supervisor.start_run(
        run_id="run-complete",
        agent_id="main",
        session_id=goal.session_id,
        user_id="local:user",
        text="finish",
        on_complete=lambda _text: completed.set(),
    )

    assert completed.wait(timeout=5)
    completed_goal = goal_store.get_goal(goal.goal_id)
    assert completed_goal is not None and completed_goal.status == "completed"
