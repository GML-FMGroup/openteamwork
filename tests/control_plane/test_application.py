"""Control Plane system, Config, and Model vertical-slice tests."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from openppx.actions import ActionContext
from openppx.config import InMemorySecretStore, SecretRef, SecretValue
from openppx.control_plane import build_control_plane
from openppx.modeling import ModelCatalog, ModelProfile, ProviderAccessService
from openppx.permissions import PermissionRequest, evaluate_permission


class FakeProviderAccess(ProviderAccessService):
    """Secret-free provider fixture for Action projection tests."""

    def __init__(self, tmp_path: Path) -> None:
        self.catalog = ModelCatalog(codex_home=tmp_path / "codex-home")

    def list_models(self, provider_id: str) -> dict[str, object]:
        return {
            "providerId": provider_id,
            "source": "fixture",
            "authoritative": True,
            "defaultModel": "openai-codex/gpt-test",
            "items": [{"id": "openai-codex/gpt-test", "displayName": "GPT Test", "description": "", "defaultReasoningEffort": None, "reasoningEfforts": []}],
        }

    def auth_status(self, provider_id: str) -> dict[str, object]:
        return {"providerId": provider_id, "state": "authenticated", "source": "codex_cli", "expiresAt": None, "loginMode": "device_code", "session": None}

    def close(self) -> None:
        pass


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
            "controls": {},
            "modelPolicy": {"defaultProfile": "primary", "roleProfiles": {}},
        },
    }


def profile_payload() -> dict[str, object]:
    return {
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


def context(*, write: bool = True, request_id: str = "req_control_plane") -> ActionContext:
    capabilities = frozenset({"system.read", "config.read", "config.write", "flow.read", "flow.write", "goal.read", "goal.write", "model.read", "model.write", "model.use"})
    permissions = capabilities if write else frozenset({"system.read", "config.read", "flow.read", "goal.read", "model.read", "model.use"})
    return ActionContext(
        request_id=request_id,
        correlation_id="corr_control_plane",
        actor_id="local:test",
        capabilities=capabilities,
        permissions=permissions,
    )


def test_goal_actions_persist_plan_and_require_completion_evidence(tmp_path: Path) -> None:
    application = configured_application(tmp_path)

    created = application.invoke(
        "goal.create",
        {
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": "session-goal",
            "objective": "Ship a verified release",
            "completionCriteria": ["Tests pass", "Artifact exists"],
        },
        context(),
    )

    assert created.ok is True
    assert created.data["status"] == "active"
    assert created.data["permissionRevision"]
    assert created.data["modelProfileRevision"]
    flow = created.data["flow"]
    updated_flow = application.invoke(
        "task_flow.update",
        {
            "flowId": flow["flowId"],
            "userId": "local:test",
            "expectedRevision": flow["revision"],
            "steps": [
                {
                    "stepId": "verify",
                    "title": "Verify release",
                    "status": "pending",
                    "dependsOn": [],
                }
            ],
        },
        context(),
    )
    assert updated_flow.ok is True
    denied = application.invoke(
        "goal.complete",
        {
            "goalId": created.data["goalId"],
            "userId": "local:test",
            "expectedRevision": created.data["revision"],
        },
        context(),
    )
    assert denied.ok is False
    assert denied.error.code == "goal_state_invalid"
    completed = application.invoke(
        "goal.complete",
        {
            "goalId": created.data["goalId"],
            "userId": "local:test",
            "expectedRevision": created.data["revision"],
            "completionEvidence": [
                {
                    "type": "artifact",
                    "ref": "release.zip",
                    "label": "Release artifact",
                    "version": 0,
                    "criteria": ["Artifact exists"],
                },
                {
                    "type": "task_run",
                    "ref": "tests-1",
                    "label": "Tests passed",
                    "criteria": ["Tests pass"],
                },
            ],
        },
        context(),
    )
    assert completed.ok is True
    assert completed.data["status"] == "completed"


def test_goal_slash_command_creates_goal_and_requests_one_normal_agent_turn(tmp_path: Path) -> None:
    application = configured_application(tmp_path)

    outcome = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/goal Prepare a release with test evidence",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": "session-slash-goal",
        },
        context(),
    )

    assert outcome.ok is True
    assert outcome.data["targetActionId"] == "goal.command"
    assert outcome.data["lifecycle"] == "agent_turn"
    result = outcome.data["result"]
    assert result["objective"] == "Prepare a release with test evidence"
    assert result["startAgentTurn"] == {
        "text": "Prepare a release with test evidence",
        "goalId": result["goalId"],
    }
    status = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/goal status",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": "session-slash-goal",
        },
        context(),
    )
    assert status.ok is True
    assert status.data["lifecycle"] == "side_channel"
    assert status.data["result"]["current"]["goalId"] == result["goalId"]

    history = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/goal history",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": "session-slash-goal",
        },
        context(),
    )
    assert history.ok is True
    assert history.data["lifecycle"] == "side_channel"
    history_result = history.data["result"]
    assert history_result["items"][0]["goalId"] == result["goalId"]
    assert history_result["selected"]["goalId"] == result["goalId"]
    assert history_result["events"][0]["goalId"] == result["goalId"]


def test_goal_conflict_explains_how_to_continue_or_cancel_current_goal(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    first = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/goal Prepare the existing release",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": "session-goal-conflict",
        },
        context(request_id="req_goal_first"),
    )

    conflict = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/goal Replace it with another objective",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": "session-goal-conflict",
        },
        context(request_id="req_goal_second"),
    )

    assert first.ok is True
    assert conflict.ok is False
    assert conflict.error is not None
    assert conflict.error.code == "goal_active_exists"
    assert "Prepare the existing release" in conflict.error.message
    assert "/goal status" in conflict.error.message
    assert "/goal resume" in conflict.error.message
    assert "/goal cancel" in conflict.error.message
    assert conflict.error.details["currentGoal"]["goalId"] == first.data["result"]["goalId"]
    assert conflict.error.details["suggestedCommands"] == [
        "/goal status",
        "/goal resume",
        "/goal cancel",
    ]


def test_goal_retry_action_and_slash_command_reactivate_blocked_step(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    created = application.invoke(
        "goal.create",
        {
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": "session-goal-retry",
            "objective": "Recover a blocked research run",
        },
        context(request_id="req_goal_retry_create"),
    )
    goal_id = created.data["goalId"]
    flow = application.goal_store.flow_for_goal(goal_id)
    assert flow is not None
    running = application.goal_store.advance_flow_step(
        flow.flow_id,
        step_id="goal-execution",
        status="running",
        expected_revision=flow.revision,
        actor_id="system:supervisor",
    )
    application.goal_store.advance_flow_step(
        running.flow_id,
        step_id="goal-execution",
        status="blocked",
        expected_revision=running.revision,
        actor_id="system:supervisor",
    )
    original = application.goal_store.get_goal(goal_id)
    assert original is not None
    application.goal_store.transition_goal(
        goal_id,
        status="blocked",
        expected_revision=original.revision,
        actor_id="system:supervisor",
        reason="The same search repeated without progress.",
    )
    blocked = application.goal_store.get_goal(goal_id)
    assert blocked is not None

    formal = application.invoke(
        "goal.retry_step",
        {
            "goalId": goal_id,
            "userId": "local:test",
            "expectedRevision": blocked.revision,
        },
        context(request_id="req_goal_retry_formal"),
    )

    assert formal.ok is True
    assert formal.data["status"] == "active"
    assert formal.data["flow"]["waitReason"] == {}

    active_flow = application.goal_store.flow_for_goal(goal_id)
    assert active_flow is not None
    application.goal_store.advance_flow_step(
        active_flow.flow_id,
        step_id="goal-execution",
        status="blocked",
        expected_revision=active_flow.revision,
        actor_id="system:supervisor",
    )
    active = application.goal_store.get_goal(goal_id)
    assert active is not None
    application.goal_store.transition_goal(
        goal_id,
        status="blocked",
        expected_revision=active.revision,
        actor_id="system:supervisor",
        reason="No durable progress was observed.",
    )
    slash = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/goal retry",
            "userId": "local:test",
            "agentId": "low-main",
            "sessionId": "session-goal-retry",
        },
        context(request_id="req_goal_retry_slash"),
    )

    assert slash.ok is True
    assert slash.data["lifecycle"] == "agent_turn"
    assert slash.data["result"]["status"] == "active"
    assert slash.data["result"]["startAgentTurn"] == {
        "text": "Retry the blocked Goal step and continue working toward: Recover a blocked research run",
        "goalId": goal_id,
    }


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


def test_permission_audit_action_returns_only_redacted_decision_facts(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    snapshot = application.config_service.snapshot("low-main").permissions
    request = PermissionRequest.model_validate(
        {
            "requestId": "permission-action-test",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": "low-main", "runId": "run-1"},
            "object": "workspace",
            "action": "read",
            "resource": {"kind": "workspace_path", "path": "private/customer.txt"},
        }
    )
    application.permission_audit.record(
        request,
        evaluate_permission(snapshot, request),
        rollout_mode="observe",
    )
    audit_context = ActionContext(
        request_id="req_permission_audit",
        correlation_id="corr_permission_audit",
        actor_id="local:test",
        capabilities=frozenset({"audit.read"}),
        permissions=frozenset({"audit.read"}),
    )

    outcome = application.invoke(
        "permissions.audit.list",
        {"agentId": "low-main", "object": "workspace", "limit": 10},
        audit_context,
    )

    assert outcome.ok is True
    assert outcome.data is not None
    assert outcome.data["count"] == 1
    assert outcome.data["items"][0]["enforced"] is False
    assert "customer.txt" not in str(outcome.data)

    denied = application.invoke(
        "permissions.audit.list",
        {"limit": 10},
        context(request_id="req_permission_audit_denied"),
    )
    assert denied.ok is False
    assert denied.error is not None
    assert denied.error.code == "capability_required"


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
    assert profiles.data["items"][0]["displayName"] == "Primary"
    assert selection.ok
    assert selection.data["profileId"] == "primary"
    assert selection.data["provider"] == "openai"
    assert "never-visible" not in str(selection)


def test_model_profile_update_is_node_owned_revision_safe_and_secret_free(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    current = application.invoke("model.profile.read", {"profileId": "primary"}, context())

    saved = application.invoke(
        "model.profile.update",
        {
            "profileId": "primary",
            "displayName": "Daily work",
            "providerId": "openai",
            "model": "openai/gpt-5.5",
            "executionLocation": "remote",
            "apiBase": "http://127.0.0.1:8000/v1",
            "capabilities": ["text", "tool_calling"],
            "contextWindowTokens": 200000,
            "inputCostPerMillionUsd": None,
            "outputCostPerMillionUsd": None,
            "fallbackProfileIds": [],
            "enabled": True,
            "apiKey": "rotated-never-project",
            "expectedRevision": current.data["revision"],
        },
        context(),
    )
    conflict = application.invoke(
        "model.profile.update",
        {
            "profileId": "primary",
            "displayName": "Daily work",
            "providerId": "openai",
            "model": "openai/gpt-5.5",
            "executionLocation": "remote",
            "apiBase": None,
            "capabilities": ["text"],
            "contextWindowTokens": None,
            "inputCostPerMillionUsd": None,
            "outputCostPerMillionUsd": None,
            "fallbackProfileIds": [],
            "enabled": True,
            "apiKey": "must-not-write",
            "expectedRevision": current.data["revision"],
        },
        context(),
    )

    assert saved.ok
    assert saved.data["effect"] == "next_run"
    assert saved.data["credentialState"] == "available"
    assert saved.data["document"]["metadata"]["name"] == "primary"
    assert saved.data["document"]["spec"]["displayName"] == "Daily work"
    assert saved.data["document"]["spec"]["apiBase"] == "http://127.0.0.1:8000/v1"
    assert "rotated-never-project" not in str(saved)
    assert conflict.error is not None and conflict.error.code == "revision_conflict"
    spec = application.registry.resolve("model.profile.update").spec
    assert spec.scope == "node" and spec.risk == "medium"


def test_provider_catalog_and_auth_actions_are_node_owned_and_secret_free(tmp_path: Path) -> None:
    provider_access = FakeProviderAccess(tmp_path)
    application = build_control_plane(
        tmp_path,
        secret_store=InMemorySecretStore(),
        provider_access=provider_access,
        product_version="test",
    )

    catalog = application.invoke("model.catalog.list", {"providerId": "openai_codex"}, context())
    auth = application.invoke("model.auth.status", {"providerId": "openai_codex"}, context())

    assert catalog.ok and catalog.data["items"][0]["id"] == "openai-codex/gpt-test"
    assert auth.ok and auth.data["state"] == "authenticated"
    assert "token" not in str((catalog, auth)).lower()
    assert application.registry.resolve("model.catalog.list").spec.scope == "node"
    assert application.registry.resolve("model.auth.status").spec.scope == "node"


def test_agent_create_action_publishes_safe_profile_without_owner_projection(tmp_path: Path) -> None:
    application = configured_application(tmp_path)

    created = application.invoke(
        "agent.create",
        {
            "agentId": "research",
            "displayName": "Research",
            "workspace": None,
            "ownerPrincipalId": "ppx-client-user",
            "privilegeLevel": "medium",
            "modelProfileId": "primary",
        },
        context(),
    )
    listed = application.invoke("config.agent.list", {}, context())

    assert created.ok
    assert created.data["agent"]["id"] == "research"
    assert created.data["effect"] == "next_run"
    assert "owner" not in str(created.data).lower()
    assert [item["id"] for item in listed.data["items"]] == ["low-main", "research"]
    assert application.registry.resolve("agent.create").spec.scope == "node"


def test_slash_command_catalog_and_invocation_share_action_authorization(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    authorized = context()

    commands = application.catalog(authorized, projection="slash")
    status = application.invoke(
        "system.command.invoke",
        {"rawCommand": "/status", "userId": "local:test"},
        authorized,
    )
    help_result = application.invoke(
        "system.command.invoke",
        {"rawCommand": "/help", "userId": "local:test"},
        authorized,
    )
    agents = application.invoke(
        "system.command.invoke",
        {"rawCommand": "/agent", "userId": "local:test"},
        authorized,
    )

    assert commands.ok is True
    assert all(item["slashCommands"] for item in commands.data["items"])
    command_items = {item["actionId"]: item for item in commands.data["items"]}
    model_command = command_items["model.session.command"]
    assert model_command["operation"] == "mutation"
    assert model_command["slashCommands"][0]["usage"] == "/model [profile_or_operation]"
    assert model_command["slashCommands"][0]["arguments"] == [
        {
            "name": "profile_or_operation",
            "valueType": "string",
            "description": "A Model Profile ID, status, default, or reset.",
            "required": False,
            "choices": [],
        }
    ]
    assert status.ok is True
    assert status.data["targetActionId"] == "system.status"
    assert status.data["result"]["state"] == "ready"
    assert help_result.ok is True
    assert agents.ok is True
    assert agents.data["targetActionId"] == "agent.list"
    assert agents.data["result"]["items"][0]["id"] == "low-main"
    assert {item["actionId"] for item in help_result.data["result"]["items"]} >= {
        "system.help",
        "system.status",
        "model.session.command",
    }
    desktop_items = {
        item["actionId"]: item
        for item in application.catalog(authorized, projection="desktop").data["items"]
    }
    assert desktop_items["config.node.preview"]["operation"] == "read"
    assert desktop_items["config.node.apply"]["operation"] == "mutation"
    assert desktop_items["agent.create"]["operation"] == "mutation"


def test_slash_command_reports_unknown_command_and_rechecks_target_permission(tmp_path: Path) -> None:
    application = configured_application(tmp_path)
    system_only = ActionContext(
        request_id="req_command",
        correlation_id="corr_command",
        actor_id="local:test",
        capabilities=frozenset({"system.read"}),
        permissions=frozenset({"system.read"}),
    )

    unknown = application.invoke(
        "system.command.invoke",
        {"rawCommand": "/missing", "userId": "local:test"},
        system_only,
    )
    denied = application.invoke(
        "system.command.invoke",
        {"rawCommand": "/model", "userId": "local:test"},
        system_only,
    )

    assert unknown.error is not None
    assert unknown.error.code == "command_not_found"
    assert denied.error is not None
    assert denied.error.code == "capability_required"
