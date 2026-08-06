"""User Automation Actions shared by Desktop, CLI, Slash, and future Mobile."""

from __future__ import annotations

from typing import NoReturn, cast

from openppx.actions import (
    ActionContext,
    ActionError,
    ActionFailure,
    ActionRegistry,
    ActionSpec,
    SlashCommandArgumentSpec,
    SlashCommandError,
    SlashCommandSpec,
    SlashInvocationContext,
)
from openppx.automation import (
    AutomationConflictError,
    AutomationDefinition,
    AutomationNotFoundError,
    AutomationService,
    AutomationStateError,
    AutomationStoreError,
)

from .input_models import (
    AutomationCommandInput,
    AutomationCreateInput,
    AutomationHistoryInput,
    AutomationIdentityInput,
    AutomationListInput,
    AutomationPermissionRevokeInput,
    AutomationRunInput,
    AutomationRunReadInput,
    AutomationTemplateReadInput,
    AutomationTransitionInput,
    AutomationTriggerInput,
    AutomationUpdateInput,
    EmptyInput,
)
from .projections import (
    project_automation_detail,
    project_automation_event,
    project_automation_run,
    project_automation_summary,
    project_automation_trigger,
)


def register_automation_actions(registry: ActionRegistry, service: AutomationService) -> None:
    """Register the complete formal Automation product catalog."""
    registry.register(_spec("automation.list", "List Automations", "List user-created Automation Definitions.", AutomationListInput, "automation.read"), lambda _c, v: _list(service, cast(AutomationListInput, v)))
    registry.register(_spec("automation.read", "Read Automation", "Read one Automation Definition and readiness.", AutomationIdentityInput, "automation.read"), lambda _c, v: _read(service, cast(AutomationIdentityInput, v)))
    registry.register(_spec("automation.create", "Create Automation", "Create one durable user Automation.", AutomationCreateInput, "automation.write", mutation=True), lambda c, v: _create(service, c, cast(AutomationCreateInput, v)))
    registry.register(_spec("automation.update", "Update Automation", "Edit one Automation Definition.", AutomationUpdateInput, "automation.write", mutation=True), lambda c, v: _update(service, c, cast(AutomationUpdateInput, v)))
    for action_id, title, target in (
        ("automation.pause", "Pause Automation", "paused"),
        ("automation.resume", "Resume Automation", "active"),
        ("automation.delete", "Delete Automation", "deleted"),
    ):
        registry.register(
            _spec(action_id, title, f"Transition one Automation to {target}.", AutomationTransitionInput, "automation.write", mutation=True, confirmation="required" if target == "deleted" else "never", risk="high" if target == "deleted" else "medium"),
            lambda c, v, target=target: _transition(service, c, cast(AutomationTransitionInput, v), target),
        )
    registry.register(_spec("automation.run", "Run Automation", "Start an independent Automation Agent Run now.", AutomationRunInput, "automation.run", mutation=True, execution="long_running"), lambda c, v: _run(service, c, cast(AutomationRunInput, v)))
    registry.register(_spec("automation.run.read", "Read Automation Run", "Read one Automation execution result.", AutomationRunReadInput, "automation.read"), lambda _c, v: _read_run(service, cast(AutomationRunReadInput, v)))
    registry.register(_spec("automation.history", "Automation History", "Read Automation runs and lifecycle events.", AutomationHistoryInput, "automation.read"), lambda _c, v: _history(service, cast(AutomationHistoryInput, v)))
    registry.register(_spec("automation.trigger", "Trigger Automation", "Submit a trusted local typed event to an Automation.", AutomationTriggerInput, "automation.run", mutation=True, execution="long_running"), lambda c, v: _trigger(service, c, cast(AutomationTriggerInput, v)))
    registry.register(_spec("automation.template.list", "List Automation Templates", "List reviewed creation starting points.", EmptyInput, "automation.read"), lambda _c, _v: _templates(service))
    registry.register(_spec("automation.template.read", "Read Automation Template", "Read one reviewed Automation template.", AutomationTemplateReadInput, "automation.read"), lambda _c, v: _template(service, cast(AutomationTemplateReadInput, v)))
    registry.register(_spec("automation.permission.preview", "Preview Automation Permissions", "Preview Automation-specific standing permissions.", AutomationIdentityInput, "automation.read"), lambda _c, v: _permission_preview(service, cast(AutomationIdentityInput, v)))
    registry.register(_spec("automation.permission.revoke", "Revoke Automation Permissions", "Revoke standing permissions and block the Automation.", AutomationPermissionRevokeInput, "automation.write", mutation=True, confirmation="required", risk="high"), lambda c, v: _permission_revoke(service, c, cast(AutomationPermissionRevokeInput, v)))
    registry.register(
        ActionSpec(
            action_id="automation.command",
            namespace="automation",
            title="Automations",
            description="List, create, inspect, run, pause, resume, or delete User Automations.",
            input_model=AutomationCommandInput,
            scope="automation",
            required_capabilities=frozenset({"automation.write"}),
            permission="automation.write",
            risk="medium",
            operation="mutation",
            success_presentation="panel",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/automation",
                    title="Automations",
                    description="Manage user-created scheduled or event workflows.",
                    icon="clock",
                    arg_hint="[list|create ...|show ID|run ID|pause ID|resume ID|history ID|delete ID]",
                    arguments=(SlashCommandArgumentSpec(name="request", value_type="text", description="Automation operation and target."),),
                    order=36,
                ),
            ),
        ),
        lambda c, v: _command(service, c, cast(AutomationCommandInput, v)),
        slash_input=_slash_input,
    )


