"""Model Profile catalog, readiness, and selection Actions."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import cast

from openppx.actions import ActionError, ActionFailure, ActionRegistry, ActionSpec
from openppx.config import ConfigError
from openppx.modeling import ModelProfileRepository, ModelProfileSelector, ModelRequirements, ModelSelectionError

from .errors import raise_config_failure, raise_model_failure
from .input_models import EmptyInput, ModelSelectionInput
from .projections import project_resolution


def register_model_actions(
    registry: ActionRegistry,
    profiles: ModelProfileRepository,
    selector: ModelProfileSelector,
    config_repository,
) -> None:
    """Register deterministic Model Profile query and selection Actions."""
    registry.register(
        _spec("model.list", "List Model Profiles", "List configured Model Profiles.", EmptyInput, "model.read"),
        lambda _context, _input: _list_profiles(profiles, selector),
    )
    registry.register(
        _spec("model.readiness", "Check model readiness", "Check Model selection without starting a Run.", ModelSelectionInput, "model.read"),
        lambda _context, input_data: _readiness(
            selector,
            config_repository,
            cast(ModelSelectionInput, input_data),
        ),
    )
    registry.register(
        _spec("model.select", "Select a model", "Resolve one ready Model Profile for a Run.", ModelSelectionInput, "model.use"),
        lambda _context, input_data: _select(
            selector,
            config_repository,
            cast(ModelSelectionInput, input_data),
        ),
    )


def _spec(action_id: str, title: str, description: str, input_model, permission: str) -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        namespace="model",
        title=title,
        description=description,
        input_model=input_model,
        scope="agent" if action_id != "model.list" else "node",
        required_capabilities=frozenset({permission}),
        permission=permission,
        projections=("cli", "slash", "desktop", "mobile"),
    )


def _list_profiles(profiles: ModelProfileRepository, selector: ModelProfileSelector) -> dict[str, object]:
    try:
        items = []
        for profile_id in profiles.list_profile_ids():
            resource = profiles.read_profile(profile_id)
            credential = resource.document.spec.credential
            credential_state = "not_required" if credential is None else selector.secrets.status(credential).state
            items.append(
                {
                    "id": profile_id,
                    "revision": resource.revision,
                    "provider": resource.document.spec.provider,
                    "model": resource.document.spec.model,
                    "enabled": resource.document.spec.enabled,
                    "credentialState": credential_state,
                }
            )
        return {"items": items}
    except ConfigError as exc:
        raise_config_failure(exc)


def _requirements(input_data: ModelSelectionInput) -> ModelRequirements:
    try:
        input_cost = Decimal(input_data.max_input_cost_per_million_usd) if input_data.max_input_cost_per_million_usd is not None else None
        output_cost = Decimal(input_data.max_output_cost_per_million_usd) if input_data.max_output_cost_per_million_usd is not None else None
    except InvalidOperation:
        raise ActionFailure(
            ActionError(
                "invalid_action_input",
                "The Action input does not match its schema.",
                details={"issues": [{"path": ["maxCost"], "code": "invalid_value", "message": "Cost must be decimal text."}]},
            )
        ) from None
    return ModelRequirements(
        required_capabilities=set(input_data.required_capabilities),
        privacy=input_data.privacy,
        min_context_tokens=input_data.min_context_tokens,
        max_input_cost_per_million_usd=input_cost,
        max_output_cost_per_million_usd=output_cost,
    )


def _resolve(selector: ModelProfileSelector, config_repository, input_data: ModelSelectionInput):
    try:
        agent = config_repository.read_agent(input_data.agent_id).document
        return selector.select(
            agent,
            role=input_data.role,
            run_override=input_data.run_override,
            requirements=_requirements(input_data),
        )
    except ConfigError as exc:
        raise_config_failure(exc)


def _readiness(selector: ModelProfileSelector, config_repository, input_data: ModelSelectionInput) -> dict[str, object]:
    try:
        resolution = _resolve(selector, config_repository, input_data)
    except ModelSelectionError as exc:
        return {
            "ready": False,
            "attempts": [
                {"profileId": attempt.profile_id, "reason": attempt.reason}
                for attempt in exc.attempts
            ],
        }
    return {"ready": True, "selection": project_resolution(resolution)}


def _select(selector: ModelProfileSelector, config_repository, input_data: ModelSelectionInput) -> dict[str, object]:
    try:
        return project_resolution(_resolve(selector, config_repository, input_data))
    except ModelSelectionError as exc:
        raise_model_failure(exc)
