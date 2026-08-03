"""Node and Agent Config Actions over the strict Config Service."""

from __future__ import annotations

from typing import cast

from openppx.actions import ActionRegistry, ActionSpec
from openppx.config import ConfigError, ConfigService, FilesystemConfigRepository

from .errors import raise_config_failure
from .input_models import (
    AgentMutationInput,
    AgentReadInput,
    AgentValidateInput,
    EmptyInput,
    NodeMutationInput,
    NodeValidateInput,
)
from .projections import project_apply, project_preview, project_resource, project_validation


def register_config_actions(
    registry: ActionRegistry,
    repository: FilesystemConfigRepository,
    service: ConfigService,
) -> None:
    """Register the first revision-safe Node and Agent Config Actions."""
    def register(spec: ActionSpec, handler) -> None:
        registry.register(spec, handler)

    register(
        _spec("config.node.read", "Read Node config", "Read the strict Node Config resource.", EmptyInput, "config.read"),
        lambda _context, _input: _config_call(lambda: project_resource(repository.read_node())),
    )
    register(
        _spec("config.node.validate", "Validate Node config", "Validate a Node Config candidate.", NodeValidateInput, "config.write"),
        lambda _context, input_data: project_validation(service.validate_node(cast(NodeValidateInput, input_data).candidate)),
    )
    register(
        _spec("config.node.preview", "Preview Node config", "Preview a Node Config mutation.", NodeMutationInput, "config.write"),
        lambda _context, input_data: _config_call(
            lambda: project_preview(
                service.preview_node(
                    cast(NodeMutationInput, input_data).candidate,
                    expected_revision=cast(NodeMutationInput, input_data).expected_revision,
                )
            )
        ),
    )
    register(
        _spec("config.node.apply", "Apply Node config", "Atomically apply a Node Config mutation.", NodeMutationInput, "config.write"),
        lambda _context, input_data: _config_call(
            lambda: project_apply(
                service.apply_node(
                    cast(NodeMutationInput, input_data).candidate,
                    expected_revision=cast(NodeMutationInput, input_data).expected_revision,
                )
            )
        ),
    )
    register(
        _spec("config.agent.read", "Read Agent config", "Read one strict Agent Config resource.", AgentReadInput, "config.read", scope="agent"),
        lambda _context, input_data: _config_call(
            lambda: project_resource(repository.read_agent(cast(AgentReadInput, input_data).agent_id))
        ),
    )
    register(
        _spec("config.agent.list", "List Agents", "List enabled strict Agent Config resources.", EmptyInput, "config.read"),
        lambda _context, _input: _config_call(lambda: _list_enabled_agents(repository)),
    )
    register(
        _spec("config.agent.validate", "Validate Agent config", "Validate an Agent Config candidate.", AgentValidateInput, "config.write", scope="agent"),
        lambda _context, input_data: project_validation(
            service.validate_agent(
                cast(AgentValidateInput, input_data).candidate,
                agent_id=cast(AgentValidateInput, input_data).agent_id,
            )
        ),
    )
    register(
        _spec("config.agent.preview", "Preview Agent config", "Preview an Agent Config mutation.", AgentMutationInput, "config.write", scope="agent"),
        lambda _context, input_data: _config_call(
            lambda: project_preview(
                service.preview_agent(
                    cast(AgentMutationInput, input_data).agent_id,
                    cast(AgentMutationInput, input_data).candidate,
                    expected_revision=cast(AgentMutationInput, input_data).expected_revision,
                )
            )
        ),
    )
    register(
        _spec("config.agent.apply", "Apply Agent config", "Atomically apply an Agent Config mutation.", AgentMutationInput, "config.write", scope="agent"),
        lambda _context, input_data: _config_call(
            lambda: project_apply(
                service.apply_agent(
                    cast(AgentMutationInput, input_data).agent_id,
                    cast(AgentMutationInput, input_data).candidate,
                    expected_revision=cast(AgentMutationInput, input_data).expected_revision,
                )
            )
        ),
    )


def _spec(
    action_id: str,
    title: str,
    description: str,
    input_model,
    permission: str,
    *,
    scope: str = "node",
) -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        namespace="config",
        title=title,
        description=description,
        input_model=input_model,
        scope=scope,
        required_capabilities=frozenset({permission}),
        permission=permission,
        projections=("cli", "desktop", "mobile"),
    )


def _config_call(operation):
    try:
        return operation()
    except ConfigError as exc:
        raise_config_failure(exc)


def _list_enabled_agents(repository: FilesystemConfigRepository) -> dict[str, object]:
    """Project Node-enabled Agents from strict resources without direct JSON parsing."""
    node = repository.read_node()
    items: list[dict[str, object]] = []
    for agent_id in node.document.spec.enabled_agents:
        resource = repository.read_agent(agent_id)
        agent = resource.document
        items.append(
            {
                "id": agent.metadata.name,
                "name": agent.spec.display_name,
                "description": f"Workspace: {agent.spec.workspace}",
                "enabled": True,
                "status": "healthy",
                "workspace": agent.spec.workspace,
                "avatar": None,
                "tags": ["local", "openppx"],
                "revision": resource.revision,
            }
        )
    return {"items": items}
