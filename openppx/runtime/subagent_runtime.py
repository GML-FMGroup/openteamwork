"""Node-owned detached Subagent execution built on Google ADK Runner."""

from __future__ import annotations

import asyncio
import threading
from typing import Any, Callable, Literal

from google.adk.events import Event
from google.genai import types

from openppx.config import ConfigService

from .assembly import AssembledRuntime, RuntimeAssembler
from .subagent_contract import SubagentSpawnRequest
from .task_execution import (
    SubagentTaskRunnerAdapter,
    TaskController,
    TaskRunnerRegistry,
)
from .task_store import TASK_ACTIVE_STATUSES, TaskEventStore, TaskRun


TerminalRunState = Literal["completed", "failed", "cancelled"]
_MAX_ACTIVE_SUBAGENTS_PER_PARENT_SESSION = 4
_MAX_SUBAGENT_RESULT_CHARS = 64_000


class _SubagentTaskControl:
    """Bridge synchronous task controls to one async ADK worker task."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._task: asyncio.Task[str] | None = None
        self._cancel_requested = False

    def attach(self, loop: asyncio.AbstractEventLoop, task: asyncio.Task[str]) -> None:
        """Attach the worker and honor cancellation requested before startup."""
        with self._lock:
            self._loop = loop
            self._task = task
            cancel_requested = self._cancel_requested
        if cancel_requested:
            task.cancel()

    def cancel(self) -> None:
        """Request cancellation from any Node thread."""
        with self._lock:
            self._cancel_requested = True
            loop = self._loop
            task = self._task
        if loop is not None and task is not None:
            loop.call_soon_threadsafe(task.cancel)


class SubagentRuntimeManager:
    """Own detached same-Agent workers and their durable TaskRun projection."""

    def __init__(
        self,
        *,
        config_service: ConfigService,
        assembler: RuntimeAssembler,
        register_run: Callable[..., Any],
        complete_run: Callable[..., Any],
        run_status: Callable[[str], Any],
        stop_run: Callable[[str], Any],
    ) -> None:
        self._config_service = config_service
        self._assembler = assembler
        self._register_run = register_run
        self._complete_run = complete_run
        self.task_controller = TaskController(
            task_store=assembler.services.task_store,
            runner_registry=TaskRunnerRegistry(
                prepend_adapters=[
                    SubagentTaskRunnerAdapter(
                        run_status=run_status,
                        stop_run=stop_run,
                    )
                ]
            ),
        )

    def dispatch(self, request: SubagentSpawnRequest) -> TaskRun:
        """Validate a spawn ticket, persist it, and launch its ADK worker."""
        agent_id = str(getattr(request, "agent_id", "") or "").strip()
        if not agent_id:
            raise PermissionError("Subagent request is missing a trusted Agent identity.")
        snapshot = self._config_service.snapshot(agent_id)
        extensions = self._assembler.extension_snapshot_for_agent(
            agent_id,
            workspace_root=snapshot.agent.spec.workspace,
        )
        expected = {
            "snapshot": (str(getattr(request, "snapshot_revision", "") or ""), snapshot.revision),
            "permission": (
                str(getattr(request, "permission_revision", "") or ""),
                snapshot.permissions.revision,
            ),
            "extension": (
                str(getattr(request, "extension_revision", "") or ""),
                extensions.revision,
            ),
        }
        stale = [name for name, (requested, current) in expected.items() if requested != current]
        if stale:
            raise PermissionError(
                "Subagent spawn snapshot is no longer current: " + ", ".join(stale)
            )

        task_store = self._assembler.services.task_store
        existing = task_store.get_task(str(request.task_id))
        if existing is not None:
            if existing.kind != "subagent":
                raise ValueError(f"Task id {request.task_id!r} is already in use.")
            return existing
        active_subagents = [
            task
            for task in task_store.list_tasks(
                session_id=str(request.session_id),
                statuses=tuple(TASK_ACTIVE_STATUSES),
                limit=_MAX_ACTIVE_SUBAGENTS_PER_PARENT_SESSION + 1,
            )
            if task.kind == "subagent"
        ]
        if len(active_subagents) >= _MAX_ACTIVE_SUBAGENTS_PER_PARENT_SESSION:
            raise RuntimeError(
                "The parent Session already has the maximum number of active subagents."
            )

        runtime = self._assembler.assemble_subagent(
            snapshot,
            extension_snapshot=extensions,
        )
        task_id = str(request.task_id)
        run_id = f"{task_id}-run"
        child_session_id = f"{task_id}-session"
        delivery = (
            {"route": str(request.route), "scope_id": str(request.scope_id)}
            if bool(getattr(request, "notify_on_complete", True))
            else {}
        )
        runner_payload = {
            "runner": "subagent",
            "agent_id": agent_id,
            "parent_session_id": str(request.session_id),
            "child_session_id": child_session_id,
            "snapshot_revision": snapshot.revision,
            "permission_revision": snapshot.permissions.revision,
            "extension_revision": extensions.revision,
            "delivery": delivery,
            "result": "",
        }
        task = task_store.create_task(
            kind="subagent",
            status="queued",
            title="Background sub-agent task",
            owner_key=str(request.user_id),
            user_id=str(request.user_id),
            thread_id=str(request.scope_id),
            session_id=str(request.session_id),
            turn_id=str(request.invocation_id),
            invocation_id=str(request.invocation_id),
            function_call_id=str(request.function_call_id),
            tool_call_id=str(request.function_call_id),
            dedupe_key=(
                f"subagent:{agent_id}:{request.session_id}:"
                f"{request.invocation_id}:{request.function_call_id}"
            ),
            external_ref=run_id,
            runner_payload=runner_payload,
            runner_capabilities={
                "interrupt": False,
                "cancel": True,
                "output": True,
                "rejoin": False,
            },
            resume_policy="not_resumable",
            stop_policy="cancel_task",
            cancel_policy="cancel_run",
            progress_summary="Subagent task queued.",
            task_id=task_id,
        )
        event_store = TaskEventStore(db_path=task_store.db_path)
        event_store.append_event(
            task_id,
            "task.queued",
            message="Subagent task queued.",
            payload={
                "runner": "subagent",
                "agent_id": agent_id,
                "permission_revision": snapshot.permissions.revision,
            },
        )
        control = _SubagentTaskControl()
        try:
            self._register_run(
                run_id=run_id,
                agent_id=agent_id,
                session_id=child_session_id,
                snapshot_revision=runtime.metadata.snapshot_revision,
                model_profile_id=runtime.metadata.model_profile_id,
                model_profile_revision=runtime.metadata.model_profile_revision,
                provider=runtime.metadata.provider,
                model=runtime.metadata.model,
                cancel=control.cancel,
            )
        except Exception:
            asyncio.run(runtime.close())
            task_store.update_task(
                task_id,
                status="failed",
                last_error="Subagent Run could not be registered.",
                terminal_summary="Subagent task failed before startup.",
            )
            raise

        task_store.update_task(
            task_id,
            status="running",
            progress_summary="Subagent task is running.",
        )
        event_store.append_event(
            task_id,
            "task.started",
            message="Subagent task is running.",
            payload={"runner": "subagent", "run_id": run_id},
        )
        threading.Thread(
            target=self._run_worker,
            kwargs={
                "runtime": runtime,
                "request": request,
                "run_id": run_id,
                "child_session_id": child_session_id,
                "control": control,
                "task_store": task_store,
                "event_store": event_store,
            },
            name=f"openppx-{run_id}",
            daemon=True,
        ).start()
        return task_store.get_task(task_id) or task

    def _run_worker(
        self,
        *,
        runtime: AssembledRuntime,
        request: SubagentSpawnRequest,
        run_id: str,
        child_session_id: str,
        control: _SubagentTaskControl,
        task_store: Any,
        event_store: TaskEventStore,
    ) -> None:
        """Execute one detached worker and atomically publish terminal facts."""

        async def _execute() -> str:
            loop = asyncio.get_running_loop()
            task = asyncio.current_task()
            if task is None:  # pragma: no cover - asyncio always supplies it
                raise RuntimeError("Subagent task is unavailable.")
            control.attach(loop, task)
            await runtime.session_service.create_session(
                app_name=runtime.agent.name,
                user_id=str(request.user_id),
                session_id=child_session_id,
            )
            return await runtime.run_text(
                str(request.prompt),
                user_id=str(request.user_id),
                session_id=child_session_id,
            )

        status: TerminalRunState
        result = ""
        error = ""
        error_type = ""
        try:
            result = asyncio.run(_execute())
        except asyncio.CancelledError:
            status = "cancelled"
        except BaseException as exc:  # pragma: no cover - defensive thread boundary
            status = "failed"
            error = "Subagent task failed during execution."
            error_type = type(exc).__name__
        else:
            status = "completed"

        self._complete_run(run_id, state=status)
        bounded_result = result[:_MAX_SUBAGENT_RESULT_CHARS]
        current = task_store.get_task(str(request.task_id))
        payload = dict(current.runner_payload if current is not None else {})
        payload.update(
            {
                "result": bounded_result,
                "result_truncated": len(result) > len(bounded_result),
            }
        )
        summary = (
            bounded_result[:2000]
            if status == "completed"
            else (
                "Subagent task was cancelled."
                if status == "cancelled"
                else "Subagent task failed."
            )
        )
        task_store.update_task(
            str(request.task_id),
            status=status,
            runner_payload=payload,
            progress_summary=summary,
            terminal_summary=summary,
            last_error=error,
        )
        event_store.append_event(
            str(request.task_id),
            f"task.{status}",
            message=summary,
            payload={
                "runner": "subagent",
                "run_id": run_id,
                **({"error_type": error_type} if error_type else {}),
            },
        )
        try:
            asyncio.run(
                self._append_function_response(
                    runtime=runtime,
                    request=request,
                    status=status,
                    result=bounded_result,
                    error=error,
                )
            )
        except Exception as exc:
            event_store.append_event(
                str(request.task_id),
                "task.result_bridge_failed",
                message="Subagent result could not be appended to the parent Session.",
                payload={"runner": "subagent", "error_type": type(exc).__name__},
            )
        finally:
            asyncio.run(runtime.close())

    async def _append_function_response(
        self,
        *,
        runtime: AssembledRuntime,
        request: SubagentSpawnRequest,
        status: TerminalRunState,
        result: str,
        error: str,
    ) -> None:
        """Append a native ADK long-running FunctionResponse to the parent Session."""
        session = await runtime.session_service.get_session(
            app_name=runtime.agent.name,
            user_id=str(request.user_id),
            session_id=str(request.session_id),
        )
        if session is None:
            raise RuntimeError("Parent Session is no longer available.")
        response: dict[str, Any] = {
            "status": status,
            "task_id": str(request.task_id),
        }
        if status == "completed":
            response["result"] = result
        elif error:
            response["error"] = error
        part = types.Part.from_function_response(
            name="spawn_subagent",
            response=response,
        )
        assert part.function_response is not None
        part.function_response.id = str(request.function_call_id)
        await runtime.session_service.append_event(
            session,
            Event(
                invocation_id=str(request.invocation_id),
                author="user",
                content=types.Content(role="user", parts=[part]),
            ),
        )


__all__ = ["SubagentRuntimeManager"]