def _spec(action_id, title, description, input_model, permission, *, mutation=False, confirmation="never", risk=None, execution="sync") -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        namespace="automation",
        title=title,
        description=description,
        input_model=input_model,
        scope="automation",
        required_capabilities=frozenset({permission}),
        permission=permission,
        risk=risk or ("medium" if mutation else "low"),
        confirmation=confirmation,
        execution=execution,
        operation="mutation" if mutation else "read",
        success_presentation="panel",
        projections=("cli", "desktop", "mobile"),
    )


def _detail(service: AutomationService, definition: AutomationDefinition) -> dict[str, object]:
    runs = service.store.list_runs(definition.automation_id, limit=1)
    return project_automation_detail(
        definition,
        service.store.trigger_for(definition.automation_id),
        runs[0] if runs else None,
        service.readiness(definition),
    )


def _list(service: AutomationService, value: AutomationListInput) -> dict[str, object]:
    items = []
    for definition in service.list_definitions(user_id=value.user_id, statuses=value.statuses, limit=value.limit):
        runs = service.store.list_runs(definition.automation_id, limit=1)
        items.append(project_automation_summary(definition, service.store.trigger_for(definition.automation_id), runs[0] if runs else None))
    return {"items": items}


def _read(service: AutomationService, value: AutomationIdentityInput) -> dict[str, object]:
    return _detail(service, service.read_definition(value.automation_id, user_id=value.user_id))


def _create(service: AutomationService, context: ActionContext, value: AutomationCreateInput) -> dict[str, object]:
    try:
        data = value.model_dump(mode="json", by_alias=False)
        schedule = data.pop("schedule")
        local_event = data.pop("local_event")
        data["schedule"] = _camel_schedule(schedule)
        data["local_event"] = _camel_local_event(local_event)
        definition = service.create_definition(actor_id=context.actor_id, request_id=context.request_id, correlation_id=context.correlation_id, **data)
        return _detail(service, definition)
    except Exception as exc:
        _raise_failure(exc)


def _update(service: AutomationService, context: ActionContext, value: AutomationUpdateInput) -> dict[str, object]:
    definition = service.read_definition(value.automation_id, user_id=value.user_id)
    try:
        dumped = value.model_dump(mode="json", by_alias=False, exclude_unset=True)
        data = {name: item for name, item in dumped.items() if name not in {"automation_id", "user_id"}}
        if "schedule" in data and data["schedule"] is not None:
            data["schedule"] = _camel_schedule(data["schedule"])
        if "local_event" in data and data["local_event"] is not None:
            data["local_event"] = _camel_local_event(data["local_event"])
        updated = service.update_definition(definition, actor_id=context.actor_id, correlation_id=context.correlation_id, **data)
        return _detail(service, updated)
    except Exception as exc:
        _raise_failure(exc)


