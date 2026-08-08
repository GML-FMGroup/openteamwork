"""Node-owned immutable runtime cache, Session creation, and Run control."""

from __future__ import annotations

import asyncio
import inspect
import logging
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from google.genai import types
from google.adk.agents.invocation_context import LlmCallsLimitExceededError

from openppx.config import ConfigService

from .adk_identity import LEGACY_ADK_APP_NAME, adk_app_name_for_agent_id
from .assembly import AssembledRuntime, RuntimeAssembler
from .goal_supervisor import GoalSliceObserver
from .message_time import inject_request_time
from .run_config import build_run_config
from .session_history import project_visible_history
from .session_metadata_store import SessionMetadataStore
from .session_rewind import RewindTarget, resolve_rewind_target


RunState = Literal["running", "cancelling", "completed", "failed", "cancelled"]
TerminalRunState = Literal["completed", "failed", "cancelled"]
LOGGER = logging.getLogger(__name__)


def _bounded_policy_int(
    policy: dict[str, Any],
    camel_name: str,
    snake_name: str,
    *,
    default: int,
    minimum: int,
    maximum: int,
) -> int:
    """Read and clamp one integer Goal execution policy."""
    raw = policy.get(camel_name, policy.get(snake_name, default))
    try:
        value = int(raw)
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


class RuntimeSupervisorError(RuntimeError):
    """Base error for stable Runtime Supervisor failures."""


class RuntimeSupervisorStoppedError(RuntimeSupervisorError):
    """Raised when a stopped supervisor receives new work."""


class RunNotFoundError(RuntimeSupervisorError):
    """Raised when a Run identity is unknown to this Node."""


class RunNotActiveError(RuntimeSupervisorError):
    """Raised when a terminal Run receives an active-run operation."""


@dataclass(frozen=True, slots=True)
class ManagedRunSnapshot:
    """Non-sensitive state for one Node-owned Run."""

    run_id: str
    agent_id: str
    session_id: str
    snapshot_revision: str
    started_at: str
    state: RunState
    model_profile_id: str | None = None
    model_profile_revision: str | None = None
    provider: str | None = None
    model: str | None = None


@dataclass(frozen=True, slots=True)
class LegacySessionMigrationReport:
    """Outcome of one idempotent legacy ADK Session namespace migration."""

    migrated: int = 0
    removed_placeholders: int = 0
    skipped: int = 0
    failed: int = 0


@dataclass(slots=True)
class _ManagedRun:
    snapshot: ManagedRunSnapshot
    cancel: Callable[[], None]


