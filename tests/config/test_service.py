"""Tests for Config validation, preview, apply, effects, and snapshots."""

from __future__ import annotations

import os
from copy import deepcopy
from pathlib import Path

import pytest

from openppx.config import (
    AgentConfig,
    ConfigChange,
    ConfigEffect,
    ConfigImmutableFieldError,
    ConfigLoadError,
    ConfigRevisionConflict,
    ConfigService,
    ConfigWriteError,
    FilesystemConfigRepository,
    InMemorySecretStore,
    NodeConfig,
    SecretRef,
    SecretValue,
)
from openppx.modeling import ModelCatalog, ModelProfile, ModelProfileRepository, ModelProfileSelector


def node_payload(*, display_name: str = "Local Node") -> dict[str, object]:
    """Return a Node service fixture."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "NodeConfig",
        "metadata": {"name": "local-node"},
        "spec": {
            "displayName": display_name,
            "enabledAgents": ["low-main"],
            "clientApi": {
                "listenHost": "127.0.0.1",
                "port": 18765,
                "authentication": "required",
            },
        },
    }


def agent_payload(*, display_name: str = "Low Main", workspace: str = "workspace/low-main") -> dict[str, object]:
    """Return an Agent service fixture."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "AgentConfig",
        "metadata": {"name": "low-main"},
        "spec": {
            "displayName": display_name,
            "workspace": workspace,
            "ownerPrincipalId": "local:owner",
            "privilegeLevel": "low",
            "controls": {},
            "modelPolicy": {"defaultProfile": "primary", "roleProfiles": {}},
        },
    }


def model_profile() -> ModelProfile:
    """Return one ready Model Profile fixture."""
    return ModelProfile.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "ModelProfile",
            "metadata": {"name": "primary"},
            "spec": {
                "displayName": "Primary",
                "provider": "openai",
                "model": "openai/gpt-5.4",
                "credential": {"store": "system", "name": "openai-primary"},
                "executionLocation": "remote",
                "capabilities": ["text", "tool_calling"],
                "contextWindowTokens": 128000,
                "inputCostPerMillionUsd": "2.5",
                "outputCostPerMillionUsd": "10",
                "fallbackProfiles": [],
                "enabled": True,
            },
        }
    )


def service(tmp_path: Path) -> tuple[FilesystemConfigRepository, ModelProfileRepository, ConfigService]:
    """Return an isolated Config Service fixture."""
    config_repository = FilesystemConfigRepository(tmp_path)
    profile_repository = ModelProfileRepository(tmp_path)
    secrets = InMemorySecretStore()
    secrets.put(SecretRef(store="system", name="openai-primary"), SecretValue("hidden-secret"))
    selector = ModelProfileSelector(profile_repository, ModelCatalog(), secrets)
    return (
        config_repository,
        profile_repository,
        ConfigService(config_repository, profile_repository, selector),
    )


def test_validate_returns_redacted_diagnostics_without_writing(tmp_path: Path) -> None:
    _, _, config_service = service(tmp_path)
    payload = node_payload()
    payload["spec"]["secretValue"] = "sk-do-not-show"  # type: ignore[index]

    result = config_service.validate_node(payload)

    assert result.ok is False
    assert result.diagnostics.error_kind == "invalid_schema"
    assert "sk-do-not-show" not in str(result)
    assert not (tmp_path / "node.json").exists()


def test_agent_validation_and_preview_enforce_path_identity(tmp_path: Path) -> None:
    _, _, config_service = service(tmp_path)
    payload = agent_payload()
    payload["metadata"] = {"name": "other-agent"}

    validation = config_service.validate_agent(payload, agent_id="low-main")
    assert validation.ok is False
    assert validation.diagnostics.error_kind == "name_mismatch"

    with pytest.raises(ConfigLoadError) as raised:
        config_service.preview_agent(
            "low-main",
            AgentConfig.model_validate(payload),
            expected_revision=None,
        )
    assert raised.value.kind == "name_mismatch"
    assert not (tmp_path / "agents" / "low-main" / "agent.json").exists()


def test_preview_create_is_pure_and_classifies_restart_effect(tmp_path: Path) -> None:
    _, _, config_service = service(tmp_path)
    candidate = NodeConfig.model_validate(node_payload())
    before = dict(os.environ)

    preview = config_service.preview_node(candidate, expected_revision=None)

    assert preview.base_revision is None
    assert preview.candidate_revision.startswith("sha256:")
    assert preview.effect == ConfigEffect.RESTART_REQUIRED
    assert not (tmp_path / "node.json").exists()
    assert dict(os.environ) == before


