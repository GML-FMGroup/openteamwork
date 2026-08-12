"""Product Agent lifecycle Actions."""

from __future__ import annotations

from typing import cast

from openppx.actions import ActionContext, ActionError, ActionFailure, ActionRegistry, ActionSpec, SlashCommandSpec
from openppx.agents import AgentLifecycleError, AgentLifecycleService
from openppx.config import ConfigError
from openppx.runtime.user_accounts import privilege_allows

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
            required_capabilities=frozenset({"agent.read"}),
            permission="agent.read",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(SlashCommandSpec(
                command="/agent",
                title="Show Agents",
                description="List configured Agents and their current availability.",
                icon="bot",
                order=70,
            ),),
        ),
        lambda context, _input: {"items": [_project(item) for item in _visible_agents(service, context)]},
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
            required_capabilities=frozenset({"agent.write"}),
            permission="agent.write",
            risk="medium",
            operation="mutation",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda context, input_data: _create(service, context, cast(AgentCreateInput, input_data)),
    )
    _register_mutations(registry, service)


def _create(
    service: AgentLifecycleService,
    context: ActionContext,
    input_data: AgentCreateInput,
) -> dict[str, object]:
    try:
        owner_principal_id = input_data.owner_principal_id
        workspace = input_data.workspace
        if context.principal_id is not None:
            if owner_principal_id not in {None, context.principal_id}:
                raise ActionFailure(
                    ActionError("identity_mismatch", "Agent ownership must match the authenticated user.")
                )
            _require_privilege_ceiling(context, input_data.privilege_level)
            owner_principal_id = context.principal_id
            if context.privilege_level != "root":
                if workspace is not None and workspace.strip():
                    raise ActionFailure(
                        ActionError(
                            "custom_workspace_requires_root",
                            "Custom Agent workspace paths require root privilege.",
                        )
                    )
                workspace = str(
                    service.repository.paths.node_root
                    / "users"
                    / context.principal_id
                    / "agents"
                    / input_data.agent_id
                    / "workspace"
                )
        if owner_principal_id is None:
            raise ActionFailure(ActionError("owner_required", "Agent ownership is required."))
        result = service.create(
            agent_id=input_data.agent_id,
            display_name=input_data.display_name,
            owner_principal_id=owner_principal_id,
            privilege_level=input_data.privilege_level,
            model_profile_id=input_data.model_profile_id,
            workspace=workspace,
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
            description="Update an Agent's name, instructions, and Model Profile for new Runs.",
            input_model=AgentUpdateInput,
            scope="agent",
            required_capabilities=frozenset({"agent.write"}),
            permission="agent.write",
            risk="medium",
            operation="mutation",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda context, input_data: _update(service, context, cast(AgentUpdateInput, input_data)),
    )
    registry.register(
        ActionSpec(
            action_id="agent.enable",
            namespace="agent",
            title="Enable or disable Agent",
            description="Publish or withdraw one configured Agent on this Node.",
            input_model=AgentEnableInput,
            scope="agent",
            required_capabilities=frozenset({"agent.write"}),
            permission="agent.write",
            risk="medium",
            operation="mutation",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda context, input_data: _set_enabled(service, context, cast(AgentEnableInput, input_data)),
    )
    registry.register(
        ActionSpec(
            action_id="agent.delete",
            namespace="agent",
            title="Remove Agent",
            description="Archive a disabled Agent config while retaining workspace and runtime data.",
            input_model=AgentDeleteInput,
            scope="agent",
            required_capabilities=frozenset({"agent.write"}),
            permission="agent.write",
            risk="high",
            confirmation="required",
            operation="mutation",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda context, input_data: _delete(service, context, cast(AgentDeleteInput, input_data)),
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


def _update(
    service: AgentLifecycleService,
    context: ActionContext,
    input_data: AgentUpdateInput,
) -> dict[str, object]:
    try:
        _require_agent_owner_or_root(service, context, input_data.agent_id)
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


def _set_enabled(
    service: AgentLifecycleService,
    context: ActionContext,
    input_data: AgentEnableInput,
) -> dict[str, object]:
    try:
        _require_agent_owner_or_root(service, context, input_data.agent_id)
        return _project(service.set_enabled(agent_id=input_data.agent_id, enabled=input_data.enabled))
    except AgentLifecycleError as exc:
        raise ActionFailure(ActionError(exc.code, str(exc))) from None
    except ConfigError as exc:
        raise_config_failure(exc)


def _delete(
    service: AgentLifecycleService,
    context: ActionContext,
    input_data: AgentDeleteInput,
) -> dict[str, object]:
    try:
        _require_agent_owner_or_root(service, context, input_data.agent_id)
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


def _visible_agents(service: AgentLifecycleService, context: ActionContext):
    """Return Agent resources visible to one authenticated user or trusted caller."""

    items = service.list()
    if context.principal_id is None or context.privilege_level == "root":
        return items
    return tuple(
        item
        for item in items
        if item.agent.document.spec.owner_principal_id == context.principal_id
    )


def _require_privilege_ceiling(context: ActionContext, agent_level: str) -> None:
    """Reject an Agent level above the authenticated user's fixed ceiling."""

    if context.principal_id is None:
        return
    if not privilege_allows(str(context.privilege_level or ""), agent_level):
        raise ActionFailure(
            ActionError(
                "agent_privilege_exceeds_user",
                "The Agent privilege level exceeds the authenticated user's level.",
            )
        )


def _require_agent_owner_or_root(
    service: AgentLifecycleService,
    context: ActionContext,
    agent_id: str,
) -> None:
    """Allow trusted callers, root, or the immutable configured Agent owner."""

    if context.principal_id is None or context.privilege_level == "root":
        return
    current = service.repository.read_agent(agent_id).document
    if current.spec.owner_principal_id != context.principal_id:
        raise ActionFailure(
            ActionError("agent_access_denied", "The authenticated user does not own this Agent.")
        )
