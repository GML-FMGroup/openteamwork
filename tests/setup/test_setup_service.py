"""First-run setup service tests and shared setup candidate fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openppx.config import ConfigService, FilesystemConfigRepository, InMemorySecretStore
from openppx.modeling import ModelCatalog, ModelProfileRepository, ModelProfileSelector
from openppx.permissions import authorize_command
from openppx.setup import SetupApplyRequest, SetupError, SetupService


def setup_payload(tmp_path: Path, *, api_key: str | None = "secret-canary-value") -> dict[str, object]:
    profile: dict[str, object] = {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "ModelProfile",
        "metadata": {"name": "primary"},
        "spec": {
            "displayName": "Primary",
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
    catalog = ModelCatalog(codex_home=tmp_path / "codex-home")
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
    assert service.repository.read_node().document.spec.permissions.high_protected_write_roots == (
        str(tmp_path),
    )
    assert service.profiles.read_profile("primary").document.spec.context_window_tokens == 1_048_576
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


def test_clean_setup_publishes_an_executable_high_agent_policy(tmp_path: Path) -> None:
    """A fresh high Agent must not inherit the former empty-root blocking Gate."""

    service, _secrets = build_service(tmp_path)
    payload = setup_payload(tmp_path)
    payload["agent"]["spec"]["privilegeLevel"] = "high"  # type: ignore[index]

    service.apply(SetupApplyRequest.model_validate(payload))
    snapshot = service.config_service.snapshot("main")

    assert snapshot.permissions.preset == "high"
    assert snapshot.permissions.blocking_gates == ()
    assert snapshot.node.spec.permissions.high_protected_write_roots == (str(tmp_path),)
    command = authorize_command(
        snapshot.permissions,
        workspace_root=Path(snapshot.agent.spec.workspace),
        argv=["python", "create_document.py"],
        cwd=Path(snapshot.agent.spec.workspace),
        shell=False,
        background=False,
        pty=False,
        timeout_seconds=60,
    )
    assert command.execution_profile == "high-protected-sandbox"
    assert command.required_backend == "docker"


def test_setup_verification_ignores_display_names_but_not_execution_changes(tmp_path: Path) -> None:
    service, _secrets = build_service(tmp_path)
    request = SetupApplyRequest.model_validate(setup_payload(tmp_path))
    service.apply(request)
    service.mark_verified(session_id="session-verified")

    verified = service.state_repository.read()
    assert verified.execution_fingerprint is not None

    current = service.repository.read_agent("main")
    renamed_spec = current.document.spec.model_copy(update={"display_name": "Monica"})
    renamed = current.document.model_copy(update={"spec": renamed_spec})
    renamed_resource = service.config_service.apply_agent(
        "main",
        renamed,
        expected_revision=current.revision,
    ).resource

    assert renamed_resource.document.metadata.name == "main"
    assert service.status()["state"] == "ready"
    assert service.status()["steps"]["hello"] == "verified"  # type: ignore[index]

    changed_spec = renamed_resource.document.spec.model_copy(
        update={"instruction": "Use the saved workspace instructions."}
    )
    changed = renamed_resource.document.model_copy(update={"spec": changed_spec})
    service.config_service.apply_agent(
        "main",
        changed,
        expected_revision=renamed_resource.revision,
    )

    assert service.status()["state"] == "configured"
    assert service.status()["steps"]["hello"] == "stale"  # type: ignore[index]


def test_legacy_verification_remains_strict_until_one_new_hello(tmp_path: Path) -> None:
    service, _secrets = build_service(tmp_path)
    result = service.apply(SetupApplyRequest.model_validate(setup_payload(tmp_path)))
    service.state_repository.mark_verified(
        node_revision=result.node_revision,
        agent_revision=result.agent_revision,
        profile_revision=result.profile_revision,
        session_id="legacy-session",
    )

    current = service.repository.read_agent("main")
    renamed_spec = current.document.spec.model_copy(update={"display_name": "Monica"})
    renamed = current.document.model_copy(update={"spec": renamed_spec})
    service.config_service.apply_agent(
        "main",
        renamed,
        expected_revision=current.revision,
    )

    status = service.status()
    assert status["state"] == "configured"
    assert status["steps"]["hello"] == "stale"  # type: ignore[index]


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


def test_setup_status_reports_invalid_profile_without_raising(tmp_path: Path) -> None:
    service, _secrets = build_service(tmp_path)
    service.apply(SetupApplyRequest.model_validate(setup_payload(tmp_path)))
    profile_path = tmp_path / "model-profiles" / "primary" / "profile.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    del payload["spec"]["displayName"]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    status = service.status()

    assert status["state"] == "needs_configuration"
    assert status["steps"] == {
        "node": "complete",
        "agent": "complete",
        "model": "invalid",
        "credential": "not_required",
        "hello": "not_started",
    }
    assert isinstance(status["revisions"]["node"], str)
    assert isinstance(status["revisions"]["agent"], str)
    assert status["revisions"]["profile"] is None
    assert status["diagnostic"] == {
        "component": "model",
        "errorKind": "invalid_schema",
        "issues": [
            {
                "code": "invalid_value",
                "path": ["spec", "displayName"],
                "message": "Setting has an invalid value.",
                "source": "model-profile:primary",
            }
        ],
    }
    assert str(tmp_path) not in repr(status["diagnostic"])


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
        {"provider": "openai_codex", "model": "openai-codex/gpt-5.5", "credential": None}
    )
    request = SetupApplyRequest.model_validate(payload)

    result = service.apply(request)

    provider = next(item for item in service.status()["providers"] if item["id"] == "openai_codex")
    assert result.secret_state == "not_required"
    assert provider["credentialMode"] == "oauth"
    assert provider["credentialRequired"] is False