def test_apply_and_followup_preview_share_diff_and_effect_semantics(tmp_path: Path) -> None:
    _, _, config_service = service(tmp_path)
    created = config_service.apply_node(NodeConfig.model_validate(node_payload()), expected_revision=None)
    updated = NodeConfig.model_validate(node_payload(display_name="Renamed Node"))

    preview = config_service.preview_node(updated, expected_revision=created.resource.revision)
    applied = config_service.apply_node(updated, expected_revision=created.resource.revision)

    assert preview.changes == applied.changes
    assert preview.effect == ConfigEffect.LIVE
    assert applied.effect == ConfigEffect.LIVE
    assert applied.resource.document.spec.display_name == "Renamed Node"


def test_preview_diff_never_contains_changed_free_text_values(tmp_path: Path) -> None:
    _, _, config_service = service(tmp_path)
    created = config_service.apply_node(NodeConfig.model_validate(node_payload()), expected_revision=None)
    secretish = "private-machine-name"
    candidate = NodeConfig.model_validate(node_payload(display_name=secretish))

    preview = config_service.preview_node(candidate, expected_revision=created.resource.revision)

    assert preview.changes[0].path == ("spec", "displayName")
    assert secretish not in str(preview)


def test_node_context_compaction_change_takes_effect_on_next_run(tmp_path: Path) -> None:
    _, _, config_service = service(tmp_path)
    created = config_service.apply_node(NodeConfig.model_validate(node_payload()), expected_revision=None)
    candidate_payload = deepcopy(node_payload())
    candidate_payload["spec"]["runtime"] = {  # type: ignore[index]
        "contextCompaction": {"enabled": True, "thresholdPercent": 75}
    }

    preview = config_service.preview_node(
        NodeConfig.model_validate(candidate_payload),
        expected_revision=created.resource.revision,
    )

    assert preview.effect == ConfigEffect.NEXT_RUN
    assert preview.changes == (
        ConfigChange(("spec", "runtime", "contextCompaction", "thresholdPercent"), "changed"),
    )


def test_existing_agent_authority_is_immutable_but_metadata_and_model_remain_editable(
    tmp_path: Path,
) -> None:
    _, _, config_service = service(tmp_path)
    created = config_service.apply_agent(
        "low-main",
        AgentConfig.model_validate(agent_payload()),
        expected_revision=None,
    )
    display_payload = agent_payload(display_name="Renamed Agent")
    display_payload["spec"]["instruction"] = "Use concise answers."  # type: ignore[index]
    display = AgentConfig.model_validate(display_payload)
    model_payload = deepcopy(display_payload)
    model_payload["spec"]["modelPolicy"] = {  # type: ignore[index]
        "defaultProfile": "primary",
        "roleProfiles": {"fast": "primary"},
    }
    workspace = AgentConfig.model_validate(agent_payload(workspace="workspace/another"))
    owner_payload = deepcopy(agent_payload())
    owner_payload["spec"]["ownerPrincipalId"] = "local:other"  # type: ignore[index]
    privilege_payload = deepcopy(agent_payload())
    privilege_payload["spec"]["privilegeLevel"] = "medium"  # type: ignore[index]
    permissions_payload = deepcopy(agent_payload())
    permissions_payload["spec"]["permissions"] = {"objectDefaults": {"workspace": "deny"}}  # type: ignore[index]
    controls_payload = deepcopy(agent_payload())
    controls_payload["spec"]["controls"] = {"secretAccess": "none"}  # type: ignore[index]

    assert config_service.preview_agent(
        "low-main", display, expected_revision=created.resource.revision
    ).effect == ConfigEffect.LIVE
    assert config_service.preview_agent(
        "low-main",
        AgentConfig.model_validate(model_payload),
        expected_revision=created.resource.revision,
    ).effect == ConfigEffect.NEXT_RUN
    for candidate in (
        workspace,
        AgentConfig.model_validate(owner_payload),
        AgentConfig.model_validate(privilege_payload),
        AgentConfig.model_validate(permissions_payload),
        AgentConfig.model_validate(controls_payload),
    ):
        with pytest.raises(ConfigImmutableFieldError, match="authority"):
            config_service.preview_agent(
                "low-main", candidate, expected_revision=created.resource.revision
            )


def test_direct_agent_config_apply_cannot_bypass_authority_immutability(tmp_path: Path) -> None:
    _, _, config_service = service(tmp_path)
    config_service.apply_node(NodeConfig.model_validate(node_payload()), expected_revision=None)
    created = config_service.apply_agent(
        "low-main",
        AgentConfig.model_validate(agent_payload()),
        expected_revision=None,
    )
    candidate_payload = deepcopy(agent_payload())
    candidate_payload["spec"]["permissions"] = {  # type: ignore[index]
        "objectDefaults": {"workspace": "deny"}
    }

    with pytest.raises(ConfigImmutableFieldError) as captured:
        config_service.apply_agent(
            "low-main",
            AgentConfig.model_validate(candidate_payload),
            expected_revision=created.resource.revision,
        )

    assert captured.value.field_paths == (("spec", "permissions"),)
    assert "workspace/low-main" not in str(captured.value)