def _transition(service: AutomationService, context: ActionContext, value: AutomationTransitionInput, target: str) -> dict[str, object]:
    definition = service.read_definition(value.automation_id, user_id=value.user_id)
    try:
        updated = service.transition(definition, status=target, expected_revision=value.expected_revision, actor_id=context.actor_id, correlation_id=context.correlation_id)
        return _detail(service, updated) if target != "deleted" else {"automationId": value.automation_id, "deleted": True}
    except Exception as exc:
        _raise_failure(exc)


def _run(service: AutomationService, context: ActionContext, value: AutomationRunInput) -> dict[str, object]:
    definition = service.read_definition(value.automation_id, user_id=value.user_id)
    try:
        return {"run": project_automation_run(service.run_now(definition, actor_id=context.actor_id, correlation_id=context.correlation_id, input_snapshot=value.input))}
    except Exception as exc:
        _raise_failure(exc)


def _trigger(service: AutomationService, context: ActionContext, value: AutomationTriggerInput) -> dict[str, object]:
    definition = service.read_definition(value.automation_id, user_id=value.user_id)
    try:
        run = service.trigger_local_event(
            definition, actor_id=context.actor_id, correlation_id=context.correlation_id,
            event_key=value.event_key, event_id=value.event_id, input_snapshot=value.input,
        )
        return {"run": project_automation_run(run)}
    except Exception as exc:
        _raise_failure(exc)


def _read_run(service: AutomationService, value: AutomationRunReadInput) -> dict[str, object]:
    run = service.store.read_run(value.automation_run_id)
    service.read_definition(run.automation_id, user_id=value.user_id)
    return {"run": project_automation_run(run)}


def _history(service: AutomationService, value: AutomationHistoryInput) -> dict[str, object]:
    definition = service.read_definition(value.automation_id, user_id=value.user_id)
    return {
        "automation": project_automation_summary(definition, service.store.trigger_for(definition.automation_id), None),
        "runs": [project_automation_run(run) for run in service.store.list_runs(definition.automation_id, limit=value.limit)],
        "events": [project_automation_event(event) for event in service.store.history(definition.automation_id, limit=value.limit)],
    }


def _templates(service: AutomationService) -> dict[str, object]:
    return {"items": [_template_payload(item) for item in service.templates()]}


def _template(service: AutomationService, value: AutomationTemplateReadInput) -> dict[str, object]:
    item = next((candidate for candidate in service.templates() if candidate.template_id == value.template_id), None)
    if item is None:
        raise ActionFailure(ActionError("automation_template_not_found", "The Automation template was not found."))
    return {"template": _template_payload(item)}


def _template_payload(item) -> dict[str, object]:
    return {
        "templateId": item.template_id, "name": item.name, "description": item.description,
        "instructions": item.instructions, "outputRequirements": list(item.output_requirements),
        "recommendedSchedule": item.recommended_schedule, "requiredExtensions": list(item.required_extensions),
        "deliveryHint": item.delivery_hint, "behavior": item.behavior, "provenance": item.provenance, "version": item.version,
    }


def _permission_preview(service: AutomationService, value: AutomationIdentityInput) -> dict[str, object]:
    return {"preview": service.permission_preview(service.read_definition(value.automation_id, user_id=value.user_id))}


def _permission_revoke(service: AutomationService, context: ActionContext, value: AutomationPermissionRevokeInput) -> dict[str, object]:
    definition = service.read_definition(value.automation_id, user_id=value.user_id)
    if definition.revision != value.expected_revision:
        _raise_failure(AutomationConflictError("automation revision conflict"))
    return _detail(service, service.revoke_permissions(definition, actor_id=context.actor_id, correlation_id=context.correlation_id))


