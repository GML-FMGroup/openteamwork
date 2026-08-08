"""Tests for strict Model Profile resources and persistence."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

import pytest
from pydantic import ValidationError

from openppx.config import AgentConfig, ConfigLoadError, ConfigRevisionConflict, ConfigWriteError
from openppx.modeling import ModelProfile, ModelProfileRepository, export_model_profile_schema


def profile_document(*, name: str = "general-primary") -> dict[str, object]:
    """Return one valid ModelProfile payload."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "ModelProfile",
        "metadata": {"name": name},
        "spec": {
            "displayName": "General Primary",
            "provider": "openai",
            "model": "openai/gpt-5.4",
            "credential": {"store": "system", "name": "openai-primary"},
            "executionLocation": "remote",
            "apiBase": "https://models.example.test/v1/",
            "capabilities": ["text", "tool_calling", "structured_output"],
            "contextWindowTokens": 128000,
            "inputCostPerMillionUsd": "2.50",
            "outputCostPerMillionUsd": "10.00",
            "fallbackProfiles": ["general-backup"],
            "enabled": True,
        },
    }


def agent_document() -> dict[str, object]:
    """Return one Agent payload with model policy assignments."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "AgentConfig",
        "metadata": {"name": "low-main"},
        "spec": {
            "displayName": "Low Main",
            "workspace": "workspace/low-main",
            "ownerPrincipalId": "local:owner",
            "privilegeLevel": "low",
            "controls": {},
            "modelPolicy": {
                "defaultProfile": "general-primary",
                "roleProfiles": {"fast": "fast-primary", "vision": "vision-primary"},
            },
        },
    }


def test_model_profile_parses_strict_typed_fields() -> None:
    profile = ModelProfile.model_validate(profile_document())

    assert profile.metadata.name == "general-primary"
    assert profile.spec.provider == "openai"
    assert profile.spec.credential is not None
    assert profile.spec.api_base == "https://models.example.test/v1"
    assert str(profile.spec.input_cost_per_million_usd) == "2.50"
    assert profile.spec.capabilities == ["text", "tool_calling", "structured_output"]


def test_agent_model_policy_is_typed_and_role_keys_are_strict() -> None:
    agent = AgentConfig.model_validate(agent_document())
    assert agent.spec.model_policy.default_profile == "general-primary"
    assert agent.spec.model_policy.role_profiles["fast"] == "fast-primary"

    invalid = deepcopy(agent_document())
    invalid["spec"]["modelPolicy"]["roleProfiles"] = {"unknown": "general-primary"}  # type: ignore[index]
    with pytest.raises(ValidationError):
        AgentConfig.model_validate(invalid)


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("spec", "contextWindowTokens"), True),
        (("spec", "enabled"), 1),
        (("spec", "model"), "\n"),
        (("spec", "inputCostPerMillionUsd"), "-1"),
        (("spec", "capabilities"), ["text", "unregistered_capability"]),
        (("spec", "apiBase"), "https://user:secret@example.test/v1"),
        (("spec", "apiBase"), "file:///tmp/model"),
    ],
)
def test_model_profile_rejects_invalid_values(path: tuple[str, str], value: object) -> None:
    document = deepcopy(profile_document())
    document[path[0]][path[1]] = value  # type: ignore[index]

    with pytest.raises(ValidationError):
        ModelProfile.model_validate(document)


def test_model_profile_forbids_unknown_fields_and_duplicate_lists() -> None:
    unknown = deepcopy(profile_document())
    unknown["spec"]["apiKey"] = "secret"  # type: ignore[index]
    with pytest.raises(ValidationError):
        ModelProfile.model_validate(unknown)

    duplicate = deepcopy(profile_document())
    duplicate["spec"]["capabilities"] = ["text", "text"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="unique"):
        ModelProfile.model_validate(duplicate)


def test_model_profile_rejects_self_or_duplicate_fallbacks() -> None:
    self_reference = deepcopy(profile_document())
    self_reference["spec"]["fallbackProfiles"] = ["general-primary"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="itself"):
        ModelProfile.model_validate(self_reference)

    duplicate = deepcopy(profile_document())
    duplicate["spec"]["fallbackProfiles"] = ["backup", "backup"]  # type: ignore[index]
    with pytest.raises(ValidationError, match="unique"):
        ModelProfile.model_validate(duplicate)


def test_model_profile_schema_is_strict() -> None:
    schema = export_model_profile_schema()
    assert schema["additionalProperties"] is False
    assert schema["properties"]["kind"]["const"] == "ModelProfile"


def test_model_profile_repository_create_list_update_and_conflict(tmp_path: Path) -> None:
    repository = ModelProfileRepository(tmp_path)
    document = ModelProfile.model_validate(profile_document())
    created = repository.write_profile("general-primary", document, expected_revision=None)

    assert repository.list_profile_ids() == ("general-primary",)
    assert repository.read_profile("general-primary").revision == created.revision

    updated_payload = deepcopy(profile_document())
    updated_payload["spec"]["model"] = "openai/gpt-5.5"  # type: ignore[index]
    updated = repository.write_profile(
        "general-primary",
        ModelProfile.model_validate(updated_payload),
        expected_revision=created.revision,
    )
    assert updated.revision != created.revision

    with pytest.raises(ConfigRevisionConflict):
        repository.write_profile("general-primary", document, expected_revision=created.revision)


def test_model_profile_path_name_mismatch_never_writes(tmp_path: Path) -> None:
    repository = ModelProfileRepository(tmp_path)
    document = ModelProfile.model_validate(profile_document(name="other-profile"))

    with pytest.raises(ConfigLoadError) as raised:
        repository.write_profile("general-primary", document, expected_revision=None)

    assert raised.value.kind == "name_mismatch"
    assert not (tmp_path / "model-profiles" / "general-primary" / "profile.json").exists()


def test_repository_rejects_duplicate_display_names_across_distinct_ids(tmp_path: Path) -> None:
    repository = ModelProfileRepository(tmp_path)
    first = ModelProfile.model_validate(profile_document(name="first"))
    repository.write_profile("first", first, expected_revision=None)
    duplicate_payload = profile_document(name="second")
    duplicate_payload["spec"]["displayName"] = " general primary "  # type: ignore[index]

    with pytest.raises(ConfigWriteError) as raised:
        repository.write_profile(
            "second",
            ModelProfile.model_validate(duplicate_payload),
            expected_revision=None,
        )

    assert raised.value.kind == "name_conflict"
    assert not (tmp_path / "model-profiles" / "second" / "profile.json").exists()
