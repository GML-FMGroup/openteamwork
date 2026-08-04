"""Tests for deterministic Model Profile readiness and fallback selection."""

from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from pathlib import Path

import pytest

from openppx.config import AgentConfig, InMemorySecretStore, SecretRef, SecretValue
from openppx.modeling import (
    ModelCatalog,
    ModelProfile,
    ModelProfileRepository,
    ModelProfileSelector,
    ModelRequirements,
    ModelSelectionError,
)


def agent_config(
    *,
    default_profile: str | None = "primary",
    role_profiles: dict[str, str] | None = None,
) -> AgentConfig:
    """Build one Agent assignment fixture."""
    return AgentConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "low-main"},
            "spec": {
                "displayName": "Low Main",
                "workspace": "workspace/low-main",
                "ownerPrincipalId": "local:owner",
                "privilegeLevel": "low",
                "permissionOverrides": {},
                "modelPolicy": {
                    "defaultProfile": default_profile,
                    "roleProfiles": role_profiles or {},
                },
            },
        }
    )


def profile(
    name: str,
    *,
    provider: str = "openai",
    credential: str | None = "openai-primary",
    location: str = "remote",
    capabilities: list[str] | None = None,
    context_tokens: int | None = 128000,
    input_cost: str | None = "2.50",
    output_cost: str | None = "10.00",
    fallbacks: list[str] | None = None,
    enabled: bool = True,
) -> ModelProfile:
    """Build one Model Profile selection fixture."""
    credential_payload = None if credential is None else {"store": "system", "name": credential}
    return ModelProfile.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "ModelProfile",
            "metadata": {"name": name},
            "spec": {
                "displayName": name.replace("-", " ").title(),
                "provider": provider,
                "model": f"{provider}/model",
                "credential": credential_payload,
                "executionLocation": location,
                "capabilities": capabilities or ["text", "tool_calling"],
                "contextWindowTokens": context_tokens,
                "inputCostPerMillionUsd": input_cost,
                "outputCostPerMillionUsd": output_cost,
                "fallbackProfiles": fallbacks or [],
                "enabled": enabled,
            },
        }
    )


def selector(tmp_path: Path) -> tuple[ModelProfileRepository, InMemorySecretStore, ModelProfileSelector]:
    """Return one isolated selector fixture."""
    repository = ModelProfileRepository(tmp_path)
    secrets = InMemorySecretStore()
    return repository, secrets, ModelProfileSelector(repository, ModelCatalog(), secrets)


def write(repository: ModelProfileRepository, document: ModelProfile) -> None:
    repository.write_profile(document.metadata.name, document, expected_revision=None)


def add_secret(store: InMemorySecretStore, name: str = "openai-primary") -> None:
    store.put(SecretRef(store="system", name=name), SecretValue("not-visible"))


def test_selection_precedence_is_run_then_role_then_default(tmp_path: Path) -> None:
    repository, secrets, service = selector(tmp_path)
    for name in ("default", "role", "run"):
        write(repository, profile(name, credential=f"{name}-secret"))
        add_secret(secrets, f"{name}-secret")
    agent = agent_config(default_profile="default", role_profiles={"reasoning": "role"})

    assert service.select(agent, role="reasoning").profile_id == "role"
    assert service.select(agent, role=None).profile_id == "default"
    resolution = service.select(agent, role="reasoning", run_override="run")
    assert resolution.profile_id == "run"
    assert resolution.provider == "openai"
    assert resolution.model == "openai/model"
    assert resolution.secret_status is not None
    assert resolution.secret_status.state == "available"
    assert resolution.selection_source == "run_override"


def test_missing_role_assignment_falls_back_to_default_not_catalog_order(tmp_path: Path) -> None:
    repository, secrets, service = selector(tmp_path)
    write(repository, profile("default"))
    add_secret(secrets)

    resolution = service.select(agent_config(default_profile="default"), role="vision")

    assert resolution.profile_id == "default"
    assert resolution.selection_source == "agent_default"