def _command(service: AutomationService, context: ActionContext, value: AutomationCommandInput) -> dict[str, object]:
    if value.operation == "list":
        return _list(service, AutomationListInput(userId=value.user_id))
    if value.operation == "create":
        text = value.text.strip()
        if not text:
            raise ActionFailure(ActionError("command_usage", "Usage: /automation create <instructions>"))
        create = AutomationCreateInput(
            userId=value.user_id,
            agentId=value.agent_id,
            name=text[:80],
            instructions=text,
            permissionsConfirmed=False,
        )
        return _create(service, context, create)
    definition = _resolve_target(service, value.user_id, value.target)
    if value.operation == "delete" and not context.confirmed:
        raise ActionFailure(
            ActionError(
                "confirmation_required",
                "Deleting an Automation requires explicit confirmation. Use the Desktop confirmation or CLI --yes.",
            )
        )
    if value.operation == "show":
        return _detail(service, definition)
    if value.operation == "run":
        return _run(service, context, AutomationRunInput(automationId=definition.automation_id, userId=value.user_id))
    if value.operation == "history":
        return _history(service, AutomationHistoryInput(automationId=definition.automation_id, userId=value.user_id))
    target = {"pause": "paused", "resume": "active", "delete": "deleted"}[value.operation]
    return _transition(
        service,
        context,
        AutomationTransitionInput(automationId=definition.automation_id, userId=value.user_id, expectedRevision=definition.revision),
        target,
    )


def _resolve_target(service: AutomationService, user_id: str, target: str) -> AutomationDefinition:
    normalized = target.strip()
    if not normalized:
        raise ActionFailure(ActionError("command_usage", "The Automation ID or name is required."))
    if normalized.startswith("auto_"):
        return service.read_definition(normalized, user_id=user_id)
    matches = [item for item in service.list_definitions(user_id=user_id, statuses=[], limit=200) if item.name.casefold() == normalized.casefold()]
    if len(matches) != 1:
        raise ActionFailure(ActionError("automation_target_ambiguous" if matches else "automation_not_found", "The Automation target could not be resolved uniquely."))
    return matches[0]


def _slash_input(_command, args: str, context: SlashInvocationContext) -> AutomationCommandInput:
    if context.agent_id is None:
        raise SlashCommandError("agent_context_required", "The /automation command requires an Agent context.")
    tokens = args.strip().split(maxsplit=1)
    if not tokens:
        return AutomationCommandInput(userId=context.user_id, agentId=context.agent_id, operation="list")
    operation = tokens[0].lower()
    remainder = tokens[1].strip() if len(tokens) > 1 else ""
    if operation not in {"list", "create", "show", "run", "pause", "resume", "history", "delete"}:
        raise SlashCommandError("command_usage", "Usage: /automation [list|create ...|show ID|run ID|pause ID|resume ID|history ID|delete ID]")
    return AutomationCommandInput(
        userId=context.user_id,
        agentId=context.agent_id,
        operation=operation,
        target="" if operation in {"list", "create"} else remainder,
        text=remainder if operation == "create" else "",
    )


def _camel_schedule(value: dict[str, object] | None) -> dict[str, object] | None:
    if value is None:
        return None
    return {
        "kind": value.get("kind"), "everySeconds": value.get("every_seconds"),
        "cronExpr": value.get("cron_expr"), "atMs": value.get("at_ms"), "timezone": value.get("timezone"),
    }


def _camel_local_event(value: dict[str, object] | None) -> dict[str, object] | None:
    """Project Python field names into the persisted trigger contract."""
    if value is None:
        return None
    return {
        "eventKey": value.get("event_key"),
        "inputSchema": value.get("input_schema") or {},
    }


def _raise_failure(exc: Exception) -> NoReturn:
    if isinstance(exc, AutomationNotFoundError):
        raise ActionFailure(ActionError("automation_not_found", "The Automation was not found.")) from exc
    if isinstance(exc, AutomationConflictError):
        raise ActionFailure(ActionError("revision_conflict", "The Automation changed; refresh and retry.")) from exc
    if isinstance(exc, AutomationStateError):
        raise ActionFailure(ActionError("automation_state_invalid", str(exc))) from exc
    if isinstance(exc, AutomationStoreError):
        raise ActionFailure(ActionError("automation_store_error", "The Automation could not be persisted.")) from exc
    raise ActionFailure(ActionError("automation_not_ready", "The Automation dependency is not ready.")) from exc


__all__ = ["register_automation_actions"]
