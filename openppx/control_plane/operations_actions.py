"""Operations Actions over Node-owned Task, automation, usage, and audit facts."""

from __future__ import annotations

from typing import cast

from openppx.actions import (
    ActionError,
    ActionFailure,
    ActionRegistry,
    ActionSpec,
    SlashCommandSpec,
    SlashCommandArgumentSpec,
    SlashCommandError,
    SlashInvocationContext,
)
from openppx.governance import AuditQuery
from openppx.operations import OperationsService
from openppx.runtime.cron_service import CronSchedule

from .input_models import (
    EmptyInput,
    OperationsAuditListInput,
    OperationsCronCreateInput,
    OperationsCronEnableInput,
    OperationsCronIdentityInput,
    OperationsCronListInput,
    OperationsCronRunInput,
    OperationsCronUpdateInput,
    OperationsContextCompactionConfigureInput,
    OperationsHeartbeatRunInput,
    OperationsHeartbeatConfigureInput,
    OperationsTaskControlInput,
    OperationsTaskIdentityInput,
    OperationsUsageReadInput,
    TaskListInput,
    TaskCommandInput,
)


def register_operations_actions(registry: ActionRegistry, service: OperationsService) -> None:
    """Register the complete transport-neutral Node Operations surface."""
    registry.register(_read_spec("operations.overview", "Operations overview", "Summarize Node operations.", EmptyInput), lambda _c, _v: _call(service.overview))
    registry.register(_read_spec("operations.health", "Node health", "Inspect unified Node component health.", EmptyInput), lambda _c, _v: _call(service.health))
    registry.register(
        _read_spec("operations.task.list", "List tasks", "List durable Node Tasks.", TaskListInput, scope="task"),
        lambda _c, value: _call(lambda: service.list_tasks(session_id=cast(TaskListInput, value).session_id, limit=cast(TaskListInput, value).limit)),
    )
    registry.register(
        _read_spec("operations.task.get", "Inspect task", "Inspect one durable Task and its control surface.", OperationsTaskIdentityInput, scope="task"),
        lambda _c, value: _call(lambda: service.task_detail(cast(OperationsTaskIdentityInput, value).task_id)),
    )
    registry.register(
        _read_spec("operations.task.output", "Read task output", "Read retained output for one durable Task.", OperationsTaskIdentityInput, scope="task"),
        lambda _c, value: _call(lambda: service.task_output(cast(OperationsTaskIdentityInput, value).task_id)),
    )
    registry.register(
        _write_spec("operations.task.control", "Control task", "Dispatch one runner-supported durable Task action.", OperationsTaskControlInput, risk="high", scope="task"),
        lambda _c, value: _call(lambda: _control_task(service, cast(OperationsTaskControlInput, value))),
    )
    registry.register(
        ActionSpec(
            action_id="task.command",
            namespace="task",
            title="Inspect or control Task",
            description="Inspect or control one durable Task through its runner-declared capabilities.",
            input_model=TaskCommandInput,
            scope="task",
            required_capabilities=frozenset({"task.control"}),
            permission="task.control",
            risk="medium",
            operation="mutation",
            success_presentation="panel",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/task",
                    title="Task",
                    description="List, inspect, pause, resume, cancel, retry, or read one durable Task.",
                    icon="list-checks",
                    arg_hint="[list|show|pause|resume|cancel|retry|output|artifacts] [task-id]",
                    arguments=(
                        SlashCommandArgumentSpec(
                            name="request",
                            value_type="text",
                            description="A Task operation and optional Task id.",
                        ),
                    ),
                    order=71,
                ),
            ),
        ),
        lambda _context, value: _call(lambda: _task_command(service, cast(TaskCommandInput, value))),
        slash_input=_task_command_slash_input,
    )
    registry.register(
        _read_spec(
            "operations.cron.list",
            "List Cron jobs",
            "List Node Cron jobs and recent history.",
            OperationsCronListInput,
            slash_commands=(SlashCommandSpec(
                command="/cron",
                title="Show schedules",
                description="List scheduled Agent turns and recent Cron history.",
                icon="calendar-clock",
                order=100,
            ),),
        ),
        lambda _c, value: _call(lambda: service.list_cron(include_disabled=cast(OperationsCronListInput, value).include_disabled, history_limit=cast(OperationsCronListInput, value).history_limit)),
        slash_input=_operations_read_slash_input,
    )
    registry.register(
        _write_spec("operations.cron.create", "Create Cron job", "Create one Agent-scoped scheduled turn.", OperationsCronCreateInput, risk="high"),
        lambda _c, value: _call(lambda: _create_cron(service, cast(OperationsCronCreateInput, value))),
    )
    registry.register(
        _write_spec("operations.cron.update", "Update Cron job", "Update one Agent-scoped scheduled turn.", OperationsCronUpdateInput, risk="high"),
        lambda _c, value: _call(lambda: _update_cron(service, cast(OperationsCronUpdateInput, value))),
    )
    registry.register(
        _write_spec("operations.cron.enable", "Set Cron state", "Enable or disable one Cron job.", OperationsCronEnableInput, risk="high"),
        lambda _c, value: _call(lambda: service.enable_cron(cast(OperationsCronEnableInput, value).job_id, enabled=cast(OperationsCronEnableInput, value).enabled)),
    )
    registry.register(
        _write_spec("operations.cron.remove", "Remove Cron job", "Remove one Node Cron job.", OperationsCronIdentityInput, risk="high"),
        lambda _c, value: _call(lambda: service.remove_cron(cast(OperationsCronIdentityInput, value).job_id)),
    )
    registry.register(
        _write_spec("operations.cron.run", "Run Cron job", "Run one Cron job immediately.", OperationsCronRunInput, risk="high"),
        lambda _c, value: _call(lambda: service.run_cron(cast(OperationsCronRunInput, value).job_id, force=cast(OperationsCronRunInput, value).force)),
    )
    registry.register(
        _read_spec(
            "operations.heartbeat.status",
            "Heartbeat status",
            "Inspect Node heartbeat state.",
            EmptyInput,
            slash_commands=(SlashCommandSpec(
                command="/heartbeat",
                title="Show heartbeat",
                description="Inspect the Node heartbeat schedule and latest result.",
                icon="heart-pulse",
                order=110,
            ),),
        ),
        lambda _c, _v: _call(service.heartbeat_status),
        slash_input=_operations_read_slash_input,
    )
    registry.register(
        _write_spec("operations.heartbeat.run", "Run heartbeat", "Run one immediate Node heartbeat.", OperationsHeartbeatRunInput, risk="medium"),
        lambda _c, value: _call(lambda: service.run_heartbeat(reason=cast(OperationsHeartbeatRunInput, value).reason)),
    )
    registry.register(
        _write_spec("operations.heartbeat.configure", "Configure heartbeat", "Persist the complete Node heartbeat policy.", OperationsHeartbeatConfigureInput, risk="high"),
        lambda _c, value: _call(lambda: _configure_heartbeat(service, cast(OperationsHeartbeatConfigureInput, value))),
    )
    registry.register(
        _read_spec(
            "operations.compaction.status",
            "Context compaction status",
            "Inspect the Node context compaction policy and effective model thresholds.",
            EmptyInput,
        ),
        lambda _c, _v: _call(service.context_compaction_status),
    )
    registry.register(
        _write_spec(
            "operations.compaction.configure",
            "Configure context compaction",
            "Persist the Node context compaction percentage.",
            OperationsContextCompactionConfigureInput,
            risk="high",
        ),
        lambda _c, value: _call(
            lambda: service.configure_context_compaction(
                enabled=cast(OperationsContextCompactionConfigureInput, value).enabled,
                threshold_percent=cast(
                    OperationsContextCompactionConfigureInput, value
                ).threshold_percent,
            )
        ),
    )
    registry.register(
        _read_spec(
            "operations.usage.read",
            "Read usage",
            "Read Node-local model token usage.",
            OperationsUsageReadInput,
            slash_commands=(SlashCommandSpec(
                command="/usage",
                title="Show usage",
                description="Show recent model requests and token usage.",
                icon="gauge",
                order=120,
            ),),
        ),
        lambda _c, value: _call(lambda: service.usage(limit=cast(OperationsUsageReadInput, value).limit, provider=cast(OperationsUsageReadInput, value).provider)),
        slash_input=_operations_read_slash_input,
    )
    registry.register(
        ActionSpec(
            action_id="operations.audit.list",
            namespace="operations",
            title="Read audit",
            description="Read redacted Action audit facts.",
            input_model=OperationsAuditListInput,
            scope="node",
            required_capabilities=frozenset({"audit.read"}),
            permission="audit.read",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _c, value: _call(lambda: _audit(service, cast(OperationsAuditListInput, value))),
    )


def _read_spec(action_id, title, description, input_model, *, scope="node", slash_commands=()) -> ActionSpec:
    projections = ("cli", "slash", "desktop", "mobile") if slash_commands else ("cli", "desktop", "mobile")
    return ActionSpec(
        action_id=action_id,
        namespace="operations",
        title=title,
        description=description,
        input_model=input_model,
        scope=scope,
        required_capabilities=frozenset({"operations.read"}),
        permission="operations.read",
        projections=projections,
        slash_commands=slash_commands,
    )


def _operations_read_slash_input(
    command: SlashCommandSpec,
    _args: str,
    _context: SlashInvocationContext,
) -> dict[str, object]:
    """Map read-only Operations aliases to their strict Action inputs."""
    if command.command == "/cron":
        return {"includeDisabled": True, "historyLimit": 10}
    if command.command == "/usage":
        return {"limit": 20, "provider": None}
    return {}


def _task_command_slash_input(
    _command: SlashCommandSpec,
    args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    """Parse `/task` without bypassing the typed Task Action contract."""
    tokens = args.strip().split()
    if not tokens:
        return {"operation": "list", "sessionId": context.session_id, "limit": 20}
    operation = tokens[0].lower()
    if operation not in {"list", "show", "pause", "resume", "cancel", "retry", "output", "artifacts"}:
        raise SlashCommandError(
            "command_usage",
            "Use /task list, show, pause, resume, cancel, retry, output, or artifacts.",
        )
    if operation == "list":
        if len(tokens) > 2:
            raise SlashCommandError("Usage: /task list [limit]")
        limit = 20
        if len(tokens) == 2:
            try:
                limit = int(tokens[1])
            except ValueError as exc:
                raise SlashCommandError("Task list limit must be an integer.") from exc
        return {"operation": "list", "sessionId": context.session_id, "limit": limit}
    if len(tokens) != 2:
        raise SlashCommandError(f"Usage: /task {operation} <task-id>")
    return {"operation": operation, "taskId": tokens[1], "sessionId": context.session_id, "limit": 20}


def _task_command(service: OperationsService, value: TaskCommandInput) -> dict[str, object]:
    """Route one Task command to the mature Operations/TaskController surface."""
    if value.operation == "list":
        return service.list_tasks(session_id=value.session_id, limit=value.limit)
    task_id = value.task_id or ""
    if value.operation in {"show", "artifacts"}:
        detail = service.task_detail(task_id)
        if value.operation == "artifacts":
            return {"taskId": task_id, "items": detail.get("artifacts", [])}
        return detail
    if value.operation == "output":
        return service.task_output(task_id)
    action = "restart" if value.operation == "retry" else value.operation
    return service.control_task(task_id, action=action, content="", inline_budget_ms=None)


def _write_spec(action_id, title, description, input_model, *, risk, scope="node") -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        namespace="operations",
        title=title,
        description=description,
        input_model=input_model,
        scope=scope,
        required_capabilities=frozenset({"operations.write"}),
        permission="operations.write",
        risk=risk,
        confirmation="required",
        operation="mutation",
        projections=("cli", "desktop", "mobile"),
    )


def _create_cron(service: OperationsService, value: OperationsCronCreateInput) -> dict[str, object]:
    schedule = CronSchedule(
        kind=value.schedule.kind,
        every_seconds=value.schedule.every_seconds,
        cron_expr=value.schedule.cron_expression,
        at_ms=value.schedule.at_ms,
        tz=value.schedule.timezone,
    )
    return service.create_cron(
        name=value.name,
        agent_id=value.agent_id,
        user_id=value.user_id,
        message=value.message,
        schedule=schedule,
        delete_after_run=value.delete_after_run,
    )


def _control_task(service: OperationsService, value: OperationsTaskControlInput) -> dict[str, object]:
    return service.control_task(
        value.task_id,
        action=value.action,
        content=value.content,
        inline_budget_ms=value.inline_budget_ms,
    )


def _update_cron(service: OperationsService, value: OperationsCronUpdateInput) -> dict[str, object]:
    schedule = CronSchedule(
        kind=value.schedule.kind,
        every_seconds=value.schedule.every_seconds,
        cron_expr=value.schedule.cron_expression,
        at_ms=value.schedule.at_ms,
        tz=value.schedule.timezone,
    )
    return service.update_cron(
        value.job_id,
        name=value.name,
        agent_id=value.agent_id,
        user_id=value.user_id,
        message=value.message,
        schedule=schedule,
        delete_after_run=value.delete_after_run,
    )


def _configure_heartbeat(service: OperationsService, value: OperationsHeartbeatConfigureInput) -> dict[str, object]:
    return service.configure_heartbeat(
        enabled=value.enabled,
        every_seconds=value.every_seconds,
        prompt=value.prompt,
        active_hours={
            "start": value.active_hours.start,
            "end": value.active_hours.end,
            "timezone": value.active_hours.timezone,
        },
    )


def _audit(service: OperationsService, value: OperationsAuditListInput) -> dict[str, object]:
    return service.audit_rows(
        AuditQuery(
            limit=value.limit,
            actor_id=value.actor_id,
            agent_id=value.agent_id,
            run_id=value.run_id,
            extension_id=value.extension_id,
            action_id=value.action_id,
            outcome=value.outcome,
        )
    )


def _call(operation):
    try:
        return operation()
    except LookupError as exc:
        code = str(exc) if str(exc) else "operations_resource_not_found"
        raise ActionFailure(ActionError(code, "The requested Operations resource was not found.")) from exc
    except ValueError as exc:
        code = str(exc) if str(exc) in {"agent_not_enabled", "task_action_unavailable"} else "operations_invalid_request"
        message = "The requested Task action is not available." if code == "task_action_unavailable" else "The Operations request is not valid."
        raise ActionFailure(ActionError(code, message)) from exc
    except RuntimeError as exc:
        raise ActionFailure(ActionError("operations_unavailable", "Node Operations are not available.")) from exc


__all__ = ["register_operations_actions"]
