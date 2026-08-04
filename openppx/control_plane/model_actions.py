"""Model Profile catalog, readiness, and selection Actions."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import cast

from openppx.actions import ActionError, ActionFailure, ActionRegistry, ActionSpec, SlashCommandSpec
from openppx.config import ConfigError
from openppx.modeling import (
    ModelProfileRepository,
    ModelProfileLifecycleError,
    ModelProfileLifecycleService,
    ModelProfileSelector,
    ModelRequirements,
    ModelSelectionError,
    ProviderAccessError,
    ProviderAccessService,
)

from .errors import raise_config_failure, raise_model_failure
from .input_models import EmptyInput, ModelProfileCreateInput, ModelProfileMutationInput, ModelProfileReadInput, ModelProfileUpdateInput, ModelProfileWriteInput, ModelProviderInput, ModelSelectionInput
from .projections import project_resolution, project_resource


def register_model_actions(
    registry: ActionRegistry,
    profiles: ModelProfileRepository,
    lifecycle: ModelProfileLifecycleService,
    selector: ModelProfileSelector,
    config_repository,
    provider_access: ProviderAccessService,
) -> None:
    """Register deterministic Model Profile query and selection Actions."""
    registry.register(
        _spec("model.list", "List Model Profiles", "List configured Model Profiles.", EmptyInput, "model.read"),
        lambda _context, _input: _list_profiles(profiles, selector),
        slash_input=lambda _command, _args, _context: {},
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
    registry.register(
        _spec("model.profile.read", "Read Model Profile", "Read one strict Model Profile resource.", ModelProfileReadInput, "model.read"),
        lambda _context, input_data: _profile_call(
            lambda: project_resource(profiles.read_profile(cast(ModelProfileReadInput, input_data).profile_id))
        ),
    )
    registry.register(
        _spec("model.profile.apply", "Apply Model Profile", "Create or update one strict Model Profile.", ModelProfileMutationInput, "model.write"),
        lambda _context, input_data: _profile_call(
            lambda: _apply_profile(profiles, selector, cast(ModelProfileMutationInput, input_data))
        ),
    )
    registry.register(
        _spec("model.profile.create", "Create Model Profile", "Create one named Model Profile with a Node-generated identity.", ModelProfileCreateInput, "model.write"),
        lambda _context, input_data: _create_profile(
            lifecycle,
            cast(ModelProfileCreateInput, input_data),
        ),
    )
    registry.register(
        _spec("model.profile.update", "Update Model Profile", "Edit one Model Profile without changing its identity.", ModelProfileUpdateInput, "model.write"),
        lambda _context, input_data: _update_profile(
            lifecycle,
            cast(ModelProfileUpdateInput, input_data),
        ),
    )
    registry.register(
        _spec("model.catalog.list", "List provider models", "List models advertised by one provider on this Node.", ModelProviderInput, "model.read"),
        lambda _context, input_data: _provider_call(
            lambda: provider_access.list_models(cast(ModelProviderInput, input_data).provider_id)
        ),
    )
    registry.register(
        _spec("model.auth.status", "Provider authentication", "Inspect secret-free provider authentication state on this Node.", ModelProviderInput, "model.read"),
        lambda _context, input_data: _provider_call(
            lambda: provider_access.auth_status(cast(ModelProviderInput, input_data).provider_id)
        ),
    )
    registry.register(
        _spec("model.auth.begin", "Begin provider sign-in", "Start a Node-owned provider device-code login.", ModelProviderInput, "model.write"),
        lambda _context, input_data: _provider_call(
            lambda: provider_access.begin_auth(cast(ModelProviderInput, input_data).provider_id)
        ),
    )
    registry.register(
        _spec("model.auth.refresh", "Refresh provider authentication", "Adopt and verify the current provider login on this Node.", ModelProviderInput, "model.write"),
        lambda _context, input_data: _provider_call(
            lambda: provider_access.refresh_auth(cast(ModelProviderInput, input_data).provider_id)
        ),
    )


def _spec(action_id: str, title: str, description: str, input_model, permission: str) -> ActionSpec:
    slash_commands = ()
    projections = ("cli", "desktop", "mobile")
    if action_id == "model.list":
        projections = ("cli", "slash", "desktop", "mobile")
        slash_commands = (
            SlashCommandSpec(
                command="/model",
                title="Show models",
                description="List configured Model Profiles and credential readiness.",
                icon="brain",
                order=50,
            ),
        )
    return ActionSpec(
        action_id=action_id,
        namespace="model",
        title=title,
        description=description,
        input_model=input_model,
        scope="node" if action_id in {
            "model.list",
            "model.profile.read",
            "model.profile.apply",
            "model.profile.create",
            "model.profile.update",
            "model.catalog.list",
            "model.auth.status",
            "model.auth.begin",
            "model.auth.refresh",
        } else "agent",
        required_capabilities=frozenset({permission}),
        permission=permission,
        risk="medium" if action_id in {"model.profile.apply", "model.profile.create", "model.profile.update"} else "low",
        projections=projections,
        slash_commands=slash_commands,
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
                    "displayName": resource.document.spec.display_name,
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


def _apply_profile(
    profiles: ModelProfileRepository,
    selector: ModelProfileSelector,
    input_data: ModelProfileMutationInput,
) -> dict[str, object]:
    candidate = input_data.candidate
    provider = selector.catalog.get(candidate.spec.provider)
    if provider is None or provider.runtime == "unsupported":
        raise ActionFailure(ActionError("provider_not_supported", "The selected model provider is not supported."))
    if provider.credential_mode == "api_key" and candidate.spec.credential is None:
        raise ActionFailure(ActionError("credential_required", "The selected provider requires a protected credential."))
    if provider.credential_mode != "api_key" and candidate.spec.credential is not None:
        raise ActionFailure(ActionError("credential_not_supported", "This provider does not use a Model Profile credential."))
    if candidate.spec.api_base is not None and provider.runtime not in {"litellm", "codex"}:
        raise ActionFailure(ActionError("api_base_not_supported", "This provider does not support a custom API Base."))
    model_snapshot = selector.catalog.list_models(candidate.spec.provider)
    if model_snapshot.authoritative and candidate.spec.model not in {
        item.model_id for item in model_snapshot.models
    }:
        raise ActionFailure(ActionError("model_not_available", "The selected model is not advertised by this provider on the Node."))
    resource = profiles.write_profile(
        input_data.profile_id,
        candidate,
        expected_revision=input_data.expected_revision,
    )
    return project_resource(resource)


def _profile_write_kwargs(input_data: ModelProfileWriteInput) -> dict[str, object]:
    """Project validated product fields into the lifecycle call without identity data."""
    return {
        "display_name": input_data.display_name,
        "provider_id": input_data.provider_id,
        "model": input_data.model,
        "execution_location": input_data.execution_location,
        "api_base": input_data.api_base,
        "capabilities": input_data.capabilities,
        "context_window_tokens": input_data.context_window_tokens,
        "input_cost_per_million_usd": (
            Decimal(input_data.input_cost_per_million_usd)
            if input_data.input_cost_per_million_usd is not None
            else None
        ),
        "output_cost_per_million_usd": (
            Decimal(input_data.output_cost_per_million_usd)
            if input_data.output_cost_per_million_usd is not None
            else None
        ),
        "fallback_profile_ids": input_data.fallback_profile_ids,
        "enabled": input_data.enabled,
        "api_key": input_data.api_key,
    }


def _create_profile(
    lifecycle: ModelProfileLifecycleService,
    input_data: ModelProfileCreateInput,
) -> dict[str, object]:
    try:
        result = lifecycle.create(**_profile_write_kwargs(input_data))
    except ModelProfileLifecycleError as exc:
        raise ActionFailure(ActionError(exc.code, str(exc))) from None
    except ConfigError as exc:
        raise_config_failure(exc)
    return {
        **project_resource(result.profile),
        "credentialState": result.credential_state,
        "effect": "next_run",
    }


def _update_profile(
    lifecycle: ModelProfileLifecycleService,
    input_data: ModelProfileUpdateInput,
) -> dict[str, object]:
    try:
        result = lifecycle.update(
            profile_id=input_data.profile_id,
            expected_revision=input_data.expected_revision,
            **_profile_write_kwargs(input_data),
        )
    except ModelProfileLifecycleError as exc:
        raise ActionFailure(ActionError(exc.code, str(exc))) from None
    except ConfigError as exc:
        raise_config_failure(exc)
    return {
        **project_resource(result.profile),
        "credentialState": result.credential_state,
        "effect": "next_run",
    }


def _profile_call(operation):
    try:
        return operation()
    except ConfigError as exc:
        raise_config_failure(exc)


def _provider_call(operation):
    try:
        return operation()
    except ProviderAccessError as exc:
        raise ActionFailure(ActionError(exc.code, str(exc))) from None


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
