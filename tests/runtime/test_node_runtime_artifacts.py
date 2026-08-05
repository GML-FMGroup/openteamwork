from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.adk.artifacts import FileArtifactService
from google.adk.sessions import InMemorySessionService
from google.genai import types

from openppx.runtime.node_runtime import NodeRuntimeSupervisor


def _supervisor(tmp_path):
    session_service = InMemorySessionService()
    artifact_service = FileArtifactService(root_dir=tmp_path / "artifacts")
    runtime = SimpleNamespace(
        agent=SimpleNamespace(name="writer"),
        session_service=session_service,
        artifact_service=artifact_service,
    )
    supervisor = NodeRuntimeSupervisor(config_service=None, assembler=None)  # type: ignore[arg-type]
    supervisor.runtime_for = lambda _agent_id: runtime  # type: ignore[method-assign]
    return supervisor, runtime


async def _save_upload(runtime, session_id: str, *, user_id: str = "owner") -> str:
    key = "uploads/artifact_test/notes.txt"
    await runtime.artifact_service.save_artifact(
        app_name=runtime.agent.name,
        user_id=user_id,
        session_id=session_id,
        filename=key,
        artifact=types.Part(
            inline_data=types.Blob(data=b"hello", mime_type="text/plain", display_name="notes.txt")
        ),
        custom_metadata={
            "artifact_id": "artifact_test",
            "source": "user_upload",
            "file_name": "notes.txt",
            "size_bytes": 5,
        },
    )
    return key


def test_fork_session_copies_session_scoped_artifacts(tmp_path) -> None:
    supervisor, runtime = _supervisor(tmp_path)

    async def scenario() -> None:
        source = await supervisor.create_session("writer", user_id="owner", session_id="source")
        key = await _save_upload(runtime, str(source.id))
        await runtime.artifact_service.save_artifact(
            app_name=runtime.agent.name,
            user_id="owner",
            session_id=str(source.id),
            filename=key,
            artifact=types.Part(
                inline_data=types.Blob(data=b"updated", mime_type="text/plain", display_name="notes.txt")
            ),
            custom_metadata={
                "artifact_id": "artifact_test",
                "source": "user_upload",
                "file_name": "notes.txt",
                "size_bytes": 7,
            },
        )

        forked = await supervisor.fork_session("writer", user_id="owner", session_id=str(source.id))

        keys = await runtime.artifact_service.list_artifact_keys(
            app_name=runtime.agent.name,
            user_id="owner",
            session_id=str(forked.id),
        )
        versions = await runtime.artifact_service.list_artifact_versions(
            app_name=runtime.agent.name,
            user_id="owner",
            session_id=str(forked.id),
            filename=key,
        )
        copied = [
            await runtime.artifact_service.load_artifact(
                app_name=runtime.agent.name,
                user_id="owner",
                session_id=str(forked.id),
                filename=key,
                version=int(version.version),
            )
            for version in versions
        ]
        assert keys == [key]
        assert [version.version for version in versions] == [0, 1]
        assert [item.inline_data.data for item in copied] == [b"hello", b"updated"]

    asyncio.run(scenario())


def test_delete_session_removes_its_artifacts(tmp_path) -> None:
    supervisor, runtime = _supervisor(tmp_path)

    async def scenario() -> None:
        session = await supervisor.create_session("writer", user_id="owner", session_id="source")
        await _save_upload(runtime, str(session.id))

        await supervisor.delete_session("writer", user_id="owner", session_id=str(session.id))

        assert await supervisor.get_session("writer", user_id="owner", session_id=str(session.id)) is None
        assert await runtime.artifact_service.list_artifact_keys(
            app_name=runtime.agent.name,
            user_id="owner",
            session_id=str(session.id),
        ) == []

    asyncio.run(scenario())
