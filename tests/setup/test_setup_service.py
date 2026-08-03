"""First-run setup service tests and shared setup candidate fixture."""

from __future__ import annotations

from pathlib import Path

import pytest

from openppx.config import ConfigService, FilesystemConfigRepository, InMemorySecretStore
from openppx.modeling import ModelCatalog, ModelProfileRepository, ModelProfileSelector
from openppx.setup import SetupApplyRequest, SetupError, SetupService


def setup_payload(tmp_path: Path, *, api_key: str | None = "secret-canary-value") -> dict[str, object]:
    profile: dict[str, object] = {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "ModelProfile",
        "metadata": {"name": "primary"},
        "spec": {
            "provider": "google",
            "model": "gemini-3-flash-preview",
            "credential": {"store": "system", "name": "google-primary"},
            "executionLocation": "remote",
            "capabilities": ["text", "tool_calling"],
        },
    }
    return {
        "node": {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "studio-node"},
            "spec": {
                "displayName": "Studio Node",
                "enabledAgents": ["main"],
                "clientApi": {
                    "listenHost": "127.0.0.1",
                    "port": 18765,
                    "authentication": "disabled",
                },
            },
        },
        "agent": {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "main"},
            "spec": {
                "displayName": "Main",
                "workspace": str(tmp_path / "workspace"),
                "ownerPrincipalId": "ppx-client-user",
                "privilegeLevel": "medium",
                "modelPolicy": {"defaultProfile": "primary"},
            },
        },
        "profile": profile,
        "secret": None
        if api_key is None
        else {
            "ref": {"store": "system", "name": "google-primary"},
            "value": api_key,
        },
        "expectedRevisions": {"node": None, "agent": None, "profile": None},
    }


def build_service(tmp_path: Path) -> tuple[SetupService, InMemorySecretStore]:
    repository = FilesystemConfigRepository(tmp_path)
    profiles = ModelProfileRepository(tmp_path)
    secrets = InMemorySecretStore()
    catalog = ModelCatalog()
    selector = ModelProfileSelector(profiles, catalog, secrets)
    config_service = ConfigService(repository, profiles, selector)
    return SetupService(repository, config_service, profiles, catalog, secrets), secrets


def test_setup_applies_complete_baseline_and_is_exactly_retryable(tmp_path: Path) -> None:
    service, secrets = build_service(tmp_path)
    request = SetupApplyRequest.model_validate(setup_payload(tmp_path))

    assert service.status()["state"] == "needs_configuration"
    first = service.apply(request)
    second = service.apply(request)

    assert first.node_revision == second.node_revision
    assert first.agent_revision == second.agent_revision
    assert first.profile_revision == second.profile_revision
    assert first.secret_state == "available"
    assert (tmp_path / "workspace").is_dir()
    status = service.status()
    assert status["state"] == "configured"
    service.mark_verified(session_id="session-verified")
    status = service.status()
    assert status["state"] == "ready"
    assert status["steps"] == {
        "node": "complete",
        "agent": "complete",
        "model": "complete",
        "credential": "available",
        "hello": "verified",
    }
    assert secrets.resolve(request.profile.spec.credential).reveal() == "secret-canary-value"  # type: ignore[arg-type]


def test_setup_rejects_missing_required_credential_without_publishing_node(tmp_path: Path) -> None:
    service, _secrets = build_service(tmp_path)
    request = SetupApplyRequest.model_validate(setup_payload(tmp_path, api_key=None))

    with pytest.raises(SetupError, match="credential is not available") as captured:
        service.apply(request)

    assert captured.value.code == "credential_unavailable"
    assert service.status()["state"] == "needs_configuration"
    assert not (tmp_path / "node.json").exists()


def test_setup_errors_and_results_never_retain_secret_value(tmp_path: Path) -> None:
    service, _secrets = build_service(tmp_path)
    request = SetupApplyRequest.model_validate(setup_payload(tmp_path))

    result = service.apply(request)

    assert "secret-canary-value" not in repr(request)
    assert "secret-canary-value" not in repr(result)
    assert "secret-canary-value" not in repr(service.status())


def test_setup_rejects_relative_workspace_before_writing_resources(tmp_path: Path) -> None:
    service, _secrets = build_service(tmp_path)
    payload = setup_payload(tmp_path)
    payload["agent"]["spec"]["workspace"] = "relative-workspace"  # type: ignore[index]
    request = SetupApplyRequest.model_validate(payload)

    with pytest.raises(SetupError, match="absolute path"):
        service.apply(request)

    assert not (tmp_path / "node.json").exists()


def test_oauth_provider_uses_external_login_instead_of_secret_ref(tmp_path: Path) -> None:
    service, _secrets = build_service(tmp_path)
    payload = setup_payload(tmp_path, api_key=None)
    payload["profile"]["spec"].update(  # type: ignore[index]
        {"provider": "openai_codex", "model": "openai-codex/gpt-5.1-codex", "credential": None}
    )
    request = SetupApplyRequest.model_validate(payload)

    result = service.apply(request)

    provider = next(item for item in service.status()["providers"] if item["id"] == "openai_codex")
    assert result.secret_state == "not_required"
    assert provider["credentialMode"] == "oauth"
    assert provider["credentialRequired"] is False