def test_explicit_fallback_is_ordered_and_records_redacted_attempts(tmp_path: Path) -> None:
    repository, secrets, service = selector(tmp_path)
    write(repository, profile("primary", credential="missing-secret", fallbacks=["backup-a", "backup-b"]))
    write(repository, profile("backup-a", credential="backup-a-secret", enabled=False))
    write(repository, profile("backup-b", credential="backup-b-secret"))
    add_secret(secrets, "backup-b-secret")

    resolution = service.select(agent_config())

    assert resolution.profile_id == "backup-b"
    assert [(attempt.profile_id, attempt.reason) for attempt in resolution.attempts] == [
        ("primary", "secret_missing"),
        ("backup-a", "disabled"),
    ]
    assert "not-visible" not in str(resolution)


@pytest.mark.parametrize(
    ("candidate", "requirements", "reason"),
    [
        (profile("primary", capabilities=["text"]), ModelRequirements(required_capabilities={"vision"}), "capability_missing"),
        (profile("primary", location="remote"), ModelRequirements(privacy="local_only"), "privacy_mismatch"),
        (profile("primary", context_tokens=4096), ModelRequirements(min_context_tokens=8192), "context_insufficient"),
        (profile("primary", context_tokens=None), ModelRequirements(min_context_tokens=8192), "context_unknown"),
        (profile("primary", input_cost="5"), ModelRequirements(max_input_cost_per_million_usd=Decimal("2")), "cost_exceeded"),
        (profile("primary", input_cost=None), ModelRequirements(max_input_cost_per_million_usd=Decimal("2")), "cost_unknown"),
    ],
)
def test_constraints_fail_conservatively(
    tmp_path: Path,
    candidate: ModelProfile,
    requirements: ModelRequirements,
    reason: str,
) -> None:
    repository, secrets, service = selector(tmp_path)
    write(repository, candidate)
    add_secret(secrets)

    with pytest.raises(ModelSelectionError) as raised:
        service.select(agent_config(), requirements=requirements)

    assert raised.value.attempts[0].reason == reason


def test_unknown_provider_and_missing_profile_are_structured(tmp_path: Path) -> None:
    repository, secrets, service = selector(tmp_path)
    write(repository, profile("primary", provider="unknown-provider", credential=None))

    with pytest.raises(ModelSelectionError) as unknown:
        service.select(agent_config())
    assert unknown.value.attempts[0].reason == "provider_unknown"

    missing_service = ModelProfileSelector(ModelProfileRepository(tmp_path / "missing"), ModelCatalog(), secrets)
    with pytest.raises(ModelSelectionError) as missing:
        missing_service.select(agent_config())
    assert missing.value.attempts[0].reason == "profile_missing"


def test_required_provider_credential_must_have_a_reference(tmp_path: Path) -> None:
    repository, _, service = selector(tmp_path)
    write(repository, profile("primary", credential=None))

    with pytest.raises(ModelSelectionError) as raised:
        service.select(agent_config())

    assert raised.value.attempts[0].reason == "credential_missing"


def test_duplicate_fallback_candidate_is_reported_once(tmp_path: Path) -> None:
    repository, _, service = selector(tmp_path)
    write(repository, profile("primary", enabled=False, fallbacks=["branch-a", "branch-b"]))
    write(repository, profile("branch-a", enabled=False, fallbacks=["shared"]))
    write(repository, profile("branch-b", enabled=False, fallbacks=["shared"]))
    write(repository, profile("shared", enabled=False))

    with pytest.raises(ModelSelectionError) as raised:
        service.select(agent_config())

    assert any(attempt.reason == "fallback_duplicate" for attempt in raised.value.attempts)


def test_fallback_cycle_terminates_with_diagnostic(tmp_path: Path) -> None:
    repository, secrets, service = selector(tmp_path)
    write(repository, profile("primary", enabled=False, fallbacks=["backup"]))
    write(repository, profile("backup", enabled=False, fallbacks=["primary"]))

    with pytest.raises(ModelSelectionError) as raised:
        service.select(agent_config())

    assert any(attempt.reason == "fallback_cycle" for attempt in raised.value.attempts)


def test_no_default_profile_is_not_ready(tmp_path: Path) -> None:
    _, _, service = selector(tmp_path)

    with pytest.raises(ModelSelectionError) as raised:
        service.select(agent_config(default_profile=None))

    assert raised.value.attempts[0].reason == "profile_missing"
