"""Control Plane system, Config, and Model vertical-slice tests."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from openppx.actions import ActionContext
from openppx.config import InMemorySecretStore, SecretRef, SecretValue
from openppx.control_plane import build_control_plane
from openppx.modeling import ModelProfile


def node_payload(*, display_name: str = "Test Node") -> dict[str, object]:
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "NodeConfig",
        "metadata": {"name": "test-node"},
        "spec": {
            "displayName": display_name,
            "enabledAgents": ["low-main"],
            "clientApi": {"listenHost": "127.0.0.1", "port": 18765, "authentication": "required"},
        },
    }


def agent_payload() -> dict[str, object]:
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "AgentConfig",
        "metadata": {"name": "low-main"},
        "spec": {
            "displayName": "Low Main",
            "workspace": "workspace/low-main",
            "ownerPrincipalId": "local:owner",
            "privilegeLevel": "low",
            "permissionOverrides": {},
            "modelPolicy": {"defaultProfile": "primary", "roleProfiles": {}},
        },
    }


def profile_payload() -> dict[str, object]:
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "ModelProfile",
        "metadata": {"name": "primary"},
        "spec": {
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


def context(*, write: bool = True) -> ActionContext:
    capabilities = frozenset({"system.read", "config.read", "config.write", "model.read", "model.use"})
    permissions = capabilities if write else frozenset({"system.read", "config.read", "model.read", "model.use"})
    return ActionContext(
        request_id="req_control_plane",
        correlation_id="corr_control_plane",
        actor_id="local:test",
        capabilities=capabilities,
        permissions=permissions,
    )


def configured_application(tmp_path: Path):
    secrets = InMemorySecretStore()
    secrets.put(SecretRef(store="system", name="openai-primary"), SecretValue("never-visible"))
    application = build_control_plane(tmp_path, secret_store=secrets, product_version="test")
    node = application.invoke("config.node.apply", {"candidate": node_payload(), "expectedRevision": None}, context())
    agent = application.invoke(
        "config.agent.apply",
        {"agentId": "low-main", "candidate": agent_payload(), "expectedRevision": None},
        context(),
    )
    application.profile_repository.write_profile(
        "primary",
        ModelProfile.model_validate(profile_payload()),
        expected_revision=None,
    )
    assert node.ok and agent.ok
    return application


def test_system_status_is_transport_independent_and_reports_readiness(tmp_path: Path) -> None:
    application = configured_application(tmp_path)

    result = application.status(context())

    assert result.ok is True
    assert result.data["state"] == "ready"
    assert result.data["node"]["displayName"] == "Test Node"
    assert result.data["agents"] == {"configured": 1, "enabled": 1, "ready": 1}
    assert "actions.invoke" in result.data["capabilities"]
    assert "http" not in application.__class__.__module__


def test_system_status_reports_missing_config_without_path_leak(tmp_path: Path) -> None:
    application = build_control_plane(tmp_path, secret_store=InMemorySecretStore(), product_version="test")

    result = application.status(context())

    assert result.ok is True
    assert result.data["state"] == "needs_configuration"
    assert result.data["diagnostics"][0]["code"] == "not_found"
    assert str(tmp_path) not in str(result)


def test_system_status_requires_at_least_one_enabled_agent(tmp_path: Path) -> None:
    application = build_control_plane(tmp_path, secret_store=InMemorySecretStore(), product_version="test")
    empty_node = node_payload()
    empty_node["spec"]["enabledAgents"] = []  # type: ignore[index]
    created = application.invoke(
        "config.node.apply",
        {"candidate": empty_node, "expectedRevision": None},
        context(),
    )

    result = application.status(context())

    assert created.ok is True
    assert result.ok is True
    assert result.data["state"] == "needs_configuration"
    assert result.data["diagnostics"][0]["code"] == "no_enabled_agents"


def test_config_actions_share_revision_validation_preview_and_apply(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    current = application.invoke("config.node.read", {}, context())
    candidate = deepcopy(node_payload())
    candidate["spec"]["displayName"] = "Updated Node"  # type: ignore[index]

    preview = application.invoke(
        "config.node.preview",
        {"candidate": candidate, "expectedRevision": current.data["revision"]},
        context(),
    )
    applied = application.invoke(
        "config.node.apply",
        {"candidate": candidate, "expectedRevision": current.data["revision"]},
        context(),
    )
    conflict = application.invoke(
        "config.node.apply",
        {"candidate": candidate, "expectedRevision": current.data["revision"]},
        context(),
    )

    assert preview.ok and preview.data["effect"] == "live"
    assert applied.ok and applied.data["revision"] != current.data["revision"]
    assert conflict.error is not None
    assert conflict.error.code == "revision_conflict"
    assert str(tmp_path) not in str(conflict)


def test_config_write_permission_is_enforced_by_executor(tmp_path: Path) -> None:
    application = configured_application(tmp_path)

    denied = application.invoke(
        "config.node.validate",
        {"candidate": node_payload()},
        context(write=False),
    )

    assert denied.error is not None
    assert denied.error.code == "permission_denied"


def test_model_actions_return_readiness_without_secret_material(tmp_path: Path) -> None:
    application = configured_application(tmp_path)

    profiles = application.invoke("model.list", {}, context())
    selection = application.invoke("model.select", {"agentId": "low-main"}, context())

    assert profiles.ok and profiles.data["items"][0]["id"] == "primary"
    assert selection.ok
    assert selection.data["profileId"] == "primary"
    assert selection.data["provider"] == "openai"
    assert "never-visible" not in str(selection)
