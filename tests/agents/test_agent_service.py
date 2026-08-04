from __future__ import annotations

from pathlib import Path

import pytest

from openppx.agents import AgentLifecycleError, AgentLifecycleService

from tests.control_plane.test_application import configured_application


def test_create_publishes_agent_with_node_managed_workspace(tmp_path: Path) -> None:
    application = configured_application(tmp_path)

    result = application.agent_lifecycle.create(
        agent_id="research",
        display_name="Research",
        owner_principal_id="ppx-client-user",
        privilege_level="medium",
        model_profile_id="primary",
    )

    assert result.workspace == tmp_path / "workspaces" / "research"
    assert result.workspace.is_dir()
    assert application.config_repository.read_node().document.spec.enabled_agents == ["low-main", "research"]
    agent = application.config_repository.read_agent("research")
    assert agent.document.spec.display_name == "Research"
    assert agent.document.spec.owner_principal_id == "ppx-client-user"
    assert agent.document.spec.model_policy.default_profile == "primary"


def test_failed_node_publication_leaves_agent_inactive_and_retryable(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    real_service = application.config_service

    class FailingPublication:
        apply_agent = real_service.apply_agent

        def apply_node(self, *_args, **_kwargs):
            raise RuntimeError("publication fixture")

    interrupted = AgentLifecycleService(
        application.config_repository,
        FailingPublication(),  # type: ignore[arg-type]
        application.profile_repository,
    )
    with pytest.raises(RuntimeError, match="publication fixture"):
        interrupted.create(
            agent_id="research",
            display_name="Research",
            owner_principal_id="ppx-client-user",
            privilege_level="medium",
            model_profile_id="primary",
        )

    assert application.config_repository.read_agent("research").document.spec.display_name == "Research"
    assert "research" not in application.config_repository.read_node().document.spec.enabled_agents

    retried = application.agent_lifecycle.create(
        agent_id="research",
        display_name="Research",
        owner_principal_id="ppx-client-user",
        privilege_level="medium",
        model_profile_id="primary",
    )

    assert retried.agent.document.metadata.name == "research"
    assert "research" in application.config_repository.read_node().document.spec.enabled_agents


def test_create_rejects_relative_custom_workspace_and_missing_profile(tmp_path: Path) -> None:
    application = configured_application(tmp_path)

    with pytest.raises(AgentLifecycleError) as relative:
        application.agent_lifecycle.create(
            agent_id="relative",
            display_name="Relative",
            owner_principal_id="ppx-client-user",
            privilege_level="medium",
            model_profile_id="primary",
            workspace="workspace/relative",
        )
    with pytest.raises(AgentLifecycleError) as missing_profile:
        application.agent_lifecycle.create(
            agent_id="missing-profile",
            display_name="Missing Profile",
            owner_principal_id="ppx-client-user",
            privilege_level="medium",
            model_profile_id="missing",
        )

    assert relative.value.code == "workspace_not_absolute"
    assert missing_profile.value.code == "model_profile_not_found"


def test_conflicting_staged_agent_does_not_create_requested_workspace(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    real_service = application.config_service

    class FailingPublication:
        apply_agent = real_service.apply_agent

        def apply_node(self, *_args, **_kwargs):
            raise RuntimeError("publication fixture")

    interrupted = AgentLifecycleService(
        application.config_repository,
        FailingPublication(),  # type: ignore[arg-type]
        application.profile_repository,
    )
    first_workspace = tmp_path / "workspaces" / "first"
    with pytest.raises(RuntimeError, match="publication fixture"):
        interrupted.create(
            agent_id="research",
            display_name="Research",
            owner_principal_id="ppx-client-user",
            privilege_level="medium",
            model_profile_id="primary",
            workspace=str(first_workspace),
        )

    conflicting_workspace = tmp_path / "workspaces" / "conflict"
    with pytest.raises(AgentLifecycleError) as conflict:
        application.agent_lifecycle.create(
            agent_id="research",
            display_name="Different Research",
            owner_principal_id="ppx-client-user",
            privilege_level="medium",
            model_profile_id="primary",
            workspace=str(conflicting_workspace),
        )

    assert conflict.value.code == "agent_id_conflict"
    assert not conflicting_workspace.exists()