class _RunTaskControl:
    """Thread-safe cooperative cancellation bridge for one async Run task."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[str] | None = None
        self._cancel_requested = False

    def attach(self, loop: asyncio.AbstractEventLoop, task: asyncio.Task[str]) -> None:
        """Attach the task and honor cancellation requested before startup."""
        with self._lock:
            self._loop = loop
            self._task = task
            cancel_requested = self._cancel_requested
        if cancel_requested:
            task.cancel()

    def cancel(self) -> None:
        """Request task cancellation from any caller thread."""
        with self._lock:
            self._cancel_requested = True
            loop = self._loop
            task = self._task
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)


class NodeRuntimeSupervisor:
    """Own runtime instances and active Run cancellation for one Node."""

    def __init__(self, *, config_service: ConfigService, assembler: RuntimeAssembler) -> None:
        self.config_service = config_service
        self.assembler = assembler
        self._runtimes: dict[tuple[str, str, str], AssembledRuntime] = {}
        self._runs: dict[str, _ManagedRun] = {}
        self._lock = threading.RLock()
        self._stopped = False

    def runtime_for(
        self,
        agent_id: str,
        *,
        role: str | None = None,
        run_override: str | None = None,
    ) -> AssembledRuntime:
        """Return a runtime pinned to the current effective Config snapshot."""
        self._ensure_running()
        snapshot = self.config_service.snapshot(
            agent_id,
            role=role,
            run_override=run_override,
        )
        extension_snapshot = self.assembler.extension_snapshot_for_agent(agent_id)
        key = (agent_id, snapshot.revision, extension_snapshot.revision)
        with self._lock:
            current = self._runtimes.get(key)
            if current is not None:
                return current
        assembled = self.assembler.assemble(snapshot, extension_snapshot=extension_snapshot)
        with self._lock:
            retained = self._runtimes.setdefault(key, assembled)
        if retained is not assembled:
            _run_sync(assembled.close())
        return retained

    async def create_session(
        self,
        agent_id: str,
        *,
        user_id: str,
        session_id: str | None = None,
    ) -> object:
        """Create one ADK Session through the snapshot-native runtime."""
        runtime = self.runtime_for(agent_id)
        return await runtime.session_service.create_session(
            app_name=runtime.agent.name,
            user_id=user_id,
            session_id=session_id,
        )

    def create_session_sync(
        self,
        agent_id: str,
        *,
        user_id: str,
        session_id: str | None = None,
    ) -> object:
        """Create a Session from synchronous CLI/HTTP Action execution."""
        return _run_sync(
            self.create_session(agent_id, user_id=user_id, session_id=session_id)
        )

    async def list_sessions(self, agent_id: str, *, user_id: str) -> list[object]:
        """List principal-scoped Sessions from the shared Node session service."""
        runtime = self.runtime_for(agent_id)
        response = await runtime.session_service.list_sessions(
            app_name=runtime.agent.name,
            user_id=user_id,
        )
        return list(response.sessions)

    def list_sessions_sync(self, agent_id: str, *, user_id: str) -> list[object]:
        """List Sessions from synchronous HTTP and CLI boundaries."""
        return _run_sync(self.list_sessions(agent_id, user_id=user_id))  # type: ignore[return-value]

    async def get_session(self, agent_id: str, *, user_id: str, session_id: str) -> object | None:
        """Read one principal-scoped Session from the shared Node service."""
        runtime = self.runtime_for(agent_id)
        return await runtime.session_service.get_session(
            app_name=runtime.agent.name,
            user_id=user_id,
            session_id=session_id,
        )

    def get_session_sync(self, agent_id: str, *, user_id: str, session_id: str) -> object | None:
        """Read one Session from synchronous HTTP and CLI boundaries."""
        return _run_sync(
            self.get_session(agent_id, user_id=user_id, session_id=session_id)
        )

    async def delete_session(self, agent_id: str, *, user_id: str, session_id: str) -> None:
        """Delete one principal-scoped ADK Session and its scoped Artifacts."""
        runtime = self.runtime_for(agent_id)
        current = await runtime.session_service.get_session(
            app_name=runtime.agent.name,
            user_id=user_id,
            session_id=session_id,
        )
        if current is None:
            raise RuntimeSupervisorError(f"Session '{session_id}' was not found.")
        await runtime.session_service.delete_session(
            app_name=runtime.agent.name,
            user_id=user_id,
            session_id=session_id,
        )
        try:
            await self._delete_session_artifacts(runtime, user_id=user_id, session_id=session_id)
        except Exception as exc:
            raise RuntimeSupervisorError(
                "The Session was deleted, but its Artifacts could not be fully removed."
            ) from exc

    def delete_session_sync(self, agent_id: str, *, user_id: str, session_id: str) -> None:
        """Delete one Session from synchronous Action boundaries."""
        _run_sync(self.delete_session(agent_id, user_id=user_id, session_id=session_id))

    async def fork_session(self, agent_id: str, *, user_id: str, session_id: str) -> object:
        """Create a new ADK Session by replaying the source Session's durable events."""
        runtime = self.runtime_for(agent_id)
        source = await runtime.session_service.get_session(
            app_name=runtime.agent.name,
            user_id=user_id,
            session_id=session_id,
        )
        if source is None:
            raise RuntimeSupervisorError(f"Session '{session_id}' was not found.")
        forked = await runtime.session_service.create_session(
            app_name=runtime.agent.name,
            user_id=user_id,
        )
        for event in source.events:
            if getattr(event, "partial", False):
                continue
            cloned = event.model_copy(
                deep=True,
                update={"id": uuid.uuid4().hex, "timestamp": time.time()},
            )
            await runtime.session_service.append_event(forked, cloned)
        try:
            await self._copy_session_artifacts(
                runtime,
                user_id=user_id,
                source_session_id=session_id,
                target_session_id=str(forked.id),
            )
        except Exception as exc:
            await self._delete_session_artifacts(
                runtime,
                user_id=user_id,
                session_id=str(forked.id),
            )
            await runtime.session_service.delete_session(
                app_name=runtime.agent.name,
                user_id=user_id,
                session_id=str(forked.id),
            )
            raise RuntimeSupervisorError("The Session could not be forked with its Artifacts.") from exc
        refreshed = await runtime.session_service.get_session(
            app_name=runtime.agent.name,
            user_id=user_id,
            session_id=str(forked.id),
        )
        return refreshed or forked

    @staticmethod
    async def _delete_session_artifacts(runtime: AssembledRuntime, *, user_id: str, session_id: str) -> None:
        """Remove every Artifact in one Session scope without touching user-scoped files."""
        service = runtime.artifact_service
        if service is None:
            return
        keys = await service.list_artifact_keys(
            app_name=runtime.agent.name,
            user_id=user_id,
            session_id=session_id,
        )
        for key in keys:
            await service.delete_artifact(
                app_name=runtime.agent.name,
                user_id=user_id,
                session_id=session_id,
                filename=key,
            )

    @staticmethod
    async def _copy_session_artifacts(
        runtime: AssembledRuntime,
        *,
        user_id: str,
        source_session_id: str,
        target_session_id: str,
    ) -> None:
        """Copy all Artifact versions so forked history keeps stable references."""
        service = runtime.artifact_service
        if service is None:
            return
        keys = await service.list_artifact_keys(
            app_name=runtime.agent.name,
            user_id=user_id,
            session_id=source_session_id,
        )
        for key in keys:
            versions = await service.list_artifact_versions(
                app_name=runtime.agent.name,
                user_id=user_id,
                session_id=source_session_id,
                filename=key,
            )
            for version in versions:
                artifact = await service.load_artifact(
                    app_name=runtime.agent.name,
                    user_id=user_id,
                    session_id=source_session_id,
                    filename=key,
                    version=int(version.version),
                )
                if artifact is None:
                    raise RuntimeSupervisorError("A referenced Artifact version is unavailable.")
                await service.save_artifact(
                    app_name=runtime.agent.name,
                    user_id=user_id,
                    session_id=target_session_id,
                    filename=key,
                    artifact=artifact,
                    custom_metadata=dict(version.custom_metadata or {}),
                )

    def fork_session_sync(self, agent_id: str, *, user_id: str, session_id: str) -> object:
        """Fork one Session from synchronous Action boundaries."""
        return _run_sync(self.fork_session(agent_id, user_id=user_id, session_id=session_id))

    async def migrate_legacy_sessions(
        self,
        *,
        session_metadata: SessionMetadataStore,
    ) -> LegacySessionMigrationReport:
        """Move Sessions from the historical shared ADK namespace to Agent ownership.

        The migration is deliberately idempotent. Source data is deleted only
        after its events, artifacts, and ownership metadata are durable in the
        target namespace. A partially copied target is safe to resume on the
        next Node start.
        """
        service = self.assembler.services.session_service
        artifact_service = self.assembler.services.artifact_service
        known_agent_ids = set(self.config_service.repository.list_agent_ids())
        fallback_agent_id = "main" if "main" in known_agent_ids else None
        response = await service.list_sessions(
            app_name=LEGACY_ADK_APP_NAME,
            user_id=None,
        )
        migrated = 0
        removed_placeholders = 0
        skipped = 0
        failed = 0
        for summary in response.sessions:
            session_id = str(summary.id)
            user_id = str(summary.user_id)
            try:
                source = await service.get_session(
                    app_name=LEGACY_ADK_APP_NAME,
                    user_id=user_id,
                    session_id=session_id,
                )
                if source is None:
                    raise RuntimeSupervisorError(
                        f"Legacy Session '{session_id}' is unavailable after discovery."
                    )
                metadata = session_metadata.get(session_id)
                artifact_keys = await self._artifact_keys_for_scope(
                    artifact_service,
                    app_name=LEGACY_ADK_APP_NAME,
                    user_id=user_id,
                    session_id=session_id,
                )
                durable_events = [
                    event
                    for event in source.events
                    if not bool(getattr(event, "partial", False))
                ]
                has_content = bool(durable_events or artifact_keys)
                if metadata is None and not has_content:
                    await service.delete_session(
                        app_name=LEGACY_ADK_APP_NAME,
                        user_id=user_id,
                        session_id=session_id,
                    )
                    session_metadata.delete(session_id)
                    removed_placeholders += 1
                    continue

                if metadata is not None:
                    target_agent_id = metadata.agent_id
                    if target_agent_id not in known_agent_ids:
                        LOGGER.warning(
                            "Keeping legacy Session %s because its owning Agent %s is unavailable.",
                            session_id,
                            target_agent_id,
                        )
                        skipped += 1
                        continue
                else:
                    target_agent_id = fallback_agent_id
                    if target_agent_id is None:
                        LOGGER.warning(
                            "Keeping unmapped legacy Session %s because Agent 'main' is unavailable.",
                            session_id,
                        )
                        skipped += 1
                        continue

                target_app_name = adk_app_name_for_agent_id(target_agent_id)
                target = await service.get_session(
                    app_name=target_app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
                if target is None:
                    target = await service.create_session(
                        app_name=target_app_name,
                        user_id=user_id,
                        state=dict(getattr(source, "state", {}) or {}),
                        session_id=session_id,
                    )

                target_event_ids = {
                    str(getattr(event, "id", ""))
                    for event in target.events
                    if getattr(event, "id", None)
                }
                for event in durable_events:
                    event_id = str(getattr(event, "id", ""))
                    if not event_id:
                        raise RuntimeSupervisorError(
                            f"Legacy Session '{session_id}' contains an event without an identity."
                        )
                    if event_id and event_id in target_event_ids:
                        continue
                    cloned = event.model_copy(deep=True)
                    await service.append_event(target, cloned)
                    target_event_ids.add(event_id)

                verified_target = await service.get_session(
                    app_name=target_app_name,
                    user_id=user_id,
                    session_id=session_id,
                )
                if verified_target is None:
                    raise RuntimeSupervisorError(
                        f"Legacy Session '{session_id}' target is unavailable after event copy."
                    )
                source_event_ids = {
                    str(getattr(event, "id", "")) for event in durable_events
                }
                verified_event_ids = {
                    str(getattr(event, "id", ""))
                    for event in verified_target.events
                    if getattr(event, "id", None)
                }
                if not source_event_ids.issubset(verified_event_ids):
                    raise RuntimeSupervisorError(
                        f"Legacy Session '{session_id}' events could not be verified in its target namespace."
                    )

                await self._copy_artifacts_between_apps(
                    artifact_service,
                    source_app_name=LEGACY_ADK_APP_NAME,
                    target_app_name=target_app_name,
                    user_id=user_id,
                    session_id=session_id,
                    artifact_keys=artifact_keys,
                )
                session_metadata.update(
                    session_id=session_id,
                    agent_id=target_agent_id,
                    principal_id=user_id,
                )
                await self._delete_artifacts_for_scope(
                    artifact_service,
                    app_name=LEGACY_ADK_APP_NAME,
                    user_id=user_id,
                    session_id=session_id,
                    artifact_keys=artifact_keys,
                )
                await service.delete_session(
                    app_name=LEGACY_ADK_APP_NAME,
                    user_id=user_id,
                    session_id=session_id,
                )
                migrated += 1
            except Exception:
                failed += 1
                LOGGER.exception("Legacy Session migration failed for %s; source retained.", session_id)
        return LegacySessionMigrationReport(
            migrated=migrated,
            removed_placeholders=removed_placeholders,
            skipped=skipped,
            failed=failed,
        )

    def migrate_legacy_sessions_sync(
        self,
        *,
        session_metadata: SessionMetadataStore,
    ) -> LegacySessionMigrationReport:
        """Run legacy Session migration at the synchronous Node boundary."""
        return _run_sync(
            self.migrate_legacy_sessions(session_metadata=session_metadata)
        )  # type: ignore[return-value]

    @staticmethod
    async def _artifact_keys_for_scope(
        service: Any | None,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
    ) -> list[str]:
        """List artifact keys through ADK's public ArtifactService API."""
        if service is None:
            return []
        return list(
            await service.list_artifact_keys(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
            )
        )

    @staticmethod
    async def _copy_artifacts_between_apps(
        service: Any | None,
        *,
        source_app_name: str,
        target_app_name: str,
        user_id: str,
        session_id: str,
        artifact_keys: list[str],
    ) -> None:
        """Idempotently copy Artifact versions between ADK app namespaces."""
        if service is None:
            return
        for key in artifact_keys:
            source_versions = await service.list_artifact_versions(
                app_name=source_app_name,
                user_id=user_id,
                session_id=session_id,
                filename=key,
            )
            target_versions = await service.list_artifact_versions(
                app_name=target_app_name,
                user_id=user_id,
                session_id=session_id,
                filename=key,
            )
            existing_versions = {int(version.version) for version in target_versions}
            for version in source_versions:
                version_number = int(version.version)
                if version_number in existing_versions:
                    continue
                artifact = await service.load_artifact(
                    app_name=source_app_name,
                    user_id=user_id,
                    session_id=session_id,
                    filename=key,
                    version=version_number,
                )
                if artifact is None:
                    raise RuntimeSupervisorError(
                        f"Legacy Artifact '{key}' version {version_number} is unavailable."
                    )
                saved_version = await service.save_artifact(
                    app_name=target_app_name,
                    user_id=user_id,
                    session_id=session_id,
                    filename=key,
                    artifact=artifact,
                    custom_metadata=dict(version.custom_metadata or {}),
                )
                existing_versions.add(int(saved_version))
            verified_target_versions = await service.list_artifact_versions(
                app_name=target_app_name,
                user_id=user_id,
                session_id=session_id,
                filename=key,
            )
            source_by_version = {
                int(version.version): version for version in source_versions
            }
            target_by_version = {
                int(version.version): version for version in verified_target_versions
            }
            if set(source_by_version) != set(target_by_version):
                raise RuntimeSupervisorError(
                    f"Legacy Artifact '{key}' could not be verified in its target namespace."
                )
            for version_number, source_version in source_by_version.items():
                source_artifact = await service.load_artifact(
                    app_name=source_app_name,
                    user_id=user_id,
                    session_id=session_id,
                    filename=key,
                    version=version_number,
                )
                target_artifact = await service.load_artifact(
                    app_name=target_app_name,
                    user_id=user_id,
                    session_id=session_id,
                    filename=key,
                    version=version_number,
                )
                if source_artifact is None or target_artifact is None:
                    raise RuntimeSupervisorError(
                        f"Legacy Artifact '{key}' version {version_number} is unavailable."
                    )
                if source_artifact.model_dump(mode="json") != target_artifact.model_dump(
                    mode="json"
                ):
                    raise RuntimeSupervisorError(
                        f"Legacy Artifact '{key}' version {version_number} content differs after copy."
                    )
                target_version = target_by_version[version_number]
                if dict(source_version.custom_metadata or {}) != dict(
                    target_version.custom_metadata or {}
                ):
                    raise RuntimeSupervisorError(
                        f"Legacy Artifact '{key}' version {version_number} metadata differs after copy."
                    )

    @staticmethod
    async def _delete_artifacts_for_scope(
        service: Any | None,
        *,
        app_name: str,
        user_id: str,
        session_id: str,
        artifact_keys: list[str],
    ) -> None:
        """Delete a known Artifact set through ADK's public API."""
        if service is None:
            return
        for key in artifact_keys:
            await service.delete_artifact(
                app_name=app_name,
                user_id=user_id,
                session_id=session_id,
                filename=key,
            )

    async def session_history(
        self,
        agent_id: str,
        *,
        user_id: str,
        session_id: str,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Read recent ADK-visible text history for one principal-scoped Session."""
        session = await self.get_session(agent_id, user_id=user_id, session_id=session_id)
        if session is None:
            raise RuntimeSupervisorError(f"Session '{session_id}' was not found.")
        return project_visible_history(session, limit=limit)

    def session_history_sync(
        self,
        agent_id: str,
        *,
        user_id: str,
        session_id: str,
        limit: int = 20,
    ) -> list[dict[str, object]]:
        """Read visible Session history from synchronous Action boundaries."""
        return _run_sync(
            self.session_history(
                agent_id,
                user_id=user_id,
                session_id=session_id,
                limit=limit,
            )
        )  # type: ignore[return-value]

    async def rewind_session(
        self,
        agent_id: str,
        *,
        user_id: str,
        session_id: str,
        before_invocation_id: str | None = None,
    ) -> RewindTarget:
        """Append an ADK-native rewind marker before the resolved invocation."""
        runtime = self.runtime_for(agent_id)
        target = await resolve_rewind_target(
            runtime.session_service,
            app_name=runtime.agent.name,
            user_id=user_id,
            session_id=session_id,
            before_invocation_id=before_invocation_id,
        )
        await runtime.runner.rewind_async(
            user_id=user_id,
            session_id=session_id,
            rewind_before_invocation_id=target.invocation_id,
        )
        return target

    def rewind_session_sync(
        self,
        agent_id: str,
        *,
        user_id: str,
        session_id: str,
        before_invocation_id: str | None = None,
    ) -> RewindTarget:
        """Rewind one Session from synchronous Action boundaries."""
        return _run_sync(
            self.rewind_session(
                agent_id,
                user_id=user_id,
                session_id=session_id,
                before_invocation_id=before_invocation_id,
            )
        )  # type: ignore[return-value]

    async def hello(
        self,
        agent_id: str,
        text: str,
        *,
        user_id: str,
        session_id: str,
    ) -> str:
        """Run a real ADK turn using the current immutable snapshot."""
        return await self.runtime_for(agent_id).run_text(
            text,
            user_id=user_id,
            session_id=session_id,
        )

    def hello_sync(
        self,
        agent_id: str,
        text: str,
        *,
        user_id: str,
        session_id: str,
    ) -> str:
        """Complete one model turn from synchronous Setup/CLI Action boundaries."""
        return _run_sync(
            self.hello(
                agent_id,
                text,
                user_id=user_id,
                session_id=session_id,
            )
        )  # type: ignore[return-value]

    def register_run(
        self,
        *,
        run_id: str,
        agent_id: str,
        session_id: str,
        snapshot_revision: str,
        model_profile_id: str | None = None,
        model_profile_revision: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        cancel: Callable[[], None],
    ) -> ManagedRunSnapshot:
        """Register a newly active Run and its cooperative cancel boundary."""
        self._ensure_running()
        snapshot = ManagedRunSnapshot(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            snapshot_revision=snapshot_revision,
            model_profile_id=model_profile_id,
            model_profile_revision=model_profile_revision,
            provider=provider,
            model=model,
            started_at=datetime.now(timezone.utc).isoformat(),
            state="running",
        )
        with self._lock:
            if run_id in self._runs:
                raise ValueError(f"Run '{run_id}' is already registered.")
            self._runs[run_id] = _ManagedRun(snapshot=snapshot, cancel=cancel)
        return snapshot

    def start_run(
        self,
        *,
        run_id: str,
        agent_id: str,
        session_id: str,
        user_id: str,
        text: str,
        run_override: str | None = None,
        artifact_parts: tuple[types.Part, ...] = (),
        on_event: Callable[[Any], None] | None = None,
        on_text_update: Callable[[str, str], None] | None = None,
        on_complete: Callable[[str], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
    ) -> ManagedRunSnapshot:
        """Start one snapshot-pinned Run on a Node-owned background thread."""
        runtime = self.runtime_for(agent_id, run_override=run_override)
        control = _RunTaskControl()
        snapshot = self.register_run(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            snapshot_revision=runtime.metadata.snapshot_revision,
            model_profile_id=runtime.metadata.model_profile_id,
            model_profile_revision=runtime.metadata.model_profile_revision,
            provider=runtime.metadata.provider,
            model=runtime.metadata.model,
            cancel=control.cancel,
        )

        def _worker() -> None:
            goal_store = self.assembler.services.goal_store
            observer = GoalSliceObserver()
            tracks_goal = False
            last_invocation_id = ""

            def _record_terminal_goal_fact(status: TerminalRunState) -> None:
                """Persist the terminal Run fact before presentation callbacks fire."""
                if not tracks_goal:
                    return
                goal_store.record_run_fact(
                    session_id=session_id,
                    run_id=run_id,
                    status=status,
                    correlation_id=run_id,
                    invocation_id=last_invocation_id,
                )

            async def _execute() -> str:
                nonlocal last_invocation_id, tracks_goal
                loop = asyncio.get_running_loop()
                task = asyncio.current_task()
                if task is None:  # pragma: no cover - asyncio always supplies it
                    raise RuntimeError("Run task is unavailable.")
                control.attach(loop, task)
                request = types.UserContent(parts=[
                    types.Part.from_text(
                        text=inject_request_time(text, received_at=datetime.now().astimezone())
                    ),
                    *artifact_parts,
                ])
                goal = goal_store.current_goal(session_id)
                tracks_goal = goal is not None
                if goal is not None:
                    goal_store.record_run_fact(
                        session_id=session_id,
                        run_id=run_id,
                        status="running",
                        correlation_id=run_id,
                        snapshot={
                            "snapshotRevision": snapshot.snapshot_revision,
                            "modelProfileId": snapshot.model_profile_id,
                            "modelProfileRevision": snapshot.model_profile_revision,
                            "provider": snapshot.provider,
                            "model": snapshot.model,
                        },
                    )
                budget = goal.budget_policy if goal is not None else {}
                calls_per_invocation = _bounded_policy_int(
                    budget,
                    "maxLlmCallsPerInvocation",
                    "max_llm_calls_per_invocation",
                    default=64,
                    minimum=1,
                    maximum=500,
                )
                max_continuations = _bounded_policy_int(
                    budget,
                    "maxContinuations",
                    "max_continuations",
                    default=8,
                    minimum=0,
                    maximum=64,
                )
                auto_continue = bool(
                    budget.get("autoContinue", budget.get("auto_continue", True))
                )
                max_no_progress = _bounded_policy_int(
                    budget,
                    "maxNoProgressContinuations",
                    "max_no_progress_continuations",
                    default=3,
                    minimum=1,
                    maximum=20,
                )
                max_repeated_actions = _bounded_policy_int(
                    budget,
                    "maxRepeatedActionContinuations",
                    "max_repeated_action_continuations",
                    default=2,
                    minimum=1,
                    maximum=20,
                )
                run_metadata = {"clientRunId": run_id}
                if goal is not None:
                    run_metadata["goalId"] = goal.goal_id
                run_config = build_run_config(
                    profile="full",
                    max_llm_calls=calls_per_invocation if goal is not None else None,
                    custom_metadata=run_metadata,
                )
                continuation_index = 0
                final_text = ""

                async def _observe_event(event: Any) -> None:
                    """Observe native ADK events and forward them to the caller."""
                    observer.observe(event)
                    if on_event is None:
                        return
                    forwarded = on_event(event)
                    if inspect.isawaitable(forwarded):
                        await forwarded

                def _record_slice_progress(before_signature: str) -> object | None:
                    """Persist one bounded slice and return the current Goal."""
                    nonlocal last_invocation_id
                    observation = observer.snapshot()
                    if observation.invocation_id:
                        last_invocation_id = observation.invocation_id
                    if goal is None:
                        return None
                    return goal_store.record_progress_observation(
                        session_id=session_id,
                        run_id=run_id,
                        continuation_index=continuation_index,
                        before_signature=before_signature,
                        action_fingerprint=observation.action_fingerprint,
                        action_names=observation.action_names,
                        max_no_progress=max_no_progress,
                        max_repeated_actions=max_repeated_actions,
                        correlation_id=run_id,
                    )

                def _controlled_goal_halt_reply() -> str:
                    """Return a stable reply when the durable Goal intentionally stops."""
                    if goal is None:
                        return ""
                    halted = goal_store.current_goal(session_id)
                    if halted is None or halted.status not in {"blocked", "paused", "waiting"}:
                        return ""
                    flow = goal_store.flow_for_goal(halted.goal_id)
                    reason = (
                        str((flow.wait_reason if flow is not None else {}).get("message") or "")
                        .strip()
                    )
                    return f"Goal paused: {reason}" if reason else "Goal paused."

                while True:
                    observer.reset()
                    current_goal = goal_store.current_goal(session_id) if goal is not None else None
                    before_signature = (
                        goal_store.progress_signature(current_goal.goal_id)
                        if current_goal is not None and current_goal.status == "active"
                        else ""
                    )
                    try:
                        if continuation_index == 0:
                            final_text = await runtime.run_message(
                                request,
                                user_id=user_id,
                                session_id=session_id,
                                on_event=_observe_event,
                                on_text_update=on_text_update,
                                run_config=run_config,
                            )
                        else:
                            final_text = await runtime.continue_message(
                                user_id=user_id,
                                session_id=session_id,
                                on_event=_observe_event,
                                on_text_update=on_text_update,
                                run_config=run_config,
                            )
                        observed_goal = _record_slice_progress(before_signature)
                        if goal is None:
                            break
                        if getattr(observed_goal, "status", "") == "blocked":
                            break
                        if not auto_continue:
                            goal_store.wait_current_goal(
                                session_id,
                                reason="Automatic Goal continuation is disabled by its budget policy.",
                                correlation_id=run_id,
                            )
                            break
                        refreshed = goal_store.current_goal(session_id)
                        flow = (
                            goal_store.flow_for_goal(refreshed.goal_id)
                            if refreshed is not None
                            else None
                        )
                        completion_pending = bool(
                            (flow.recovery_state if flow is not None else {}).get("pendingCompletion")
                        )
                        if refreshed is None or refreshed.status != "active" or completion_pending:
                            break
                        if continuation_index >= max_continuations:
                            goal_store.record_continuation_fact(
                                session_id=session_id,
                                run_id=run_id,
                                continuation_index=continuation_index,
                                max_continuations=max_continuations,
                                max_llm_calls_per_invocation=calls_per_invocation,
                                exhausted=True,
                            )
                            goal_store.wait_current_goal(
                                session_id,
                                reason="The Goal continuation budget was exhausted. Resume it to start another bounded Run.",
                                correlation_id=run_id,
                            )
                            break
                        continuation_index += 1
                        goal_store.record_continuation_fact(
                            session_id=session_id,
                            run_id=run_id,
                            continuation_index=continuation_index,
                            max_continuations=max_continuations,
                            max_llm_calls_per_invocation=calls_per_invocation,
                        )
                    except LlmCallsLimitExceededError:
                        observed_goal = _record_slice_progress(before_signature)
                        if getattr(observed_goal, "status", "") == "blocked":
                            break
                        if goal is None or continuation_index >= max_continuations:
                            if goal is not None:
                                goal_store.record_continuation_fact(
                                    session_id=session_id,
                                    run_id=run_id,
                                    continuation_index=continuation_index,
                                    max_continuations=max_continuations,
                                    max_llm_calls_per_invocation=calls_per_invocation,
                                    exhausted=True,
                                )
                                if final_text.strip():
                                    goal_store.wait_current_goal(
                                        session_id,
                                        reason="The Goal LLM-call budget was exhausted. Resume it to continue.",
                                        correlation_id=run_id,
                                    )
                                    break
                                goal_store.block_current_goal(
                                    session_id,
                                    reason="The Goal exhausted its LLM-call budget before producing a result.",
                                    correlation_id=run_id,
                                )
                                break
                            raise
                        continuation_index += 1
                        goal_store.record_continuation_fact(
                            session_id=session_id,
                            run_id=run_id,
                            continuation_index=continuation_index,
                            max_continuations=max_continuations,
                            max_llm_calls_per_invocation=calls_per_invocation,
                        )
                if not final_text.strip():
                    final_text = _controlled_goal_halt_reply()
                if not final_text.strip():
                    raise RuntimeError("Run finished without returning a final reply.")
                _record_terminal_goal_fact("completed")
                return final_text

            try:
                final_text = asyncio.run(_execute())
            except asyncio.CancelledError:
                _record_terminal_goal_fact("cancelled")
                self.complete_run(run_id, state="cancelled")
                if on_cancelled is not None:
                    on_cancelled()
            except BaseException as exc:  # pragma: no cover - verified through callback tests
                _record_terminal_goal_fact("failed")
                self.complete_run(run_id, state="failed")
                if on_error is not None:
                    on_error(exc)
            else:
                self.complete_run(run_id, state="completed")
                if on_complete is not None:
                    on_complete(final_text)

        threading.Thread(
            target=_worker,
            name=f"openppx-run-{run_id}",
            daemon=True,
        ).start()
        return snapshot
    def stop_run(self, run_id: str) -> ManagedRunSnapshot:
        """Request cooperative cancellation of one active Run exactly once."""
        with self._lock:
            managed = self._runs.get(run_id)
            if managed is None:
                raise RunNotFoundError(f"Run '{run_id}' was not found.")
            if managed.snapshot.state != "running":
                raise RunNotActiveError(f"Run '{run_id}' is not active.")
            updated = _replace_run_state(managed.snapshot, "cancelling")
            managed.snapshot = updated
            cancel = managed.cancel
        cancel()
        return updated

    def complete_run(self, run_id: str, *, state: TerminalRunState) -> ManagedRunSnapshot:
        """Record one terminal Run state while retaining queryable provenance."""
        with self._lock:
            managed = self._runs.get(run_id)
            if managed is None:
                raise RunNotFoundError(f"Run '{run_id}' was not found.")
            managed.snapshot = _replace_run_state(managed.snapshot, state)
            return managed.snapshot

    def run_status(self, run_id: str) -> ManagedRunSnapshot:
        """Return the current state of one registered Run."""
        with self._lock:
            managed = self._runs.get(run_id)
            if managed is None:
                raise RunNotFoundError(f"Run '{run_id}' was not found.")
            return managed.snapshot

    def inspect(
        self,
        *,
        agent_id: str | None = None,
        session_id: str | None = None,
        run_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, object]:
        """Return a bounded, secret-free projection of current Runtime facts.

        The projection reads the same immutable Config and Extension snapshots
        used to assemble future ADK Runs.  It deliberately omits workspace
        paths, credentials, message bodies, tool arguments, and model prompts.
        """
        self._ensure_running()
        safe_limit = max(1, min(int(limit or 20), 100))
        with self._lock:
            snapshots = [managed.snapshot for managed in self._runs.values()]
        snapshots.sort(key=lambda item: item.started_at, reverse=True)
        if agent_id is not None:
            snapshots = [item for item in snapshots if item.agent_id == agent_id]
        if session_id is not None:
            snapshots = [item for item in snapshots if item.session_id == session_id]
        if run_id is not None:
            snapshots = [item for item in snapshots if item.run_id == run_id]

        effective: dict[str, object] | None = None
        if agent_id is not None:
            snapshot = self.config_service.snapshot(agent_id)
            extensions = self.assembler.extension_snapshot_for_agent(agent_id)
            effective = {
                "agentId": agent_id,
                "configSnapshotRevision": snapshot.revision,
                "modelProfileId": snapshot.model.profile_id,
                "modelProfileRevision": snapshot.model.revision,
                "provider": snapshot.model.provider,
                "model": snapshot.model.model,
                "workspaceConfigured": bool(snapshot.agent.spec.workspace.strip()),
                "extensionSnapshotRevision": extensions.revision,
                "extensions": {
                    "skills": len(extensions.skills.skills),
                    "mcpServers": len(extensions.mcp.entries),
                    "apps": len(extensions.apps.entries),
                    "plugins": len(extensions.plugins.entries),
                    "pluginHooks": len(extensions.plugins.hooks.entries),
                },
            }

        return {
            "supervisor": self.status(),
            "effectiveRuntime": effective,
            "runs": [
                {
                    "runId": item.run_id,
                    "agentId": item.agent_id,
                    "sessionId": item.session_id,
                    "state": item.state,
                    "startedAt": item.started_at,
                    "configSnapshotRevision": item.snapshot_revision,
                    "modelProfileId": item.model_profile_id,
                    "modelProfileRevision": item.model_profile_revision,
                    "provider": item.provider,
                    "model": item.model,
                }
                for item in snapshots[:safe_limit]
            ],
        }

    def status(self) -> dict[str, object]:
        """Return a redacted Runtime Supervisor status projection."""
        with self._lock:
            active = sum(
                item.snapshot.state in {"running", "cancelling"}
                for item in self._runs.values()
            )
            return {
                "state": "stopped" if self._stopped else "running",
                "runtimeSnapshots": len(self._runtimes),
                "activeRuns": active,
            }

    def close(self) -> None:
        """Idempotently reject work, cancel Runs, and close extension sessions."""
        with self._lock:
            if self._stopped:
                return
            self._stopped = True
            active = [
                managed
                for managed in self._runs.values()
                if managed.snapshot.state == "running"
            ]
            for managed in active:
                managed.snapshot = _replace_run_state(managed.snapshot, "cancelling")
            runtimes = tuple(self._runtimes.values())
            self._runtimes.clear()
        for managed in active:
            managed.cancel()
        for runtime in runtimes:
            _run_sync(runtime.close())

    def _ensure_running(self) -> None:
        with self._lock:
            if self._stopped:
                raise RuntimeSupervisorStoppedError("The Node Runtime Supervisor is stopped.")


def _replace_run_state(snapshot: ManagedRunSnapshot, state: RunState) -> ManagedRunSnapshot:
    return ManagedRunSnapshot(
        run_id=snapshot.run_id,
        agent_id=snapshot.agent_id,
        session_id=snapshot.session_id,
        snapshot_revision=snapshot.snapshot_revision,
        model_profile_id=snapshot.model_profile_id,
        model_profile_revision=snapshot.model_profile_revision,
        provider=snapshot.provider,
        model=snapshot.model,
        started_at=snapshot.started_at,
        state=state,
    )


def _run_sync(awaitable: object) -> object:
    """Run one coroutine at sync boundaries and reject nested event loops."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(awaitable)  # type: ignore[arg-type]
    raise RuntimeError("Synchronous Runtime API cannot run inside an active event loop.")


__all__ = [
    "LegacySessionMigrationReport",
    "ManagedRunSnapshot",
    "NodeRuntimeSupervisor",
    "RunNotActiveError",
    "RunNotFoundError",
    "RuntimeSupervisorError",
    "RuntimeSupervisorStoppedError",
]