def test_current_permission_snapshot_does_not_depend_on_model_resolution(tmp_path: Path) -> None:
    _, _, config_service = service(tmp_path)
    config_service.apply_node(NodeConfig.model_validate(node_payload()), expected_revision=None)
    config_service.apply_agent(
        "low-main",
        AgentConfig.model_validate(agent_payload()),
        expected_revision=None,
    )

    snapshot = config_service.permission_snapshot("low-main")

    assert snapshot.agent_id == "low-main"
    assert snapshot.preset == "low"


def test_apply_rejects_stale_revision_without_changing_resource(tmp_path: Path) -> None:
    repository, _, config_service = service(tmp_path)
    created = config_service.apply_node(NodeConfig.model_validate(node_payload()), expected_revision=None)

    with pytest.raises(ConfigRevisionConflict):
        config_service.apply_node(
            NodeConfig.model_validate(node_payload(display_name="Stale")),
            expected_revision="sha256:" + "0" * 64,
        )

    assert repository.read_node().revision == created.resource.revision


def test_apply_write_failure_preserves_current_resource(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository, _, config_service = service(tmp_path)
    created = config_service.apply_node(NodeConfig.model_validate(node_payload()), expected_revision=None)

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        del source, target
        raise OSError("simulated apply failure")

    monkeypatch.setattr("openppx.config.atomic.os.replace", fail_replace)
    with pytest.raises(ConfigWriteError):
        config_service.apply_node(
            NodeConfig.model_validate(node_payload(display_name="Not Applied")),
            expected_revision=created.resource.revision,
        )

    current = repository.read_node()
    assert current.revision == created.resource.revision
    assert current.document.spec.display_name == "Local Node"


def test_snapshot_is_deterministic_and_changes_with_agent_revision(tmp_path: Path) -> None:
    _, profiles, config_service = service(tmp_path)
    config_service.apply_node(NodeConfig.model_validate(node_payload()), expected_revision=None)
    agent_result = config_service.apply_agent(
        "low-main",
        AgentConfig.model_validate(agent_payload()),
        expected_revision=None,
    )
    profiles.write_profile("primary", model_profile(), expected_revision=None)

    first = config_service.snapshot("low-main")
    second = config_service.snapshot("low-main")
    assert first.revision == second.revision
    assert first.permissions.revision == second.permissions.revision
    assert first.permissions.rollout_for("workspace") == "enforce"
    assert first.permissions.agent_id == "low-main"
    assert first.model.profile_id == "primary"
    assert {origin.resource_id for origin in first.origins} == {
        "node/local-node",
        "agent/low-main",
        "model-profile/primary",
    }

    updated_payload = deepcopy(agent_payload())
    updated_payload["spec"]["displayName"] = "Updated"  # type: ignore[index]
    config_service.apply_agent(
        "low-main",
        AgentConfig.model_validate(updated_payload),
        expected_revision=agent_result.resource.revision,
    )
    updated = config_service.snapshot("low-main")
    assert updated.revision != first.revision
    assert updated.permissions.revision == first.permissions.revision


def test_snapshot_permission_revision_tracks_other_agent_workspace_boundaries(tmp_path: Path) -> None:
    _, profiles, config_service = service(tmp_path)
    config_service.apply_node(NodeConfig.model_validate(node_payload()), expected_revision=None)
    config_service.apply_agent(
        "low-main",
        AgentConfig.model_validate(agent_payload()),
        expected_revision=None,
    )
    high_payload = deepcopy(agent_payload(workspace="workspace/high-operator"))
    high_payload["metadata"] = {"name": "high-operator"}
    high_payload["spec"]["displayName"] = "High Operator"  # type: ignore[index]
    high_payload["spec"]["privilegeLevel"] = "high"  # type: ignore[index]
    config_service.apply_agent(
        "high-operator",
        AgentConfig.model_validate(high_payload),
        expected_revision=None,
    )
    profiles.write_profile("primary", model_profile(), expected_revision=None)
    before = config_service.snapshot("low-main")

    high_payload["metadata"] = {"name": "high-operator-2"}
    high_payload["spec"]["displayName"] = "High Operator 2"  # type: ignore[index]
    high_payload["spec"]["workspace"] = "workspace/high-operator-2"  # type: ignore[index]
    config_service.apply_agent(
        "high-operator-2",
        AgentConfig.model_validate(high_payload),
        expected_revision=None,
    )
    after = config_service.snapshot("low-main")

    assert before.permissions.revision != after.permissions.revision
    assert before.revision != after.revision
