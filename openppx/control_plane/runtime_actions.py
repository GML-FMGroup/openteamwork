"""Session and Run Actions backed by the Node Runtime Supervisor."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from openppx.actions import (
    ActionError,
    ActionFailure,
    ActionRegistry,
    ActionSpec,
    SlashCommandArgumentSpec,
    SlashCommandError,
    SlashCommandSpec,
    SlashInvocationContext,
)
from openppx.runtime.node_runtime import (
    NodeRuntimeSupervisor,
    RunNotActiveError,
    RunNotFoundError,
    RuntimeSupervisorError,
)
from openppx.runtime.session_rewind import SessionRewindError
from openppx.runtime.task_execution import TaskController
from openppx.runtime.session_metadata_store import SessionMetadataStore

from .input_models import (
    RunStopInput,
    RuntimeInspectInput,
    SessionHistoryInput,
    SessionArchiveInput,
    SessionIdentityInput,
    SessionNewInput,
    SessionRenameInput,
    SessionRewindInput,
    TaskListInput,
)


def register_runtime_actions(
    registry: ActionRegistry,
    supervisor: NodeRuntimeSupervisor,
    *,
    task_controller: TaskController | None = None,
    session_metadata: SessionMetadataStore,
) -> None:
    """Register shared Session, Run, and durable Task control Actions."""
    tasks = task_controller or _task_controller_from_supervisor(supervisor)
    metadata = session_metadata
    registry.register(
        ActionSpec(
            action_id="session.new",
            namespace="session",
            title="New session",
            description="Create a new Agent conversation Session.",
            input_model=SessionNewInput,
            scope="agent",
            required_capabilities=frozenset({"session.write"}),
            permission="session.write",
            operation="mutation",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/new",
                    title="New session",
                    description="Create and switch to a fresh conversation Session.",
                    icon="square-pen",
                    lifecycle="finalize_active_turn",
                    order=20,
                ),
            ),
        ),
        lambda _context, input_data: _new_session(
            supervisor,
            cast(SessionNewInput, input_data),
        ),
        slash_input=_new_session_slash_input,
    )
    registry.register(
        ActionSpec(
            action_id="session.history",
            namespace="session",
            title="Session history",
            description="Read recent visible conversation messages.",
            input_model=SessionHistoryInput,
            scope="session",
            required_capabilities=frozenset({"session.read"}),
            permission="session.read",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/history",
                    title="Show history",
                    description="Show recent visible messages in this Session.",
                    icon="history",
                    arg_hint="[limit]",
                    arguments=(
                        SlashCommandArgumentSpec(
                            name="limit",
                            value_type="integer",
                            description="Maximum number of visible messages to return.",
                        ),
                    ),
                    order=60,
                ),
            ),
        ),
        lambda _context, input_data: _session_history(
            supervisor,
            cast(SessionHistoryInput, input_data),
        ),
        slash_input=_session_history_slash_input,
    )
    registry.register(
        ActionSpec(
            action_id="session.rewind",
            namespace="session",
            title="Rewind session",
            description="Rewind model context before one visible invocation.",
            input_model=SessionRewindInput,
            scope="session",
            required_capabilities=frozenset({"session.write"}),
            permission="session.write",
            risk="medium",
            operation="mutation",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/rewind",
                    title="Rewind conversation",
                    description="Hide the latest turn, or a selected invocation, from future model context.",
                    icon="undo-2",
                    arg_hint="[last|invocation-id]",
                    arguments=(
                        SlashCommandArgumentSpec(
                            name="invocation",
                            value_type="string",
                            description="The invocation ID to rewind before, or last.",
                        ),
                    ),
                    lifecycle="finalize_active_turn",
                    order=25,
                ),
            ),
        ),
        lambda _context, input_data: _rewind_session(
            supervisor,
            cast(SessionRewindInput, input_data),
        ),
        slash_input=_rewind_session_slash_input,
    )
    registry.register(
        ActionSpec(
            action_id="run.stop",
            namespace="run",
            title="Stop run",
            description="Request cooperative cancellation of one active Run.",
            input_model=RunStopInput,
            scope="run",
            required_capabilities=frozenset({"run.control"}),
            permission="run.control",
            risk="medium",
            operation="mutation",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/stop",
                    title="Stop current run",
                    description="Request cooperative cancellation of the active Run.",
                    icon="square",
                    lifecycle="stop_active_turn",
                    order=30,
                ),
            ),
        ),
        lambda _context, input_data: _stop_run(
            supervisor,
            cast(RunStopInput, input_data),
        ),
        slash_input=_stop_run_slash_input,
    )
    registry.register(
        ActionSpec(
            action_id="runtime.inspect",
            namespace="runtime",
            title="Inspect runtime",
            description="Inspect effective Runtime snapshots, limits, and active work without exposing sensitive values.",
            input_model=RuntimeInspectInput,
            scope="node",
            required_capabilities=frozenset({"system.read"}),
            permission="system.read",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/inspect",
                    title="Inspect runtime",
                    description="Show the current Agent Runtime snapshot and active work.",
                    icon="scan-search",
                    order=45,
                ),
            ),
        ),
        lambda _context, input_data: _inspect_runtime(
            supervisor,
            cast(RuntimeInspectInput, input_data),
        ),
        slash_input=_runtime_inspect_slash_input,
    )
    registry.register(
        ActionSpec(
            action_id="task.list",
            namespace="task",
            title="List tasks",
            description="List recent durable Tasks and their current state.",
            input_model=TaskListInput,
            scope="task",
            required_capabilities=frozenset({"task.read"}),
            permission="task.read",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/tasks",
                    title="Show tasks",
                    description="Show recent Tasks for this Session.",
                    icon="list-checks",
                    arg_hint="[limit]",
                    arguments=(
                        SlashCommandArgumentSpec(
                            name="limit",
                            value_type="integer",
                            description="Maximum number of durable Tasks to return.",
                        ),
                    ),
                    order=70,
                ),
            ),
        ),
        lambda _context, input_data: _list_tasks(
            tasks,
            cast(TaskListInput, input_data),
        ),
        availability=lambda _context: None if tasks is not None else "runtime_unavailable",
        slash_input=_task_list_slash_input,
    )
    registry.register(
        ActionSpec(
            action_id="session.rename",
            namespace="session",
            title="Rename session",
            description="Assign a user-facing title without changing conversation history.",
            input_model=SessionRenameInput,
            scope="session",
            required_capabilities=frozenset({"session.write"}),
            permission="session.write",
            operation="mutation",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, input_data: _rename_session(
            supervisor, metadata, cast(SessionRenameInput, input_data)
        ),
    )
    registry.register(
        ActionSpec(
            action_id="session.archive",
            namespace="session",
            title="Archive or restore session",
            description="Hide or restore a Session without deleting its conversation data.",
            input_model=SessionArchiveInput,
            scope="session",
            required_capabilities=frozenset({"session.write"}),
            permission="session.write",
            operation="mutation",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, input_data: _archive_session(
            supervisor, metadata, cast(SessionArchiveInput, input_data)
        ),
    )
    registry.register(
        ActionSpec(
            action_id="session.fork",
            namespace="session",
            title="Fork session",
            description="Create a new Session from the durable history of an existing Session.",
            input_model=SessionIdentityInput,
            scope="session",
            required_capabilities=frozenset({"session.write"}),
            permission="session.write",
            risk="medium",
            operation="mutation",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, input_data: _fork_session(
            supervisor, metadata, cast(SessionIdentityInput, input_data)
        ),
    )
    registry.register(
        ActionSpec(
            action_id="session.export",
            namespace="session",
            title="Export session",
            description="Export bounded visible conversation history as structured JSON.",
            input_model=SessionIdentityInput,
            scope="session",
            required_capabilities=frozenset({"session.read"}),
            permission="session.read",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, input_data: _export_session(
            supervisor, metadata, cast(SessionIdentityInput, input_data)
        ),
    )
    registry.register(
        ActionSpec(
            action_id="session.delete",
            namespace="session",
            title="Delete session",
            description="Permanently remove one ADK Session after explicit confirmation.",
            input_model=SessionIdentityInput,
            scope="session",
            required_capabilities=frozenset({"session.write"}),
            permission="session.write",
            risk="high",
            confirmation="required",
            operation="mutation",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, input_data: _delete_session(
            supervisor, metadata, cast(SessionIdentityInput, input_data)
        ),
    )


def _required_context(value: str | None, field: str) -> str:
    if value:
        return value
    raise SlashCommandError("command_context_required", f"The slash command requires {field} context.")


def _new_session_slash_input(
    _command: SlashCommandSpec,
    _args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    return {
        "agentId": _required_context(context.agent_id, "Agent"),
        "userId": context.user_id,
    }


def _stop_run_slash_input(
    _command: SlashCommandSpec,
    _args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    return {"runId": _required_context(context.run_id, "active Run")}


def _runtime_inspect_slash_input(
    _command: SlashCommandSpec,
    _args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    return {
        "userId": context.user_id,
        "agentId": context.agent_id,
        "sessionId": context.session_id,
        "runId": context.run_id,
        "limit": 20,
    }


def _bounded_limit(args: str, *, default: int = 20) -> int:
    if not args:
        return default
    try:
        value = int(args)
    except ValueError as exc:
        raise SlashCommandError("command_arguments_invalid", "The command limit must be an integer.") from exc
    if value < 1 or value > 100:
        raise SlashCommandError("command_arguments_invalid", "The command limit must be between 1 and 100.")
    return value


def _session_history_slash_input(
    _command: SlashCommandSpec,
    args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    return {
        "agentId": _required_context(context.agent_id, "Agent"),
        "userId": context.user_id,
        "sessionId": _required_context(context.session_id, "Session"),
        "limit": _bounded_limit(args),
    }


def _rewind_session_slash_input(
    _command: SlashCommandSpec,
    args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    selector = args.strip()
    return {
        "agentId": _required_context(context.agent_id, "Agent"),
        "userId": context.user_id,
        "sessionId": _required_context(context.session_id, "Session"),
        "beforeInvocationId": None if not selector or selector.lower() == "last" else selector,
    }


def _task_list_slash_input(
    _command: SlashCommandSpec,
    args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    return {
        "sessionId": context.session_id,
        "limit": _bounded_limit(args),
    }


def _new_session(
    supervisor: NodeRuntimeSupervisor,
    input_data: SessionNewInput,
) -> dict[str, object]:
    try:
        session = supervisor.create_session_sync(
            input_data.agent_id,
            user_id=input_data.user_id,
        )
    except RuntimeSupervisorError as exc:
        raise ActionFailure(
            ActionError("runtime_unavailable", "The Node runtime is not available.")
        ) from exc
    last_update = getattr(session, "last_update_time", None)
    if isinstance(last_update, (int, float)):
        updated_at = datetime.fromtimestamp(last_update, tz=timezone.utc).isoformat()
    else:
        updated_at = datetime.now(timezone.utc).isoformat()
    return {
        "session": {
            "id": str(getattr(session, "id", "")),
            "agentId": input_data.agent_id,
            "subjectPrincipalId": input_data.user_id,
            "title": "New chat",
            "updatedAt": updated_at,
            "lastMessagePreview": "",
            "archived": False,
        }
    }


def _stop_run(
    supervisor: NodeRuntimeSupervisor,
    input_data: RunStopInput,
) -> dict[str, object]:
    try:
        run = supervisor.stop_run(input_data.run_id)
    except RunNotFoundError as exc:
        raise ActionFailure(
            ActionError("run_not_found", "The requested Run was not found.")
        ) from exc
    except RunNotActiveError as exc:
        raise ActionFailure(
            ActionError("run_not_active", "The requested Run is no longer active.")
        ) from exc
    return {
        "run": {
            "id": run.run_id,
            "agentId": run.agent_id,
            "sessionId": run.session_id,
            "snapshotRevision": run.snapshot_revision,
            "modelProfileId": run.model_profile_id,
            "modelProfileRevision": run.model_profile_revision,
            "provider": run.provider,
            "model": run.model,
            "startedAt": run.started_at,
            "state": run.state,
        }
    }


def _session_history(
    supervisor: NodeRuntimeSupervisor,
    input_data: SessionHistoryInput,
) -> dict[str, object]:
    try:
        items = supervisor.session_history_sync(
            input_data.agent_id,
            user_id=input_data.user_id,
            session_id=input_data.session_id,
            limit=input_data.limit,
        )
    except RuntimeSupervisorError as exc:
        raise ActionFailure(ActionError("session_not_found", "The requested Session was not found.")) from exc
    return {"items": items}


def _rewind_session(
    supervisor: NodeRuntimeSupervisor,
    input_data: SessionRewindInput,
) -> dict[str, object]:
    try:
        target = supervisor.rewind_session_sync(
            input_data.agent_id,
            user_id=input_data.user_id,
            session_id=input_data.session_id,
            before_invocation_id=input_data.before_invocation_id,
        )
    except SessionRewindError as exc:
        raise ActionFailure(ActionError("session_rewind_unavailable", str(exc))) from exc
    except RuntimeSupervisorError as exc:
        raise ActionFailure(ActionError("runtime_unavailable", "The Node runtime is not available.")) from exc
    return {
        "sessionId": input_data.session_id,
        "rewindBeforeInvocationId": target.invocation_id,
        "explicit": target.explicit,
        "visibleEventCount": target.visible_event_count,
    }


def _require_session(supervisor: NodeRuntimeSupervisor, input_data: SessionIdentityInput) -> object:
    session = supervisor.get_session_sync(
        input_data.agent_id,
        user_id=input_data.user_id,
        session_id=input_data.session_id,
    )
    if session is None:
        raise ActionFailure(ActionError("session_not_found", "The requested Session was not found."))
    return session


def _rename_session(
    supervisor: NodeRuntimeSupervisor,
    metadata: SessionMetadataStore,
    input_data: SessionRenameInput,
) -> dict[str, object]:
    _require_session(supervisor, input_data)
    stored = metadata.update(
        session_id=input_data.session_id,
        agent_id=input_data.agent_id,
        principal_id=input_data.user_id,
        title=input_data.title,
    )
    return {"sessionId": stored.session_id, "title": stored.title, "archived": stored.archived}


def _archive_session(
    supervisor: NodeRuntimeSupervisor,
    metadata: SessionMetadataStore,
    input_data: SessionArchiveInput,
) -> dict[str, object]:
    _require_session(supervisor, input_data)
    stored = metadata.update(
        session_id=input_data.session_id,
        agent_id=input_data.agent_id,
        principal_id=input_data.user_id,
        archived=input_data.archived,
    )
    return {"sessionId": stored.session_id, "title": stored.title, "archived": stored.archived}


def _fork_session(
    supervisor: NodeRuntimeSupervisor,
    metadata: SessionMetadataStore,
    input_data: SessionIdentityInput,
) -> dict[str, object]:
    try:
        forked = supervisor.fork_session_sync(
            input_data.agent_id,
            user_id=input_data.user_id,
            session_id=input_data.session_id,
        )
    except RuntimeSupervisorError as exc:
        raise ActionFailure(ActionError("session_not_found", "The requested Session was not found.")) from exc
    session_id = str(getattr(forked, "id", ""))
    source_metadata = metadata.get(input_data.session_id)
    title = f"{source_metadata.title} (fork)" if source_metadata and source_metadata.title else "Forked chat"
    metadata.update(
        session_id=session_id,
        agent_id=input_data.agent_id,
        principal_id=input_data.user_id,
        title=title[:120],
    )
    return {"session": {"id": session_id, "agentId": input_data.agent_id, "title": title[:120], "archived": False}}


def _export_session(
    supervisor: NodeRuntimeSupervisor,
    metadata: SessionMetadataStore,
    input_data: SessionIdentityInput,
) -> dict[str, object]:
    _require_session(supervisor, input_data)
    items = supervisor.session_history_sync(
        input_data.agent_id,
        user_id=input_data.user_id,
        session_id=input_data.session_id,
        limit=100,
    )
    stored = metadata.get(input_data.session_id)
    return {
        "sessionId": input_data.session_id,
        "agentId": input_data.agent_id,
        "title": stored.title if stored else None,
        "archived": stored.archived if stored else False,
        "items": items,
        "exportedAt": datetime.now(timezone.utc).isoformat(),
    }


def _delete_session(
    supervisor: NodeRuntimeSupervisor,
    metadata: SessionMetadataStore,
    input_data: SessionIdentityInput,
) -> dict[str, object]:
    try:
        supervisor.delete_session_sync(
            input_data.agent_id,
            user_id=input_data.user_id,
            session_id=input_data.session_id,
        )
    except RuntimeSupervisorError as exc:
        raise ActionFailure(ActionError("session_not_found", "The requested Session was not found.")) from exc
    metadata.delete(input_data.session_id)
    return {"sessionId": input_data.session_id, "deleted": True, "artifactsRetained": False}


def _task_controller_from_supervisor(supervisor: NodeRuntimeSupervisor) -> TaskController | None:
    """Resolve the Node-owned Task store without making registration depend on internals."""
    assembler = getattr(supervisor, "assembler", None)
    services = getattr(assembler, "services", None)
    task_store = getattr(services, "task_store", None)
    return TaskController(task_store=task_store) if task_store is not None else None


def _list_tasks(controller: TaskController | None, input_data: TaskListInput) -> dict[str, object]:
    if controller is None:
        raise ActionFailure(ActionError("runtime_unavailable", "Task storage is not available."))
    result = controller.list_tasks(session_id=input_data.session_id, limit=input_data.limit)
    return {"items": result.get("items", [])}


def _inspect_runtime(
    supervisor: NodeRuntimeSupervisor,
    input_data: RuntimeInspectInput,
) -> dict[str, object]:
    """Combine ADK Runtime snapshots with durable Goal and Task summaries."""
    try:
        result = supervisor.inspect(
            agent_id=input_data.agent_id,
            session_id=input_data.session_id,
            run_id=input_data.run_id,
            limit=input_data.limit,
        )
    except RuntimeSupervisorError as exc:
        raise ActionFailure(ActionError("runtime_unavailable", "The Node runtime is not available.")) from exc
    except Exception as exc:
        raise ActionFailure(ActionError("runtime_inspection_failed", "The Runtime snapshot could not be inspected.")) from exc

    services = supervisor.assembler.services
    goals = (
        services.goal_store.list_goals(
            session_id=input_data.session_id,
            user_id=input_data.user_id,
            limit=min(input_data.limit, 20),
        )
        if input_data.user_id is not None
        else []
    )
    tasks = services.task_store.list_tasks(
        session_id=input_data.session_id,
        limit=min(input_data.limit, 20),
    )
    result["goals"] = [
        {
            "goalId": goal.goal_id,
            "agentId": goal.agent_id,
            "sessionId": goal.session_id,
            "status": goal.status,
            "revision": goal.revision,
            "objective": goal.objective[:240],
            "activeFlowId": goal.active_flow_id or None,
            "budgetState": goal.budget_state,
            "updatedAtMs": goal.updated_at_ms,
        }
        for goal in goals
    ]
    result["tasks"] = [
        {
            "taskId": task.task_id,
            "sessionId": task.session_id,
            "status": task.status,
            "kind": task.kind,
            "title": task.title[:240],
            "progressSummary": task.progress_summary[:500],
            "updatedAtMs": task.updated_at_ms,
        }
        for task in tasks
    ]
    return result


__all__ = ["register_runtime_actions"]
