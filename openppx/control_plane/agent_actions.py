"""Product Agent lifecycle Actions."""

from __future__ import annotations

from typing import cast

from openppx.actions import ActionError, ActionFailure, ActionRegistry, ActionSpec
from openppx.agents import AgentLifecycleError, AgentLifecycleService
from openppx.config import ConfigError

from .errors import raise_config_failure
from .input_models import AgentCreateInput


def register_agent_actions(registry: ActionRegistry, service: AgentLifecycleService) -> None:
    """Register Node-owned Agent lifecycle operations."""
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


def _create(service: AgentLifecycleService, input_data: AgentCreateInput) -> dict[str, object]:
    try:
        result = service.create(
            agent_id=input_data.agent_id,
            display_name=input_data.display_name,
            owner_principal_id=input_data.owner_principal_id,
            privilege_level=input_data.privilege_level,
            model_profile_id=input_data.model_profile_id,
            workspace=input_data.workspace,
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
