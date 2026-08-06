"""System status and caller-aware Action catalog handlers."""

from __future__ import annotations

from typing import Callable, cast

from openppx.actions import ActionCatalogEntry, ActionContext, ActionRegistry, ActionSpec, SlashCommandSpec
from openppx.config import ConfigError, FilesystemConfigRepository

from .capabilities import CONTROL_PLANE_CAPABILITIES
from .input_models import EmptyInput, SlashCommandInvokeInput, SystemHelpInput
from .projections import project_diagnostics


def register_system_actions(
    registry: ActionRegistry,
    repository: FilesystemConfigRepository,
    *,
    product_version: str,
    catalog_provider: Callable[[ActionContext, str | None, str | None], tuple[ActionCatalogEntry, ...]],
    command_invoker: Callable[[ActionContext, SlashCommandInvokeInput], dict[str, object]],
) -> None:
    """Register system status and help after all domain Actions are available."""
    registry.register(
        ActionSpec(
            action_id="system.status",
            namespace="system",
            title="System status",
            description="Return Node configuration and capability readiness.",
            input_model=EmptyInput,
            scope="node",
            required_capabilities=frozenset({"system.read"}),
            permission="system.read",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/status",
                    title="Show status",
                    description="Display Node and Agent readiness.",
                    icon="activity",
                    order=40,
                ),
            ),
        ),
        lambda _context, _input: _status(repository, product_version=product_version),
        slash_input=lambda _command, _args, _context: {},
    )
    registry.register(
        ActionSpec(
            action_id="system.help",
            namespace="system",
            title="Action catalog",
            description="List caller-visible product Actions.",
            input_model=SystemHelpInput,
            scope="node",
            required_capabilities=frozenset({"system.read"}),
            permission="system.read",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/help",
                    title="Show commands",
                    description="List commands available to this client.",
                    icon="circle-help",
                    order=10,
                ),
            ),
        ),
        lambda context, input_data: {
            "items": [
                project_catalog_entry(item)
                for item in catalog_provider(
                    context,
                    cast(SystemHelpInput, input_data).namespace,
                    cast(SystemHelpInput, input_data).projection,
                )
            ]
        },
        slash_input=lambda _command, _args, _context: {"namespace": None, "projection": "slash"},
    )
    registry.register(
        ActionSpec(
            action_id="system.command.invoke",
            namespace="system",
            title="Invoke slash command",
            description="Resolve and invoke one Action-backed slash command.",
            input_model=SlashCommandInvokeInput,
            scope="session",
            required_capabilities=frozenset({"system.read"}),
            permission="system.read",
            operation="mutation",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda context, input_data: command_invoker(
            context,
            cast(SlashCommandInvokeInput, input_data),
        ),
    )


def _status(repository: FilesystemConfigRepository, *, product_version: str) -> dict[str, object]:
    diagnostics: list[dict[str, object]] = []
    node_diagnostics = repository.diagnose_node()
    if not node_diagnostics.ok:
        diagnostics.append(_diagnostic_summary(node_diagnostics))
        return {
            "state": "needs_configuration",
            "productVersion": product_version,
            "node": None,
            "agents": {"configured": 0, "enabled": 0, "ready": 0},
            "capabilities": list(CONTROL_PLANE_CAPABILITIES),
            "diagnostics": diagnostics,
        }

    node = repository.read_node()
    enabled_ids = tuple(node.document.spec.enabled_agents)
    ready = 0
    if not enabled_ids:
        diagnostics.append(
            {
                "code": "no_enabled_agents",
                "source": "node",
                "message": "At least one enabled Agent is required.",
            }
        )
    for agent_id in enabled_ids:
        item = repository.diagnose_agent(agent_id)
        if item.ok:
            ready += 1
        else:
            diagnostics.append(_diagnostic_summary(item))
    try:
        configured = len(repository.list_agent_ids())
    except ConfigError:
        configured = len(enabled_ids)
        diagnostics.append(
            {"code": "invalid_resource", "source": "agents", "message": "One or more Agent resources are invalid."}
        )
    state = "ready" if enabled_ids and ready == len(enabled_ids) and not diagnostics else "needs_configuration"
    return {
        "state": state,
        "productVersion": product_version,
        "node": {
            "id": node.document.metadata.name,
            "displayName": node.document.spec.display_name,
            "revision": node.revision,
        },
        "agents": {"configured": configured, "enabled": len(enabled_ids), "ready": ready},
        "capabilities": list(CONTROL_PLANE_CAPABILITIES),
        "diagnostics": diagnostics,
    }


def _diagnostic_summary(diagnostics) -> dict[str, object]:
    projected = project_diagnostics(diagnostics)
    first_issue = projected["issues"][0] if projected["issues"] else None
    return {
        "code": diagnostics.error_kind or "invalid_resource",
        "source": diagnostics.source,
        "message": first_issue["message"] if first_issue is not None else "The resource is not ready.",
    }


def project_catalog_entry(item: ActionCatalogEntry) -> dict[str, object]:
    """Project Action metadata and caller-computed availability."""
    spec = item.spec
    return {
        "actionId": spec.action_id,
        "namespace": spec.namespace,
        "title": spec.title,
        "description": spec.description,
        "scope": spec.scope,
        "inputSchema": spec.input_model.model_json_schema(by_alias=True),
        "requiredCapabilities": sorted(spec.required_capabilities),
        "permission": spec.permission,
        "risk": spec.risk,
        "confirmation": spec.confirmation,
        "execution": spec.execution,
        "operation": spec.operation,
        "previewActionId": spec.preview_action_id,
        "successPresentation": spec.success_presentation,
        "projections": list(spec.projections),
        "slashCommands": [
            {
                "command": command.command,
                "title": command.title,
                "description": command.description,
                "icon": command.icon,
                "argHint": command.arg_hint,
                "lifecycle": command.lifecycle,
                "acceptsArgs": command.accepts_args,
                "arguments": [
                    {
                        "name": argument.name,
                        "valueType": argument.value_type,
                        "description": argument.description,
                        "required": argument.required,
                        "choices": list(argument.choices),
                    }
                    for argument in command.arguments
                ],
                "noArgsBehavior": command.no_args_behavior,
                "usage": command.usage,
                "order": command.order,
            }
            for command in spec.slash_commands
        ],
        "available": item.available,
        "availabilityReason": item.availability_reason,
    }
