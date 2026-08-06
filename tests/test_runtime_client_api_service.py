from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from google.adk.artifacts import FileArtifactService
from google.adk.events.event import Event
from google.adk.memory.memory_entry import MemoryEntry
from google.genai import types

from openppx.config import AgentConfig, FilesystemConfigRepository, NodeConfig
from openppx.runtime.access_policy import AccessPolicy
from openppx.runtime.agent_access_store import AgentAccessStore, AgentMembership
from openppx.runtime.client_api_service import (
    ClientApiCoordinator,
    RunHandle,
    _ClientApiHandler,
    project_session_event,
)
from openppx.runtime.identity_models import ResolvedPrincipal
from openppx.runtime.identity_store import IdentityStore
from openppx.runtime.memory_query_service import MemoryQueryService
from openppx.runtime.session_service import SessionConfig, create_session_service
from openppx.runtime.sqlite_memory_service import SQLiteMemoryService
from openppx.control_plane import build_control_plane
from openppx.runtime.node_runtime import ManagedRunSnapshot, RunNotActiveError, RunNotFoundError


class _FakeRuntimeSupervisor:
    """Deterministic in-process Runtime Supervisor test double."""

    def __init__(self, *, run_mode: str = "complete") -> None:
        self.run_mode = run_mode
        self.sessions: dict[tuple[str, str], list[SimpleNamespace]] = {}
        self.runs: dict[str, ManagedRunSnapshot] = {}
        self.callbacks: dict[str, object] = {}

    def create_session_sync(self, agent_id: str, *, user_id: str, session_id: str | None = None):
        session = SimpleNamespace(
            id=session_id or f"{agent_id}-session-{len(self.sessions) + 1}",
            last_update_time=1_700_000_001,
            events=[],
        )
        self.sessions.setdefault((agent_id, user_id), []).append(session)
        return session

    def list_sessions_sync(self, agent_id: str, *, user_id: str):
        return list(self.sessions.get((agent_id, user_id), []))

    def get_session_sync(self, agent_id: str, *, user_id: str, session_id: str):
        return next(
            (item for item in self.sessions.get((agent_id, user_id), []) if item.id == session_id),
            None,
        )

    def start_run(self, **kwargs):
        run_id = kwargs["run_id"]
        snapshot = ManagedRunSnapshot(
            run_id=run_id,
            agent_id=kwargs["agent_id"],
            session_id=kwargs["session_id"],
            snapshot_revision="sha256:" + "1" * 64,
            started_at="2026-08-03T00:00:00+00:00",
            state="running",
        )
        self.runs[run_id] = snapshot
        self.callbacks[run_id] = kwargs
        if self.run_mode == "pending":
            return snapshot
        event_payload = {
            "invocation_id": "invocation-test-run",
            "long_running_tool_ids": None,
            "content": {
                "parts": [
                    {"function_call": {"id": "call_1", "name": "inspect_repo", "args": {"path": "."}}},
                    {"function_response": {"id": "call_1", "name": "inspect_repo", "response": {"ok": True}}},
                ]
            },
        }
        kwargs["on_event"](SimpleNamespace(model_dump=lambda **_options: event_payload))
        if self.run_mode == "empty":
            kwargs["on_complete"]("")
        else:
            kwargs["on_text_update"]("hello", "hello")
            kwargs["on_complete"]("hello world")
        return snapshot

    def stop_run(self, run_id: str):
        current = self.runs.get(run_id)
        if current is None:
            raise RunNotFoundError(run_id)
        if current.state != "running":
            raise RunNotActiveError(run_id)
        updated = ManagedRunSnapshot(
            run_id=current.run_id,
            agent_id=current.agent_id,
            session_id=current.session_id,
            snapshot_revision=current.snapshot_revision,
            started_at=current.started_at,
            state="cancelling",
        )
        self.runs[run_id] = updated
        self.callbacks[run_id]["on_cancelled"]()
        return updated


def _coordinator_with_runtime(
    root: Path,
    *,
    supervisor: _FakeRuntimeSupervisor | None = None,
    **kwargs,
) -> tuple[ClientApiCoordinator, _FakeRuntimeSupervisor]:
    runtime = supervisor or _FakeRuntimeSupervisor()
    control_plane = build_control_plane(root, product_version="test")
    coordinator = ClientApiCoordinator(
        data_dir=root,
        control_plane=control_plane,
        runtime_supervisor=runtime,
        **kwargs,
    )
    return coordinator, runtime


def _principal(*, principal_id: str, privilege_level: str = "minimal") -> ResolvedPrincipal:
    return ResolvedPrincipal(
        principal_id=principal_id,
        principal_type="human",
        privilege_level=privilege_level,
        account_kind="local",
        display_name=principal_id,
        authenticated=True,
    )


def _memory(text: str, *, timestamp: str) -> MemoryEntry:
    return MemoryEntry(
        id=f"mem-{abs(hash((text, timestamp)))}",
        author="user",
        timestamp=timestamp,
        content=types.Content(role="user", parts=[types.Part.from_text(text=text)]),
    )


@pytest.fixture(autouse=True)
def _strict_control_plane_resources(request: pytest.FixtureRequest) -> None:
    """Seed strict Node/Agent truth for tests that use an isolated data root."""
    if "tmp_path" not in request.fixturenames:
        return
    root = request.getfixturevalue("tmp_path")
    repository = FilesystemConfigRepository(root)
    repository.write_node(
        NodeConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "NodeConfig",
                "metadata": {"name": "test-node"},
                "spec": {
                    "displayName": "Test Node",
                    "enabledAgents": ["writer"],
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
    repository.write_agent(
        "writer",
        AgentConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AgentConfig",
                "metadata": {"name": "writer"},
                "spec": {
                    "displayName": "Writer",
                    "workspace": "workspace/writer",
                    "ownerPrincipalId": "owner",
                    "privilegeLevel": "low",
                    "permissionOverrides": {},
                    "modelPolicy": {"defaultProfile": None, "roleProfiles": {}},
                },
            }
        ),
        expected_revision=None,
    )


