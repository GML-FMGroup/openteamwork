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


def test_update_disable_and_recoverable_delete_preserve_workspace(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    created = application.agent_lifecycle.create(
        agent_id="research",
        display_name="Research",
        owner_principal_id="ppx-client-user",
        privilege_level="medium",
        model_profile_id="primary",
    )

    updated = application.agent_lifecycle.update(
        agent_id="research",
        display_name="Research Desk",
        workspace=str(created.workspace),
        privilege_level="high",
        model_profile_id="primary",
        instruction="Prefer concise research notes.",
        expected_revision=created.agent.revision,
    )
    assert updated.agent.document.spec.display_name == "Research Desk"
    assert updated.agent.document.metadata.name == "research"
    assert updated.agent.document.spec.instruction == "Prefer concise research notes."
    assert updated.agent.document.spec.privilege_level == "high"
    assert application.config_repository.paths.agent_file("research").is_file()
    assert not (application.config_repository.paths.agents_dir / "Research Desk").exists()

    with pytest.raises(AgentLifecycleError) as active_delete:
        application.agent_lifecycle.delete(
            agent_id="research",
            expected_revision=updated.agent.revision,
        )
    assert active_delete.value.code == "agent_must_be_disabled"

    disabled = application.agent_lifecycle.set_enabled(agent_id="research", enabled=False)
    assert disabled.enabled is False
    removed = application.agent_lifecycle.delete(
        agent_id="research",
        expected_revision=updated.agent.revision,
    )
    assert removed.workspace.is_dir()
    assert removed.archive_path.is_file()
    assert "research" not in application.config_repository.list_agent_ids()
