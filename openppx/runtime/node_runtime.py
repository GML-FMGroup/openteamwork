"""Node-owned immutable runtime cache, Session creation, and Run control."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from openppx.config import ConfigService

from .assembly import AssembledRuntime, RuntimeAssembler
from .message_time import inject_request_time
from .session_history import project_visible_history
from .session_rewind import RewindTarget, resolve_rewind_target


RunState = Literal["running", "cancelling", "completed", "failed", "cancelled"]
TerminalRunState = Literal["completed", "failed", "cancelled"]


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

    def register_run(
        self,
        *,
        run_id: str,
        agent_id: str,
        session_id: str,
        snapshot_revision: str,
        cancel: Callable[[], None],
    ) -> ManagedRunSnapshot:
        """Register a newly active Run and its cooperative cancel boundary."""
        self._ensure_running()
        snapshot = ManagedRunSnapshot(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            snapshot_revision=snapshot_revision,
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
        on_event: Callable[[Any], None] | None = None,
        on_text_update: Callable[[str, str], None] | None = None,
        on_complete: Callable[[str], None] | None = None,
        on_error: Callable[[BaseException], None] | None = None,
        on_cancelled: Callable[[], None] | None = None,
    ) -> ManagedRunSnapshot:
        """Start one snapshot-pinned Run on a Node-owned background thread."""
        runtime = self.runtime_for(agent_id)
        control = _RunTaskControl()
        snapshot = self.register_run(
            run_id=run_id,
            agent_id=agent_id,
            session_id=session_id,
            snapshot_revision=runtime.metadata.snapshot_revision,
            cancel=control.cancel,
        )

        def _worker() -> None:
            async def _execute() -> str:
                loop = asyncio.get_running_loop()
                task = asyncio.current_task()
                if task is None:  # pragma: no cover - asyncio always supplies it
                    raise RuntimeError("Run task is unavailable.")
                control.attach(loop, task)
                final_text = await runtime.run_text(
                    inject_request_time(text, received_at=datetime.now().astimezone()),
                    user_id=user_id,
                    session_id=session_id,
                    on_event=on_event,
                    on_text_update=on_text_update,
                )
                if not final_text.strip():
                    raise RuntimeError("Run finished without returning a final reply.")
                return final_text

            try:
                final_text = asyncio.run(_execute())
            except asyncio.CancelledError:
                self.complete_run(run_id, state="cancelled")
                if on_cancelled is not None:
                    on_cancelled()
            except BaseException as exc:  # pragma: no cover - verified through callback tests
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
    "ManagedRunSnapshot",
    "NodeRuntimeSupervisor",
    "RunNotActiveError",
    "RunNotFoundError",
    "RuntimeSupervisorError",
    "RuntimeSupervisorStoppedError",
]