def test_list_agents_uses_strict_control_plane_resources(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text("not-json", encoding="utf-8")

    payload = ClientApiCoordinator(data_dir=tmp_path).list_agents()

    assert payload["ok"] is True
    assert payload["data"]["items"][0]["id"] == "writer"
    assert payload["data"]["items"][0]["workspace"] == "workspace/writer"
    assert "Workspace:" in payload["data"]["items"][0]["description"]


def test_mcp_oauth_callback_is_public_but_state_gated(tmp_path: Path) -> None:
    class _OAuthService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str | None, str]] = []

        def deliver_callback(self, code: str, state: str | None, *, error: str = "") -> bool:
            self.calls.append((code, state, error))
            return state == "expected-state"

    control_plane = build_control_plane(tmp_path, product_version="test")
    oauth = _OAuthService()
    control_plane.mcp_oauth_service = oauth
    coordinator = ClientApiCoordinator(data_dir=tmp_path, control_plane=control_plane)
    responses: list[tuple[int, str]] = []
    handler = _ClientApiHandler.__new__(_ClientApiHandler)
    handler.server = SimpleNamespace(coordinator=coordinator)
    handler._send_html = lambda status, body: responses.append((status, body))

    handler.path = "/api/v1/mcp/oauth/callback?code=code-value&state=expected-state"
    handler.do_GET()
    assert responses[-1][0] == 200
    assert "Connected to OpenPPX" in responses[-1][1]
    assert oauth.calls == [("code-value", "expected-state", "")]

    handler.path = "/api/v1/mcp/oauth/callback?code=other-code&state=wrong-state"
    handler.do_GET()
    assert responses[-1][0] == 400


def test_action_catalog_and_invoke_use_common_contract_envelope(tmp_path: Path) -> None:
    coordinator = ClientApiCoordinator(data_dir=tmp_path)

    catalog = coordinator.action_catalog(
        namespace="system",
        request_id="req_catalog",
        correlation_id="corr_catalog",
    )
    invoked = coordinator.invoke_action(
        "system.status",
        {},
        request_id="req_status",
        correlation_id="corr_status",
        confirmed=False,
    )

    assert catalog["ok"] is True
    assert catalog["requestId"] == "req_catalog"
    assert {item["actionId"] for item in catalog["result"]["items"]} == {
        "system.command.invoke",
        "system.help",
        "system.status",
    }
    assert invoked["ok"] is True
    assert invoked["result"]["state"] == "ready"
    assert "data" not in invoked


def test_project_session_event_builds_structured_parts() -> None:
    message = project_session_event(
        {
            "id": "evt_1",
            "author": "assistant",
            "timestamp": 1_717_171_717,
            "content": {
                "parts": [
                    {"text": "I will inspect the repo."},
                    {"function_call": {"id": "call_1", "name": "inspect_repo", "args": {"path": "."}}},
                    {"function_response": {"id": "call_1", "name": "inspect_repo", "response": {"ok": True}}},
                ]
            },
        },
        "session_1",
    )

    assert message["role"] == "assistant"
    assert message["parts"][0]["type"] == "markdown"
    assert message["parts"][1]["type"] == "step_ref"
    assert message["parts"][2]["type"] == "step_ref"
    assert message["parts"][3]["type"] == "tool_result"


def test_project_session_event_skips_thought_text() -> None:
    message = project_session_event(
        {
            "id": "evt_thought",
            "author": "assistant",
            "timestamp": 1_717_171_717,
            "content": {
                "parts": [
                    {"text": "hidden reasoning", "thought": True},
                    {"text": "visible answer"},
                ]
            },
        },
        "session_thought",
    )

    assert message is not None
    assert message["parts"] == [{"type": "markdown", "text": "visible answer"}]


def test_project_session_event_projects_inline_image_and_file_parts() -> None:
    message = project_session_event(
        {
            "id": "evt_artifacts",
            "author": "user",
            "timestamp": 1_717_171_717,
            "content": {
                "parts": [
                    {
                        "inline_data": {
                            "data": "aW1hZ2U=",
                            "mime_type": "image/png",
                            "display_name": "diagram.png",
                        }
                    },
                    {
                        "inline_data": {
                            "data": "bm90ZXM=",
                            "mime_type": "text/plain",
                            "display_name": "notes.txt",
                        }
                    },
                ]
            },
        },
        "session_artifacts",
    )

    assert message is not None
    assert message["parts"][0] == {
        "type": "image",
        "text": "diagram.png",
        "url": "data:image/png;base64,aW1hZ2U=",
        "mime_type": "image/png",
    }
    assert message["parts"][1] == {
        "type": "file",
        "text": "Attached file",
        "file_name": "notes.txt",
        "size_bytes": 5,
        "mime_type": "text/plain",
    }


def test_project_session_event_skips_unrenderable_events() -> None:
    message = project_session_event(
        {
            "id": "evt_2",
            "author": "system",
            "timestamp": 1_717_171_718,
            "content": {"parts": [{}]},
        },
        "session_2",
    )

    assert message is None


def test_project_session_event_strips_request_time_prefix_from_user_text() -> None:
    message = project_session_event(
        {
            "id": "evt_request_time",
            "author": "user",
            "timestamp": 1_717_171_719,
            "content": {
                "parts": [
                    {
                        "text": (
                            "Current request time: 2026-04-03T12:32:17+08:00 (CST)\n"
                            "Use this as the reference 'now' for relative time expressions in this message.\n\n"
                            "今天日期给我一下"
                        )
                    }
                ]
            },
        },
        "session_request_time",
    )

    assert message is not None
    assert message["role"] == "user"
    assert message["parts"] == [{"type": "markdown", "text": "今天日期给我一下"}]


def test_create_run_streams_replayable_events(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    stdout_lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "event",
                    "event": {
                        "content": {
                            "parts": [
                                {"function_call": {"id": "call_1", "name": "inspect_repo", "args": {"path": "."}}},
                            ]
                        }
                    },
                }
            ),
            json.dumps({"type": "delta", "text": "hello"}),
            json.dumps({"type": "final", "text": "hello world"}),
        ]
    )

    coordinator, runtime = _coordinator_with_runtime(tmp_path)
    runtime.create_session_sync("writer", user_id="owner", session_id="session_1")
    payload = coordinator.create_run("writer", "session_1", "hi", user_id="owner")
    assert payload["ok"] is True
    run_id = payload["data"]["run"]["id"]

    handle = coordinator._runs[run_id]
    assert handle.done.wait(timeout=1.0)

    subscriber = coordinator.stream_run_events(run_id)
    assert subscriber is not None

    events: list[str] = []
    while True:
        item = subscriber.get(timeout=1.0)
        if item is None:
            break
        events.append(item.event)

    assert "run.started" in events
    assert "message.created" in events
    assert "step.updated" in events
    assert "message.delta" in events
    assert "message.completed" in events
    assert "run.finished" in events


