"""Product Agent lifecycle Actions."""

from __future__ import annotations

from typing import cast

from openppx.actions import ActionError, ActionFailure, ActionRegistry, ActionSpec, SlashCommandSpec
from openppx.agents import AgentLifecycleError, AgentLifecycleService
from openppx.config import ConfigError

from .errors import raise_config_failure
from .input_models import AgentCreateInput, AgentDeleteInput, AgentEnableInput, AgentUpdateInput, EmptyInput


def register_agent_actions(registry: ActionRegistry, service: AgentLifecycleService) -> None:
    """Register Node-owned Agent lifecycle operations."""
    registry.register(
        ActionSpec(
            action_id="agent.list",
            namespace="agent",
            title="List Agents",
            description="List configured Agents, including disabled resources.",
            input_model=EmptyInput,
            scope="node",
            required_capabilities=frozenset({"config.read"}),
            permission="config.read",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(SlashCommandSpec(
                command="/agent",
                title="Show Agents",
                description="List configured Agents and their current availability.",
                icon="bot",
                order=70,
            ),),
        ),
        lambda _context, _input: {"items": [_project(item) for item in service.list()]},
        slash_input=lambda _command, _args, _context: {},
    )
    registry.register(
        ActionSpec(
            action_id="agent.create",
            namespace="agent",
            title="Create Agent",
            description="Create and enable one Agent on this Node.",
            input_model=AgentCreateInput,
            scope="node",
            required_capabilities=frozenset({"config.write"}),
            permission="config.write",
            risk="medium",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, input_data: _create(service, cast(AgentCreateInput, input_data)),
    )
    _register_mutations(registry, service)


def _create(service: AgentLifecycleService, input_data: AgentCreateInput) -> dict[str, object]:
    try:
        result = service.create(
            agent_id=input_data.agent_id,
            display_name=input_data.display_name,
            owner_principal_id=input_data.owner_principal_id,
            privilege_level=input_data.privilege_level,
            model_profile_id=input_data.model_profile_id,
            workspace=input_data.workspace,
            instruction=input_data.instruction,
        )
    except AgentLifecycleError as exc:
        raise ActionFailure(ActionError(exc.code, str(exc))) from None
    except ConfigError as exc:
        raise_config_failure(exc)
    agent = result.agent.document
    return {
        "agent": {
            "id": agent.metadata.name,
            "name": agent.spec.display_name,
            "description": f"Workspace: {agent.spec.workspace}",
            "enabled": True,
            "status": "healthy",
            "workspace": agent.spec.workspace,
            "avatar": None,
            "tags": ["local", "openppx"],
            "revision": result.agent.revision,
        },
        "nodeRevision": result.node_revision,
        "effect": "next_run",
    }


def _register_mutations(registry: ActionRegistry, service: AgentLifecycleService) -> None:
    """Register update, enablement, and recoverable removal Actions."""
    registry.register(
        ActionSpec(
            action_id="agent.update",
            namespace="agent",
            title="Update Agent",
            description="Update Agent workspace and execution policy for new Runs.",
            input_model=AgentUpdateInput,
            scope="agent",
            required_capabilities=frozenset({"config.write"}),
            permission="config.write",
            risk="medium",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, input_data: _update(service, cast(AgentUpdateInput, input_data)),
    )
    registry.register(
        ActionSpec(
            action_id="agent.enable",
            namespace="agent",
            title="Enable or disable Agent",
            description="Publish or withdraw one configured Agent on this Node.",
            input_model=AgentEnableInput,
            scope="agent",
            required_capabilities=frozenset({"config.write"}),
            permission="config.write",
            risk="medium",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, input_data: _set_enabled(service, cast(AgentEnableInput, input_data)),
    )
    registry.register(
        ActionSpec(
            action_id="agent.delete",
            namespace="agent",
            title="Remove Agent",
            description="Archive a disabled Agent config while retaining workspace and runtime data.",
            input_model=AgentDeleteInput,
            scope="agent",
            required_capabilities=frozenset({"config.write"}),
            permission="config.write",
            risk="high",
            confirmation="required",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, input_data: _delete(service, cast(AgentDeleteInput, input_data)),
    )


def _project(result) -> dict[str, object]:
    """Project one lifecycle resource without exposing its owner principal."""
    agent = result.agent.document
    return {
        "id": agent.metadata.name,
        "name": agent.spec.display_name,
        "description": f"Workspace: {agent.spec.workspace}",
        "enabled": result.enabled,
        "status": "healthy" if result.enabled else "disabled",
        "workspace": agent.spec.workspace,
        "instruction": agent.spec.instruction,
        "privilegeLevel": agent.spec.privilege_level,
        "modelProfileId": agent.spec.model_policy.default_profile,
        "avatar": None,
        "tags": ["local", "openppx"],
        "revision": result.agent.revision,
        "nodeRevision": result.node_revision,
        "effect": result.effect,
    }


def _update(service: AgentLifecycleService, input_data: AgentUpdateInput) -> dict[str, object]:
    try:
        return _project(service.update(
            agent_id=input_data.agent_id,
            display_name=input_data.display_name,
            workspace=input_data.workspace,
            privilege_level=input_data.privilege_level,
            model_profile_id=input_data.model_profile_id,
            instruction=input_data.instruction,
            expected_revision=input_data.expected_revision,
        ))
    except AgentLifecycleError as exc:
        raise ActionFailure(ActionError(exc.code, str(exc))) from None
    except ConfigError as exc:
        raise_config_failure(exc)


def _set_enabled(service: AgentLifecycleService, input_data: AgentEnableInput) -> dict[str, object]:
    try:
        return _project(service.set_enabled(agent_id=input_data.agent_id, enabled=input_data.enabled))
    except AgentLifecycleError as exc:
        raise ActionFailure(ActionError(exc.code, str(exc))) from None
    except ConfigError as exc:
        raise_config_failure(exc)


def _delete(service: AgentLifecycleService, input_data: AgentDeleteInput) -> dict[str, object]:
    try:
        result = service.delete(agent_id=input_data.agent_id, expected_revision=input_data.expected_revision)
    except AgentLifecycleError as exc:
        raise ActionFailure(ActionError(exc.code, str(exc))) from None
    except ConfigError as exc:
        raise_config_failure(exc)
    return {
        "agentId": result.agent_id,
        "workspace": str(result.workspace),
        "workspaceRetained": True,
        "archivePath": str(result.archive_path),
        "nodeRevision": result.node_revision,
    }
