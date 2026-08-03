"""Operations Actions over Node-owned Task, automation, usage, and audit facts."""

from __future__ import annotations

from typing import cast

from openppx.actions import ActionError, ActionFailure, ActionRegistry, ActionSpec
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
    OperationsHeartbeatRunInput,
    OperationsUsageReadInput,
    TaskListInput,
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
        _read_spec("operations.cron.list", "List Cron jobs", "List Node Cron jobs and recent history.", OperationsCronListInput),
        lambda _c, value: _call(lambda: service.list_cron(include_disabled=cast(OperationsCronListInput, value).include_disabled, history_limit=cast(OperationsCronListInput, value).history_limit)),
    )
    registry.register(
        _write_spec("operations.cron.create", "Create Cron job", "Create one Agent-scoped scheduled turn.", OperationsCronCreateInput, risk="high"),
        lambda _c, value: _call(lambda: _create_cron(service, cast(OperationsCronCreateInput, value))),
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
        _read_spec("operations.heartbeat.status", "Heartbeat status", "Inspect Node heartbeat state.", EmptyInput),
        lambda _c, _v: _call(service.heartbeat_status),
    )
    registry.register(
        _write_spec("operations.heartbeat.run", "Run heartbeat", "Run one immediate Node heartbeat.", OperationsHeartbeatRunInput, risk="medium"),
        lambda _c, value: _call(lambda: service.run_heartbeat(reason=cast(OperationsHeartbeatRunInput, value).reason)),
    )
    registry.register(
        _read_spec("operations.usage.read", "Read usage", "Read Node-local model token usage.", OperationsUsageReadInput),
        lambda _c, value: _call(lambda: service.usage(limit=cast(OperationsUsageReadInput, value).limit, provider=cast(OperationsUsageReadInput, value).provider)),
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


def _read_spec(action_id, title, description, input_model, *, scope="node") -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        namespace="operations",
        title=title,
        description=description,
        input_model=input_model,
        scope=scope,
        required_capabilities=frozenset({"operations.read"}),
        permission="operations.read",
        projections=("cli", "desktop", "mobile"),
    )


def _write_spec(action_id, title, description, input_model, *, risk) -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        namespace="operations",
        title=title,
        description=description,
        input_model=input_model,
        scope="node",
        required_capabilities=frozenset({"operations.write"}),
        permission="operations.write",
        risk=risk,
        confirmation="required",
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
        code = str(exc) if str(exc) in {"agent_not_enabled"} else "operations_invalid_request"
        raise ActionFailure(ActionError(code, "The Operations request is not valid.")) from exc
    except RuntimeError as exc:
        raise ActionFailure(ActionError("operations_unavailable", "Node Operations are not available.")) from exc


__all__ = ["register_operations_actions"]