def test_create_run_passes_persisted_session_model_override_to_runtime(tmp_path: Path) -> None:
    coordinator, runtime = _coordinator_with_runtime(tmp_path)
    runtime.create_session_sync("writer", user_id="owner", session_id="session_model")
    coordinator._session_metadata.update_model_profile(
        session_id="session_model",
        agent_id="writer",
        principal_id="owner",
        model_profile_id="reasoning",
    )

    payload = coordinator.create_run("writer", "session_model", "hi", user_id="owner")

    run_id = payload["data"]["run"]["id"]
    assert runtime.callbacks[run_id]["run_override"] == "reasoning"


def test_create_run_rejects_a_session_that_the_node_does_not_own(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer"}}),
        encoding="utf-8",
    )
    coordinator, _runtime = _coordinator_with_runtime(tmp_path)

    payload = coordinator.create_run("writer", "missing-session", "hi", user_id="owner")

    assert payload["ok"] is False
    assert payload["error"]["code"] == "SESSION_NOT_FOUND"


def test_session_artifact_upload_list_download_and_run_reference(tmp_path: Path) -> None:
    coordinator, supervisor = _coordinator_with_runtime(tmp_path)
    artifact_root = tmp_path / "artifacts"
    artifact_service = FileArtifactService(root_dir=str(artifact_root))
    supervisor.runtime_for = lambda _agent_id: SimpleNamespace(  # type: ignore[attr-defined]
        artifact_service=artifact_service,
        agent=SimpleNamespace(name="writer"),
    )
    created = coordinator.create_session("writer", user_id="owner")
    session_id = created["data"]["session"]["id"]

    uploaded = coordinator.upload_artifact(
        "writer",
        session_id,
        file_name="notes.txt",
        mime_type="text/plain",
        data_base64="aGVsbG8=",
        user_id="owner",
    )

    assert uploaded["ok"] is True
    artifact = uploaded["data"]["artifact"]
    assert artifact["file_name"] == "notes.txt"
    assert artifact["size_bytes"] == 5
    restarted_artifact_service = FileArtifactService(root_dir=str(artifact_root))
    supervisor.runtime_for = lambda _agent_id: SimpleNamespace(  # type: ignore[attr-defined]
        artifact_service=restarted_artifact_service,
        agent=SimpleNamespace(name="writer"),
    )
    listed = coordinator.list_artifacts("writer", session_id, user_id="owner")
    assert listed["data"]["items"] == [artifact]
    metadata, content = coordinator.load_artifact(
        "writer",
        session_id,
        key=artifact["key"],
        version=artifact["version"],
        user_id="owner",
    )
    assert metadata["data"]["mime_type"] == "text/plain"
    assert content == b"hello"

    run = coordinator.create_run(
        "writer",
        session_id,
        "Summarize the attachment",
        artifact_refs=[{"key": artifact["key"], "version": artifact["version"]}],
        user_id="owner",
    )
    callback = supervisor.callbacks[run["data"]["run"]["id"]]
    assert "[Attachment: notes.txt]" in callback["artifact_parts"][0].text
    assert "hello" in callback["artifact_parts"][0].text


def test_session_artifact_enforces_the_node_message_total_limit(tmp_path: Path, monkeypatch) -> None:
    coordinator, supervisor = _coordinator_with_runtime(tmp_path)
    artifact_service = FileArtifactService(root_dir=str(tmp_path / "artifacts"))
    supervisor.runtime_for = lambda _agent_id: SimpleNamespace(  # type: ignore[attr-defined]
        artifact_service=artifact_service,
        agent=SimpleNamespace(name="writer"),
    )
    session_id = coordinator.create_session("writer", user_id="owner")["data"]["session"]["id"]
    monkeypatch.setattr("openppx.runtime.client_api_service.MAX_MESSAGE_ATTACHMENT_BYTES", 10)
    artifacts = [
        coordinator.upload_artifact(
            "writer",
            session_id,
            file_name=f"notes-{index}.txt",
            mime_type="text/plain",
            data_base64="aGVsbG8h",
            user_id="owner",
        )["data"]["artifact"]
        for index in range(2)
    ]

    result = coordinator.create_run(
        "writer",
        session_id,
        "Read both",
        artifact_refs=[{"key": item["key"], "version": item["version"]} for item in artifacts],
        user_id="owner",
    )

    assert result["error"]["code"] == "INVALID_ARTIFACT"
    assert "cannot exceed" in result["error"]["message"]


def test_session_artifact_rejects_invalid_names_and_cross_session_access(tmp_path: Path) -> None:
    coordinator, supervisor = _coordinator_with_runtime(tmp_path)
    artifact_service = FileArtifactService(root_dir=str(tmp_path / "artifacts"))
    supervisor.runtime_for = lambda _agent_id: SimpleNamespace(  # type: ignore[attr-defined]
        artifact_service=artifact_service,
        agent=SimpleNamespace(name="writer"),
    )
    first = coordinator.create_session("writer", user_id="owner")["data"]["session"]["id"]
    second = coordinator.create_session("writer", user_id="owner")["data"]["session"]["id"]

    invalid_name = coordinator.upload_artifact(
        "writer",
        first,
        file_name="../secret.txt",
        mime_type="text/plain",
        data_base64="aGVsbG8=",
        user_id="owner",
    )
    invalid_data = coordinator.upload_artifact(
        "writer",
        first,
        file_name="notes.txt",
        mime_type="text/plain",
        data_base64="not-base64",
        user_id="owner",
    )
    spoofed_mime = coordinator.upload_artifact(
        "writer",
        first,
        file_name="notes.txt",
        mime_type="image/png",
        data_base64="aGVsbG8=",
        user_id="owner",
    )
    uploaded = coordinator.upload_artifact(
        "writer",
        first,
        file_name="notes.txt",
        mime_type="text/plain",
        data_base64="aGVsbG8=",
        user_id="owner",
    )["data"]["artifact"]
    wrong_session, content = coordinator.load_artifact(
        "writer",
        second,
        key=uploaded["key"],
        version=uploaded["version"],
        user_id="owner",
    )

    assert invalid_name["error"]["code"] == "INVALID_ARTIFACT"
    assert invalid_data["error"]["code"] == "INVALID_ARTIFACT"
    assert spoofed_mime["error"]["code"] == "INVALID_ARTIFACT"
    assert "MIME type" in spoofed_mime["error"]["message"]
    assert wrong_session["error"]["code"] == "ARTIFACT_NOT_FOUND"
    assert content is None


