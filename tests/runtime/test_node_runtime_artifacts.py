from __future__ import annotations

import asyncio
from types import SimpleNamespace

from google.adk.artifacts import FileArtifactService
from google.adk.events.event import Event
from google.adk.sessions import InMemorySessionService
from google.genai import types

from openppx.runtime.adk_identity import LEGACY_ADK_APP_NAME, adk_app_name_for_agent_id
from openppx.runtime.node_runtime import NodeRuntimeSupervisor
from openppx.runtime.session_metadata_store import SessionMetadataStore


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


def test_migrate_legacy_sessions_assigns_ownership_and_removes_only_placeholders(
    tmp_path,
) -> None:
    session_service = InMemorySessionService()
    artifact_service = FileArtifactService(root_dir=tmp_path / "migration-artifacts")

    class _Repository:
        @staticmethod
        def list_agent_ids() -> tuple[str, ...]:
            return ("main", "research")

    assembler = SimpleNamespace(
        services=SimpleNamespace(
            session_service=session_service,
            artifact_service=artifact_service,
        )
    )
    supervisor = NodeRuntimeSupervisor(
        config_service=SimpleNamespace(repository=_Repository()),
        assembler=assembler,
    )
    metadata = SessionMetadataStore(tmp_path / "session_metadata.db")

    async def scenario() -> None:
        await session_service.create_session(
            app_name=LEGACY_ADK_APP_NAME,
            user_id="owner",
            session_id="placeholder",
        )
        fallback = await session_service.create_session(
            app_name=LEGACY_ADK_APP_NAME,
            user_id="owner",
            session_id="fallback",
        )
        await session_service.append_event(
            fallback,
            Event(
                id="fallback-event",
                author="user",
                content=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Keep this conversation")],
                ),
            ),
        )
        owned = await session_service.create_session(
            app_name=LEGACY_ADK_APP_NAME,
            user_id="owner",
            session_id="owned",
        )
        await session_service.append_event(
            owned,
            Event(
                id="owned-event",
                author="assistant",
                content=types.Content(
                    role="model",
                    parts=[types.Part.from_text(text="Research result")],
                ),
            ),
        )
        metadata.update(
            session_id="owned",
            agent_id="research",
            principal_id="owner",
            title="Research conversation",
        )
        key = "outputs/report.txt"
        await artifact_service.save_artifact(
            app_name=LEGACY_ADK_APP_NAME,
            user_id="owner",
            session_id="owned",
            filename=key,
            artifact=types.Part.from_bytes(data=b"report", mime_type="text/plain"),
            custom_metadata={"source": "agent_output"},
        )

        report = await supervisor.migrate_legacy_sessions(session_metadata=metadata)

        assert report.migrated == 2
        assert report.removed_placeholders == 1
        assert report.skipped == 0
        assert report.failed == 0
        legacy = await session_service.list_sessions(
            app_name=LEGACY_ADK_APP_NAME,
            user_id=None,
        )
        assert legacy.sessions == []

        migrated_fallback = await session_service.get_session(
            app_name=adk_app_name_for_agent_id("main"),
            user_id="owner",
            session_id="fallback",
        )
        assert migrated_fallback is not None
        assert [event.id for event in migrated_fallback.events] == ["fallback-event"]
        assert metadata.get("fallback").agent_id == "main"  # type: ignore[union-attr]

        migrated_owned = await session_service.get_session(
            app_name=adk_app_name_for_agent_id("research"),
            user_id="owner",
            session_id="owned",
        )
        assert migrated_owned is not None
        assert [event.id for event in migrated_owned.events] == ["owned-event"]
        assert metadata.get("owned").agent_id == "research"  # type: ignore[union-attr]
        versions = await artifact_service.list_artifact_versions(
            app_name=adk_app_name_for_agent_id("research"),
            user_id="owner",
            session_id="owned",
            filename=key,
        )
        copied = await artifact_service.load_artifact(
            app_name=adk_app_name_for_agent_id("research"),
            user_id="owner",
            session_id="owned",
            filename=key,
            version=0,
        )
        assert [version.version for version in versions] == [0]
        assert versions[0].custom_metadata == {"source": "agent_output"}
        assert copied is not None
        assert copied.inline_data.data == b"report"  # type: ignore[union-attr]

        rerun = await supervisor.migrate_legacy_sessions(session_metadata=metadata)
        assert rerun.migrated == 0
        assert rerun.removed_placeholders == 0
        assert rerun.failed == 0

    asyncio.run(scenario())


def test_migrate_legacy_session_preserves_source_when_owner_is_missing(tmp_path) -> None:
    session_service = InMemorySessionService()
    assembler = SimpleNamespace(
        services=SimpleNamespace(session_service=session_service, artifact_service=None)
    )

    class _Repository:
        @staticmethod
        def list_agent_ids() -> tuple[str, ...]:
            return ("main",)

    supervisor = NodeRuntimeSupervisor(
        config_service=SimpleNamespace(repository=_Repository()),
        assembler=assembler,
    )
    metadata = SessionMetadataStore(tmp_path / "session_metadata.db")

    async def scenario() -> None:
        source = await session_service.create_session(
            app_name=LEGACY_ADK_APP_NAME,
            user_id="owner",
            session_id="missing-owner",
        )
        await session_service.append_event(
            source,
            Event(
                id="keep-event",
                author="user",
                content=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Do not lose this")],
                ),
            ),
        )
        metadata.update(
            session_id="missing-owner",
            agent_id="removed-agent",
            principal_id="owner",
        )

        report = await supervisor.migrate_legacy_sessions(session_metadata=metadata)

        assert report.skipped == 1
        assert report.migrated == 0
        retained = await session_service.get_session(
            app_name=LEGACY_ADK_APP_NAME,
            user_id="owner",
            session_id="missing-owner",
        )
        assert retained is not None
        assert [event.id for event in retained.events] == ["keep-event"]

    asyncio.run(scenario())