def test_session_artifact_storage_errors_do_not_expose_sensitive_details(tmp_path: Path) -> None:
    class _FailingArtifactService:
        async def save_artifact(self, **_kwargs):
            raise RuntimeError("/Users/private/.openppx/token-secret")

        async def list_artifact_keys(self, **_kwargs):
            raise RuntimeError("/Users/private/.openppx/token-secret")

    coordinator, supervisor = _coordinator_with_runtime(tmp_path)
    failing_service = _FailingArtifactService()
    supervisor.runtime_for = lambda _agent_id: SimpleNamespace(  # type: ignore[attr-defined]
        artifact_service=failing_service,
        agent=SimpleNamespace(name="writer"),
    )
    session_id = coordinator.create_session("writer", user_id="owner")["data"]["session"]["id"]

    upload = coordinator.upload_artifact(
        "writer",
        session_id,
        file_name="notes.txt",
        mime_type="text/plain",
        data_base64="aGVsbG8=",
        user_id="owner",
    )
    listing = coordinator.list_artifacts("writer", session_id, user_id="owner")
    loaded, content = coordinator.load_artifact(
        "writer",
        session_id,
        key="uploads/missing/notes.txt",
        user_id="owner",
    )

    assert upload["error"]["code"] == "ARTIFACT_SAVE_FAILED"
    assert upload["error"]["message"] == "The attachment could not be saved."
    assert listing["error"]["code"] == "ARTIFACT_LIST_FAILED"
    assert listing["error"]["message"] == "Artifacts could not be listed."
    assert loaded["error"]["code"] == "ARTIFACT_LOAD_FAILED"
    assert loaded["error"]["message"] == "The Artifact could not be loaded."
    assert content is None
    assert "private" not in json.dumps([upload, listing, loaded])
    assert "token-secret" not in json.dumps([upload, listing, loaded])


def test_create_run_treats_empty_final_as_failed_message(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    stdout_lines = json.dumps({"type": "final", "text": ""})

    coordinator, runtime = _coordinator_with_runtime(
        tmp_path,
        supervisor=_FakeRuntimeSupervisor(run_mode="empty"),
    )
    runtime.create_session_sync("writer", user_id="owner", session_id="session_empty_final")
    payload = coordinator.create_run("writer", "session_empty_final", "hi", user_id="owner")
    assert payload["ok"] is True

    run_id = payload["data"]["run"]["id"]
    handle = coordinator._runs[run_id]
    assert handle.done.wait(timeout=1.0)

    subscriber = coordinator.stream_run_events(run_id)
    assert subscriber is not None

    events = []
    while True:
        item = subscriber.get(timeout=1.0)
        if item is None:
            break
        events.append((item.event, item.payload))

    by_event = {name: payload for name, payload in events}
    assert "message.completed" not in by_event
    assert by_event["message.failed"]["status"] == "failed"
    assert by_event["message.failed"]["error"]["text"]
    assert by_event["run.finished"]["status"] == "failed"


def test_create_run_emits_normalized_event_context(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    stdout_lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "event",
                    "event": {
                        "content": {
                            "parts": [
                                {"function_call": {"id": "call_ctx", "name": "inspect_repo", "args": {"path": "."}}},
                                {"function_response": {"id": "call_ctx", "name": "inspect_repo", "response": {"ok": True}}},
                            ]
                        }
                    },
                }
            ),
            json.dumps({"type": "delta", "text": "hello"}),
            json.dumps({"type": "final", "text": "hello world"}),
        ]
    )

    coordinator, runtime = _coordinator_with_runtime(tmp_path)
    runtime.create_session_sync("writer", user_id="owner", session_id="session_ctx")
    payload = coordinator.create_run("writer", "session_ctx", "hi", user_id="owner")
    run_id = payload["data"]["run"]["id"]
    handle = coordinator._runs[run_id]
    assert handle.done.wait(timeout=1.0)

    subscriber = coordinator.stream_run_events(run_id)
    assert subscriber is not None

    envelopes = []
    while True:
        item = subscriber.get(timeout=1.0)
        if item is None:
            break
        envelopes.append((item.event, item.payload))

    by_event = {name: payload for name, payload in envelopes}

    assert by_event["message.created"]["agent_id"] == "writer"
    assert by_event["message.created"]["session_id"] == "session_ctx"
    assert by_event["message.created"]["message_id"] == handle.assistant_message_id
    assert by_event["step.updated"]["step"]["type"] == "step_ref"
    assert by_event["message.delta"]["status"] == "streaming"
    assert by_event["message.completed"]["status"] == "completed"
    assert by_event["run.finished"]["message_id"] == handle.assistant_message_id


def test_cancel_run_emits_cancelled_message_and_run(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    coordinator, runtime = _coordinator_with_runtime(
        tmp_path,
        supervisor=_FakeRuntimeSupervisor(run_mode="pending"),
    )
    runtime.create_session_sync("writer", user_id="owner", session_id="session_cancel")
    goal, _flow = coordinator._control_plane.goal_store.create_goal(
        session_id="session_cancel",
        agent_id="writer",
        user_id="owner",
        objective="Finish the cancellable task",
        created_by="owner",
    )
    payload = coordinator.create_run("writer", "session_cancel", "hi", user_id="owner")
    run_id = payload["data"]["run"]["id"]
    cancel_payload = coordinator.cancel_run(run_id)

    assert cancel_payload["ok"] is True

    subscriber = coordinator.stream_run_events(run_id)
    assert subscriber is not None
    events = []
    while True:
        item = subscriber.get(timeout=1.0)
        if item is None:
            break
        events.append((item.event, item.payload))

    by_event = {name: payload for name, payload in events}
    assert by_event["message.cancelled"]["status"] == "cancelled"
    assert by_event["message.cancelled"]["message_id"].startswith("msg_")
    assert by_event["run.cancelled"]["status"] == "cancelled"
    paused_goal = coordinator._control_plane.goal_store.get_goal(goal.goal_id)
    paused_flow = coordinator._control_plane.goal_store.get_flow(goal.active_flow_id)
    assert paused_goal is not None
    assert paused_goal.status == "paused"
    assert paused_flow is not None
    assert paused_flow.status == "paused"
    assert paused_flow.wait_reason["kind"] == "paused"


def test_failed_run_blocks_the_active_goal(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer"}}),
        encoding="utf-8",
    )

    coordinator, runtime = _coordinator_with_runtime(
        tmp_path,
        supervisor=_FakeRuntimeSupervisor(run_mode="pending"),
    )
    runtime.create_session_sync("writer", user_id="owner", session_id="session_failure")
    goal, _flow = coordinator._control_plane.goal_store.create_goal(
        session_id="session_failure",
        agent_id="writer",
        user_id="owner",
        objective="Finish the failing task",
        created_by="owner",
    )
    payload = coordinator.create_run("writer", "session_failure", "hi", user_id="owner")
    run_id = payload["data"]["run"]["id"]

    runtime.callbacks[run_id]["on_error"](RuntimeError("model transport failed"))

    blocked_goal = coordinator._control_plane.goal_store.get_goal(goal.goal_id)
    blocked_flow = coordinator._control_plane.goal_store.get_flow(goal.active_flow_id)
    assert blocked_goal is not None
    assert blocked_goal.status == "blocked"
    assert blocked_flow is not None
    assert blocked_flow.status == "blocked"
    assert blocked_flow.wait_reason["kind"] == "blocked"
    assert "model transport failed" in blocked_flow.wait_reason["message"]


def test_run_event_replay_uses_sequence_after_two_digit_event_id() -> None:
    handle = RunHandle(run_id="run_resume", agent_id="writer", session_id="session_resume")
    for index in range(12):
        handle.publish("message.delta", {"index": index + 1})
    handle.finish()

    subscriber = handle.subscribe(last_event_id="run_resume:9")
    replay = []
    while True:
        item = subscriber.get(timeout=1.0)
        if item is None:
            break
        replay.append(item)

    assert [item.seq for item in replay] == [10, 11, 12]


def test_create_run_tolerates_null_long_running_tool_ids(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    stdout_lines = "\n".join(
        [
            json.dumps(
                {
                    "type": "event",
                    "event": {
                        "long_running_tool_ids": None,
                        "content": {
                            "parts": [
                                {"function_call": {"id": "call_2", "name": "inspect_repo", "args": {"path": "."}}},
                            ]
                        },
                    },
                }
            ),
            json.dumps({"type": "final", "text": "done"}),
        ]
    )

    coordinator, runtime = _coordinator_with_runtime(tmp_path)
    runtime.create_session_sync("writer", user_id="owner", session_id="session_2")
    payload = coordinator.create_run("writer", "session_2", "hi", user_id="owner")
    assert payload["ok"] is True

    handle = coordinator._runs[payload["data"]["run"]["id"]]
    assert handle.done.wait(timeout=1.0)
    assert handle.failed is False


def test_client_api_reads_sessions_directly_without_worker(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "database").mkdir()
    config_path = agent_dir / "config.json"
    config_path.write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    async def _seed() -> None:
        service = create_session_service(
            SessionConfig(db_url=f"sqlite+aiosqlite:///{agent_dir / 'database' / 'sessions.db'}")
        )
        async with service:
            session = await service.create_session(
                app_name="openppx",
                user_id="ppx-client-user",
                session_id="writer-seeded",
            )
            await service.append_event(
                session=session,
                event=Event(
                    invocation_id="inv-user",
                    author="user",
                    content=types.Content(
                        role="user",
                        parts=[
                            types.Part.from_text(
                                text=(
                                    "Current request time: 2026-06-10T16:32:17+08:00 (CST)\n"
                                    "Use this as the reference 'now' for relative time expressions in this message.\n\n"
                                    "帮我查一下深圳到青岛的火车和费用"
                                )
                            )
                        ],
                    ),
                ),
            )
            await service.append_event(
                session=session,
                event=Event(
                    invocation_id="inv-1",
                    author="assistant",
                    content=types.Content(role="model", parts=[types.Part.from_text(text="Hello direct path")]),
                ),
            )

    import asyncio

    asyncio.run(_seed())

    runtime = _FakeRuntimeSupervisor()
    runtime.sessions[("writer", "owner")] = [
        SimpleNamespace(
            id="writer-seeded",
            last_update_time=1_700_000_000,
            events=[
                Event(
                    invocation_id="inv-user-runtime",
                    author="user",
                    content=types.Content(
                        role="user",
                        parts=[types.Part.from_text(text="帮我查一下深圳到青岛的火车和费用")],
                    ),
                ),
                Event(
                    invocation_id="inv-assistant-runtime",
                    author="assistant",
                    content=types.Content(role="model", parts=[types.Part.from_text(text="Hello direct path")]),
                ),
            ],
        )
    ]
    coordinator, _runtime = _coordinator_with_runtime(tmp_path, supervisor=runtime)
    sessions = coordinator.list_sessions("writer", user_id="owner")
    assert sessions["ok"] is True
    assert sessions["data"]["items"][0]["id"] == "writer-seeded"
    assert sessions["data"]["items"][0]["title"] == "帮我查一下深圳到青岛的火车和费用"

    messages = coordinator.get_session_messages("writer-seeded", user_id="owner")
    assert messages["ok"] is True
    assert messages["data"]["items"][0]["parts"][0]["text"] == "帮我查一下深圳到青岛的火车和费用"
    assert messages["data"]["items"][1]["parts"][0]["text"] == "Hello direct path"


def test_list_sessions_uses_short_cache(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    calls = {"count": 0}

    def _fake_read(self, config_path: Path, *, user_id: str) -> list[dict[str, object]]:
        calls["count"] += 1
        return [{"id": "session-1", "last_update_time": 1_700_000_000, "last_preview": "cached"}]

    monkeypatch.setattr(ClientApiCoordinator, "_read_sessions_for_principal", _fake_read)

    coordinator, _runtime = _coordinator_with_runtime(tmp_path)
    first = coordinator.list_sessions("writer", user_id="owner")
    second = coordinator.list_sessions("writer", user_id="owner")

    assert first["ok"] is True
    assert second["ok"] is True
    assert calls["count"] == 1


def test_list_sessions_does_not_synthesize_a_preview(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer"}}),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        ClientApiCoordinator,
        "_read_sessions_for_principal",
        lambda self, config_path, *, user_id: [{"id": "session-1", "last_update_time": 1_700_000_000}],
    )

    coordinator, _runtime = _coordinator_with_runtime(tmp_path)
    sessions = coordinator.list_sessions("writer", user_id="owner")

    assert sessions["ok"] is True
    assert sessions["data"]["items"][0]["last_message_preview"] == ""


def test_create_session_invalidates_session_list_cache(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    calls = {"count": 0}

    def _fake_read(self, config_path: Path, *, user_id: str) -> list[dict[str, object]]:
        calls["count"] += 1
        return [{"id": f"session-{calls['count']}", "last_update_time": 1_700_000_000, "last_preview": "cached"}]

    monkeypatch.setattr(ClientApiCoordinator, "_read_sessions_for_principal", _fake_read)

    coordinator, _runtime = _coordinator_with_runtime(tmp_path)
    before = coordinator.list_sessions("writer", user_id="owner")
    created = coordinator.create_session("writer", user_id="owner")
    after = coordinator.list_sessions("writer", user_id="owner")

    assert before["data"]["items"][0]["id"] == "session-1"
    assert created["ok"] is True
    assert created["data"]["session"]["title"] == "New chat"
    assert after["data"]["items"][0]["id"] == "session-2"
    assert calls["count"] == 2


def test_client_api_owner_can_list_participant_sessions(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "database").mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    db_path = tmp_path / "identity.db"
    memory_db_path = tmp_path / "memory.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    owner = identity_store.put_principal(_principal(principal_id="owner"))
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.set_agent_owner(agent_id="writer", owner_principal_id=owner.principal_id)
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=SQLiteMemoryService(db_path=memory_db_path),
        audit_db_path=memory_db_path,
    )

    async def _seed() -> None:
        service = create_session_service(
            SessionConfig(db_url=f"sqlite+aiosqlite:///{agent_dir / 'database' / 'sessions.db'}")
        )
        async with service:
            session = await service.create_session(
                app_name="openppx",
                user_id=participant.principal_id,
                session_id="participant-session",
            )
            await service.append_event(
                session=session,
                event=Event(
                    invocation_id="inv-participant",
                    author="assistant",
                    content=types.Content(role="model", parts=[types.Part.from_text(text="Participant history")]),
                ),
            )

    import asyncio

    asyncio.run(_seed())

    runtime = _FakeRuntimeSupervisor()
    runtime.sessions[("writer", participant.principal_id)] = [
        SimpleNamespace(
            id="participant-session",
            last_update_time=1_700_000_000,
            events=[
                Event(
                    invocation_id="inv-participant-runtime",
                    author="assistant",
                    content=types.Content(role="model", parts=[types.Part.from_text(text="Participant history")]),
                )
            ],
        )
    ]
    coordinator, _runtime = _coordinator_with_runtime(
        tmp_path,
        supervisor=runtime,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )
    sessions = coordinator.list_sessions("writer", user_id=owner.principal_id)

    assert sessions["ok"] is True
    assert sessions["data"]["items"][0]["id"] == "participant-session"
    assert sessions["data"]["items"][0]["subject_principal_id"] == participant.principal_id


def test_client_api_owner_cannot_run_in_participant_session(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "database").mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    db_path = tmp_path / "identity.db"
    memory_db_path = tmp_path / "memory.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    owner = identity_store.put_principal(_principal(principal_id="owner"))
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.set_agent_owner(agent_id="writer", owner_principal_id=owner.principal_id)
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=SQLiteMemoryService(db_path=memory_db_path),
        audit_db_path=memory_db_path,
    )

    async def _seed() -> None:
        service = create_session_service(
            SessionConfig(db_url=f"sqlite+aiosqlite:///{agent_dir / 'database' / 'sessions.db'}")
        )
        async with service:
            await service.create_session(
                app_name="openppx",
                user_id=participant.principal_id,
                session_id="participant-session",
            )

    import asyncio

    asyncio.run(_seed())

    runtime = _FakeRuntimeSupervisor(run_mode="pending")
    runtime.sessions[("writer", participant.principal_id)] = [
        SimpleNamespace(id="participant-session", last_update_time=1_700_000_000, events=[])
    ]
    coordinator, _runtime = _coordinator_with_runtime(
        tmp_path,
        supervisor=runtime,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )
    payload = coordinator.create_run("writer", "participant-session", "hello", user_id=owner.principal_id)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "ACCESS_DENIED"
    assert payload["error"]["details"]["reason"] == "run_requires_session_owner"


def test_client_api_owner_can_query_participant_memory(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(json.dumps({"agent": {"workspace": "workspace/writer"}}), encoding="utf-8")

    db_path = tmp_path / "identity.db"
    memory_db_path = tmp_path / "memory.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    owner = identity_store.put_principal(_principal(principal_id="owner"))
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.set_agent_owner(agent_id="writer", owner_principal_id=owner.principal_id)
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    memory_service = SQLiteMemoryService(db_path=memory_db_path)
    asyncio.run(
        memory_service.add_memory(
            app_name="openppx",
            user_id=participant.principal_id,
            memories=[_memory("remember the launch checklist", timestamp="2026-04-18T10:00:00+08:00")],
        )
    )
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=memory_service,
        audit_db_path=memory_db_path,
    )

    coordinator = ClientApiCoordinator(
        data_dir=tmp_path,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )
    payload = coordinator.search_memory("writer", "launch", user_id=owner.principal_id)

    assert payload["ok"] is True
    assert payload["data"]["items"][0]["subject_principal_id"] == participant.principal_id
    assert "launch checklist" in payload["data"]["items"][0]["text"]


def test_client_api_get_agent_access_uses_strict_agent_resource(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps(
            {
                "agent": {
                    "workspace": "workspace/writer",
                    "privilegeLevel": "high",
                    "ownerPrincipalId": "owner",
                }
            }
        ),
        encoding="utf-8",
    )

    coordinator = ClientApiCoordinator(data_dir=tmp_path)
    payload = coordinator.get_agent_access("writer", user_id="owner")

    assert payload["ok"] is True
    assert payload["data"]["agent"]["privilege_level"] == "low"
    assert payload["data"]["agent"]["owner_principal_id"] == "owner"
    assert payload["data"]["agent"]["owner_configured"] is True
    assert payload["data"]["agent"]["metadata"]["owner_source"] == "config"
    assert payload["data"]["requester"]["relation"] == "owner"
    assert payload["data"]["requester"]["scope_kind"] == "agent"
    assert payload["data"]["memberships"] == []


def test_client_api_get_agent_access_filters_memberships_by_visible_scope(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps(
            {
                "agent": {
                    "workspace": "workspace/writer",
                    "ownerPrincipalId": "owner",
                }
            }
        ),
        encoding="utf-8",
    )

    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=SQLiteMemoryService(db_path=tmp_path / "memory.db"),
        audit_db_path=tmp_path / "memory.db",
    )

    coordinator = ClientApiCoordinator(
        data_dir=tmp_path,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )
    payload = coordinator.get_agent_access("writer", user_id=participant.principal_id)

    assert payload["ok"] is True
    assert payload["data"]["requester"]["relation"] == "participant"
    assert payload["data"]["requester"]["scope_kind"] == "self"
    assert payload["data"]["agent"]["owner_configured"] is True
    assert payload["data"]["agent"]["owner_principal_id"] is None
    assert payload["data"]["memberships"] == [
        {
            "principal_id": "participant",
            "relation": "participant",
            "joined_at_ms": payload["data"]["memberships"][0]["joined_at_ms"],
            "metadata": {},
            "display_name": "participant",
            "principal_type": "human",
            "privilege_level": "minimal",
        }
    ]


def test_client_api_owner_can_manage_participant_membership(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    coordinator = ClientApiCoordinator(data_dir=tmp_path)
    create_payload = coordinator.upsert_agent_membership("writer", "participant", user_id="owner")
    access_payload = coordinator.get_agent_access("writer", user_id="owner")
    delete_payload = coordinator.delete_agent_membership("writer", "participant", user_id="owner")

    assert create_payload["ok"] is True
    assert create_payload["data"]["membership"]["principal_id"] == "participant"
    assert create_payload["data"]["membership"]["relation"] == "participant"
    assert access_payload["ok"] is True
    assert access_payload["data"]["requester"]["capabilities"]["can_manage_memberships"] is True
    assert access_payload["data"]["requester"]["capabilities"]["can_read_access_audit"] is True
    assert access_payload["data"]["requester"]["capabilities"]["can_read_admin_audit"] is True
    assert access_payload["data"]["requester"]["capabilities"]["can_change_owner"] is False
    assert {item["principal_id"] for item in access_payload["data"]["memberships"]} == {"participant"}
    assert delete_payload["ok"] is True
    assert delete_payload["data"]["deleted"] is True


def test_client_api_participant_cannot_manage_memberships(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=SQLiteMemoryService(db_path=tmp_path / "memory.db"),
        audit_db_path=tmp_path / "memory.db",
    )
    coordinator = ClientApiCoordinator(
        data_dir=tmp_path,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )

    payload = coordinator.upsert_agent_membership("writer", "another-user", user_id=participant.principal_id)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "ACCESS_DENIED"
    assert payload["error"]["details"]["reason"] == "insufficient_agent_admin_role"


def test_client_api_root_can_change_owner(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    root = identity_store.put_principal(_principal(principal_id="root-user", privilege_level="root"))
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=SQLiteMemoryService(db_path=tmp_path / "memory.db"),
        audit_db_path=tmp_path / "memory.db",
    )
    coordinator = ClientApiCoordinator(
        data_dir=tmp_path,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )

    payload = coordinator.set_agent_owner("writer", "new-owner", user_id=root.principal_id)
    access_payload = coordinator.get_agent_access("writer", user_id=root.principal_id)

    assert payload["ok"] is True
    assert payload["data"]["agent"]["owner_principal_id"] == "new-owner"
    assert payload["data"]["agent"]["metadata"]["owner_source"] == "client_api"
    assert access_payload["ok"] is True
    assert access_payload["data"]["agent"]["owner_principal_id"] == "new-owner"
    assert access_payload["data"]["requester"]["capabilities"]["can_read_access_audit"] is True
    assert access_payload["data"]["requester"]["capabilities"]["can_read_admin_audit"] is True
    assert access_payload["data"]["requester"]["capabilities"]["can_change_owner"] is True


def test_client_api_owner_can_read_access_mutation_audit(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    coordinator = ClientApiCoordinator(data_dir=tmp_path)
    create_payload = coordinator.upsert_agent_membership("writer", "participant", user_id="owner")
    delete_payload = coordinator.delete_agent_membership("writer", "participant", user_id="owner")
    audit_payload = coordinator.get_access_audit("writer", user_id="owner", limit=10, category="mutation")

    assert create_payload["ok"] is True
    assert delete_payload["ok"] is True
    assert audit_payload["ok"] is True
    assert audit_payload["data"]["requester"]["relation"] == "owner"
    assert audit_payload["data"]["category"] == "mutation"
    assert [item["action"] for item in audit_payload["data"]["items"][:2]] == [
        "delete_membership",
        "upsert_membership",
    ]
    newest = audit_payload["data"]["items"][0]
    assert newest["actor_principal_id"] == "owner"
    assert newest["actor_relation"] == "owner"
    assert newest["target_principal_id"] == "participant"
    assert newest["details"]["deleted"] is True


def test_client_api_participant_cannot_read_access_mutation_audit(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=SQLiteMemoryService(db_path=tmp_path / "memory.db"),
        audit_db_path=tmp_path / "memory.db",
    )
    coordinator = ClientApiCoordinator(
        data_dir=tmp_path,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )

    payload = coordinator.get_access_audit("writer", user_id=participant.principal_id, limit=10)

    assert payload["ok"] is False
    assert payload["error"]["code"] == "ACCESS_DENIED"
    assert payload["error"]["details"]["reason"] == "insufficient_agent_admin_role"


def test_client_api_owner_can_read_unified_admin_audit(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    coordinator = ClientApiCoordinator(data_dir=tmp_path)
    access_payload = coordinator.get_agent_access("writer", user_id="owner")
    create_payload = coordinator.upsert_agent_membership("writer", "participant", user_id="owner")
    admin_audit_payload = coordinator.get_access_audit("writer", user_id="owner", limit=10, category="all")

    assert access_payload["ok"] is True
    assert create_payload["ok"] is True
    assert admin_audit_payload["ok"] is True
    assert admin_audit_payload["data"]["category"] == "all"
    actions = [item["action"] for item in admin_audit_payload["data"]["items"]]
    assert "read_access" in actions
    assert "upsert_membership" in actions
    assert "read_admin_audit" not in actions


def test_client_api_batch_participant_management_supports_dry_run_and_apply(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    coordinator = ClientApiCoordinator(data_dir=tmp_path)
    dry_run_payload = coordinator.batch_add_participants(
        "writer",
        ["alice", "bob", "alice"],
        user_id="owner",
        dry_run=True,
    )
    access_before = coordinator.get_agent_access("writer", user_id="owner")
    apply_payload = coordinator.batch_add_participants("writer", ["alice", "bob"], user_id="owner")
    remove_payload = coordinator.batch_remove_participants("writer", ["bob", "nobody"], user_id="owner")
    sync_payload = coordinator.sync_participants("writer", ["carol"], user_id="owner")
    mutation_audit_payload = coordinator.get_access_audit("writer", user_id="owner", limit=10, category="mutation")

    assert dry_run_payload["ok"] is True
    assert dry_run_payload["data"]["dry_run"] is True
    assert dry_run_payload["data"]["applied"] is False
    assert dry_run_payload["data"]["added_principal_ids"] == ["alice", "bob"]
    assert access_before["ok"] is True
    assert access_before["data"]["memberships"] == []

    assert apply_payload["ok"] is True
    assert apply_payload["data"]["added_principal_ids"] == ["alice", "bob"]
    assert remove_payload["ok"] is True
    assert remove_payload["data"]["removed_principal_ids"] == ["bob"]
    assert remove_payload["data"]["unchanged_principal_ids"] == ["nobody"]
    assert sync_payload["ok"] is True
    assert sync_payload["data"]["added_principal_ids"] == ["carol"]
    assert sync_payload["data"]["removed_principal_ids"] == ["alice"]

    access_after = coordinator.get_agent_access("writer", user_id="owner")
    assert {item["principal_id"] for item in access_after["data"]["memberships"]} == {"carol"}

    actions = [item["action"] for item in mutation_audit_payload["data"]["items"]]
    assert actions[:4] == [
        "sync_participants",
        "batch_remove_participants",
        "batch_add_participants",
        "batch_add_participants",
    ]
    assert mutation_audit_payload["data"]["items"][0]["details"]["dry_run"] is False
    assert mutation_audit_payload["data"]["items"][3]["details"]["dry_run"] is True


def test_client_api_participant_batch_management_is_denied_and_audited(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    db_path = tmp_path / "identity.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    owner = identity_store.put_principal(_principal(principal_id="owner"))
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.set_agent_owner(agent_id="writer", owner_principal_id=owner.principal_id)
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=SQLiteMemoryService(db_path=tmp_path / "memory.db"),
        audit_db_path=tmp_path / "memory.db",
    )
    coordinator = ClientApiCoordinator(
        data_dir=tmp_path,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )

    denied_payload = coordinator.batch_add_participants(
        "writer",
        ["another-user"],
        user_id=participant.principal_id,
    )
    admin_audit_payload = coordinator.get_access_audit("writer", user_id=owner.principal_id, limit=10, category="all")

    assert denied_payload["ok"] is False
    assert denied_payload["error"]["details"]["reason"] == "insufficient_agent_admin_role"
    newest = admin_audit_payload["data"]["items"][0]
    assert newest["action"] == "batch_add_participants"
    assert newest["details"]["allowed"] is False


def test_client_api_owner_can_read_memory_audit(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    db_path = tmp_path / "identity.db"
    memory_db_path = tmp_path / "memory.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    owner = identity_store.put_principal(_principal(principal_id="owner"))
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    access_store.set_agent_owner(agent_id="writer", owner_principal_id=owner.principal_id)
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=SQLiteMemoryService(db_path=memory_db_path),
        audit_db_path=memory_db_path,
    )
    asyncio.run(
        query_service.search(
            agent_id="writer",
            requester_principal_id=participant.principal_id,
            query="launch",
        )
    )

    coordinator = ClientApiCoordinator(
        data_dir=tmp_path,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )
    payload = coordinator.get_memory_audit("writer", user_id=owner.principal_id, limit=10)

    assert payload["ok"] is True
    assert payload["data"]["requester"]["relation"] == "owner"
    assert payload["data"]["items"][0]["requester_principal_id"] == participant.principal_id


def test_client_api_participant_memory_audit_stays_self_scoped(tmp_path: Path) -> None:
    (tmp_path / "global_config.json").write_text(
        json.dumps({"agents": [{"name": "writer", "enabled": True}]}),
        encoding="utf-8",
    )
    agent_dir = tmp_path / "writer"
    agent_dir.mkdir()
    (agent_dir / "config.json").write_text(
        json.dumps({"agent": {"workspace": "workspace/writer", "ownerPrincipalId": "owner"}}),
        encoding="utf-8",
    )

    db_path = tmp_path / "identity.db"
    memory_db_path = tmp_path / "memory.db"
    identity_store = IdentityStore(db_path=db_path)
    access_store = AgentAccessStore(db_path=db_path)
    participant = identity_store.put_principal(_principal(principal_id="participant"))
    other = identity_store.put_principal(_principal(principal_id="other"))
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=participant.principal_id, relation="participant")
    )
    access_store.upsert_membership(
        AgentMembership(agent_id="writer", principal_id=other.principal_id, relation="participant")
    )
    policy = AccessPolicy(identity_store=identity_store, agent_access_store=access_store)
    query_service = MemoryQueryService(
        identity_store=identity_store,
        access_policy=policy,
        memory_service=SQLiteMemoryService(db_path=memory_db_path),
        audit_db_path=memory_db_path,
    )
    asyncio.run(
        query_service.search(
            agent_id="writer",
            requester_principal_id=participant.principal_id,
            query="alpha",
        )
    )
    asyncio.run(
        query_service.search(
            agent_id="writer",
            requester_principal_id=other.principal_id,
            query="beta",
        )
    )

    coordinator = ClientApiCoordinator(
        data_dir=tmp_path,
        identity_store=identity_store,
        agent_access_store=access_store,
        access_policy=policy,
        memory_query_service=query_service,
    )
    payload = coordinator.get_memory_audit("writer", user_id=participant.principal_id, limit=10)

    assert payload["ok"] is True
    assert payload["data"]["requester"]["scope_kind"] == "self"
    assert [item["requester_principal_id"] for item in payload["data"]["items"]] == [participant.principal_id]
