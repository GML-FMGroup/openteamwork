"""Transport-independent Extension Action lifecycle tests."""

from __future__ import annotations

import sys
from pathlib import Path

from openppx.actions import ActionContext
from openppx.config import AgentConfig, InMemorySecretStore, NodeConfig
from openppx.control_plane import build_control_plane
from openppx.extensions import AppManager, ExtensionRegistry, McpManager, PluginManager, SkillManager
from openppx.extensions.indexes import ExtensionReferenceIndex, ResourceIdentityIndex
from openppx.extensions.prefixes import ToolPrefixIndex
from openppx.runtime.mcp_adapter import McpRuntimeAdapter
from tests.extensions.test_app_resources import _connection, _definition
from tests.extensions.test_direct_mcp_registry import _server
from tests.extensions.test_skill_registry import _skill


def _context(
    *,
    confirmed: bool = False,
    write: bool = True,
    agent_id: str | None = None,
    principal_id: str | None = None,
    privilege_level: str | None = None,
) -> ActionContext:
    capabilities = frozenset({"system.read", "extension.read", "extension.write", "extension.auth"})
    permissions = capabilities if write else frozenset({"system.read", "extension.read"})
    return ActionContext(
        request_id="req_extensions",
        correlation_id="corr_extensions",
        actor_id="local:test",
        capabilities=capabilities,
        permissions=permissions,
        confirmed=confirmed,
        agent_id=agent_id,
        principal_id=principal_id,
        privilege_level=privilege_level,
    )


def _application(
    tmp_path: Path,
    *,
    builtin_skills: dict[str, Path] | None = None,
):
    node = tmp_path / "node"
    secrets = InMemorySecretStore()
    application = build_control_plane(node, secret_store=secrets, product_version="test")
    prefixes = ToolPrefixIndex()
    identities = ResourceIdentityIndex()
    references = ExtensionReferenceIndex()
    plugins = PluginManager(
        node,
        secrets,
        prefix_index=prefixes,
        identity_index=identities,
        reference_index=references,
    )
    mcp = McpManager(node, secrets, prefix_index=prefixes, identity_index=identities)
    apps = AppManager(
        node,
        secrets,
        prefix_index=prefixes,
        identity_index=identities,
    )
    skills = SkillManager(
        node,
        builtin_skills=builtin_skills,
        identity_index=identities,
    )
    inventory = ExtensionRegistry(skills=skills, mcp=mcp, apps=apps, plugins=plugins)
    application.attach_extensions(
        inventory,
        skills=skills,
        mcp=mcp,
        apps=apps,
        plugins=plugins,
        mcp_probe=McpRuntimeAdapter(secrets),
    )
    return application


def test_extension_inventory_projects_skills_and_extensions_commands(tmp_path: Path) -> None:
    application = _application(tmp_path)

    catalog = application.catalog(_context(), projection="slash")
    skills = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/skills",
            "userId": "local:test",
            "agentId": "writer",
        },
        _context(),
    )
    extensions = application.invoke(
        "system.command.invoke",
        {"rawCommand": "/extensions", "userId": "local:test"},
        _context(),
    )
    removed_skill_alias = application.invoke(
        "system.command.invoke",
        {"rawCommand": "/skill", "userId": "local:test", "agentId": "writer"},
        _context(),
    )

    extension_item = next(item for item in catalog.data["items"] if item["actionId"] == "extension.list")
    assert [command["command"] for command in extension_item["slashCommands"]] == [
        "/skills",
        "/extensions",
        "/plugins",
        "/apps",
        "/mcp",
    ]
    assert all(item["actionId"] != "extension.skill.command" for item in catalog.data["items"])
    assert skills.ok is True
    assert skills.data["targetActionId"] == "extension.list"
    assert removed_skill_alias.error is not None
    assert removed_skill_alias.error.code == "command_not_found"
    assert extensions.ok is True


def test_dynamic_skill_commands_omit_invalid_and_reserved_aliases(tmp_path: Path) -> None:
    application = _application(
        tmp_path,
        builtin_skills={
            "7zip": _skill(tmp_path / "numeric", name="7zip"),
            "status": _skill(tmp_path / "reserved", name="status"),
            "pptx": _skill(tmp_path / "presentation", name="pptx"),
        },
    )

    catalog = application.catalog(_context(agent_id="writer"), projection="slash")
    action_commands = {
        item["actionId"]: [command["command"] for command in item["slashCommands"]]
        for item in catalog.data["items"]
    }
    invalid = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/7zip inspect the archive",
            "userId": "local:test",
            "agentId": "writer",
            "sessionId": "session-1",
        },
        _context(),
    )

    assert action_commands["extension.skill.command"] == ["/pptx"]
    assert "/status" in action_commands["system.status"]
    assert invalid.error is not None
    assert invalid.error.code == "command_not_found"


def test_dynamic_skill_commands_are_scoped_to_the_authenticated_agent_owner(tmp_path: Path) -> None:
    application = _application(
        tmp_path,
        builtin_skills={"pptx": _skill(tmp_path / "presentation", name="pptx")},
    )
    application.config_service.apply_node(
        NodeConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "NodeConfig",
                "metadata": {"name": "test-node"},
                "spec": {
                    "displayName": "Test Node",
                    "enabledAgents": ["writer"],
                    "clientApi": {
                        "listenHost": "127.0.0.1",
                        "port": 18765,
                        "authentication": "required",
                    },
                },
            }
        ),
        expected_revision=None,
    )
    application.config_service.apply_agent(
        "writer",
        AgentConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AgentConfig",
                "metadata": {"name": "writer"},
                "spec": {
                    "displayName": "Writer",
                    "workspace": str(tmp_path / "writer-workspace"),
                    "ownerPrincipalId": "user-owner",
                    "privilegeLevel": "medium",
                    "controls": {},
                    "modelPolicy": {
                        "defaultProfile": "primary",
                        "roleProfiles": {},
                    },
                },
            }
        ),
        expected_revision=None,
    )
    owner = _context(
        agent_id="writer",
        principal_id="user-owner",
        privilege_level="medium",
    )
    other = _context(
        agent_id="writer",
        principal_id="user-other",
        privilege_level="high",
    )
    root = _context(
        agent_id="writer",
        principal_id="user-root",
        privilege_level="root",
    )

    owner_catalog = application.catalog(owner, projection="slash")
    other_catalog = application.catalog(other, projection="slash")
    root_catalog = application.catalog(root, projection="slash")
    denied = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/pptx create a presentation",
            "userId": "user-other",
            "agentId": "writer",
            "sessionId": "session-1",
        },
        other,
    )

    def commands(catalog) -> list[str]:
        return [
            command["command"]
            for item in catalog.data["items"]
            for command in item["slashCommands"]
        ]

    assert "/pptx" in commands(owner_catalog)
    assert "/pptx" not in commands(other_catalog)
    assert "/pptx" in commands(root_catalog)
    assert denied.error is not None
    assert denied.error.code == "command_not_found"


def test_extension_starter_actions_filter_and_get_safe_catalog_entries(tmp_path: Path) -> None:
    application = _application(tmp_path)

    listed = application.invoke(
        "extension.starter.list",
        {"kind": "app", "query": "granola"},
        _context(),
    )
    fetched = application.invoke(
        "extension.starter.get",
        {"starterId": "app-granola"},
        _context(),
    )

    assert listed.ok is True
    assert listed.data["counts"] == {"plugin": 0, "app": 1, "mcp": 0, "skill": 0}
    assert [item["id"] for item in listed.data["items"]] == ["app-granola"]
    assert fetched.ok is True
    assert fetched.data["displayName"] == "Granola"
    assert "tokens" not in str(fetched.data).lower()


def test_direct_app_starter_install_is_validated_and_idempotent(tmp_path: Path) -> None:
    application = _application(tmp_path)

    installed = application.invoke(
        "app.starter.install",
        {"starterId": "app-telegram"},
        _context(),
    )
    repeated = application.invoke(
        "app.starter.install",
        {"starterId": "app-telegram"},
        _context(),
    )
    invalid = application.invoke(
        "app.starter.install",
        {"starterId": "app-granola"},
        _context(),
    )
    inventory = application.invoke(
        "extension.list",
        {"kind": "app", "agentId": None},
        _context(),
    )

    assert installed.ok is True
    assert installed.data["id"] == "telegram"
    assert repeated.ok is True
    assert repeated.data["revision"] == installed.data["revision"]
    assert inventory.data["items"][0]["presentation"] == {
        "icon": "telegram",
        "brandColor": "#229ed9",
    }
    assert invalid.error is not None
    assert invalid.error.code == "invalid_operation"


def test_first_wave_office_apps_use_the_common_starter_install_path(tmp_path: Path) -> None:
    application = _application(tmp_path)

    expected = {
        "app-email": "email-imap",
        "app-feishu-docs": "feishu-docs",
        "app-notion": "notion",
        "app-wps-cloud-docs": "wps-cloud-docs",
    }
    installed = {
        starter_id: application.invoke(
            "app.starter.install",
            {"starterId": starter_id},
            _context(),
        )
        for starter_id in expected
    }
    inventory = application.invoke(
        "extension.list",
        {"kind": "app", "agentId": None},
        _context(),
    )

    assert all(result.ok for result in installed.values())
    assert {
        starter_id: result.data["id"]
        for starter_id, result in installed.items()
    } == expected
    assert {item["id"] for item in inventory.data["items"]} == set(expected.values())


def test_skill_preview_install_list_enable_disable_remove_use_one_action_path(tmp_path: Path) -> None:
    application = _application(tmp_path)
    source = _skill(tmp_path / "source")
    reference = {"type": "local_directory", "locator": str(source)}

    preview = application.invoke(
        "extension.preview",
        {"kind": "skill", "source": reference},
        _context(),
    )
    unconfirmed = application.invoke(
        "extension.install",
        {
            "kind": "skill",
            "source": reference,
            "expectedDigest": preview.data["preview"]["digest"],
            "expectedRevision": None,
        },
        _context(),
    )
    installed = application.invoke(
        "extension.install",
        {
            "kind": "skill",
            "source": reference,
            "expectedDigest": preview.data["preview"]["digest"],
            "expectedRevision": None,
        },
        _context(confirmed=True),
    )
    listed = application.invoke("extension.list", {"kind": "skill"}, _context())
    enabled = application.invoke(
        "extension.enable",
        {
            "kind": "skill",
            "extensionId": "demo",
            "agentId": "writer",
            "expectedRevision": installed.data["revision"],
        },
        _context(confirmed=True),
    )
    command_catalog = application.catalog(_context(agent_id="writer"), projection="slash")
    command = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/demo summarize the current workspace",
            "userId": "local:test",
            "agentId": "writer",
            "sessionId": "session-1",
        },
        _context(),
    )
    disabled = application.invoke(
        "extension.disable",
        {
            "kind": "skill",
            "extensionId": "demo",
            "agentId": "writer",
            "expectedRevision": enabled.data["revision"],
        },
        _context(),
    )
    removed = application.invoke(
        "extension.remove",
        {
            "kind": "skill",
            "extensionId": "demo",
            "expectedRevision": disabled.data["revision"],
        },
        _context(confirmed=True),
    )

    assert preview.ok and preview.data["preview"]["skillId"] == "demo"
    assert str(tmp_path) not in str(preview.data)
    assert unconfirmed.error is not None and unconfirmed.error.code == "confirmation_required"
    assert installed.ok and listed.data["items"][0]["id"] == "demo"
    assert enabled.data["status"] == "enabled"
    skill_item = next(
        item for item in command_catalog.data["items"]
        if item["actionId"] == "extension.skill.command"
    )
    assert skill_item["slashCommands"] == [
        {
            "command": "/demo",
            "title": "demo",
            "description": "A deterministic fixture skill.",
            "icon": "sparkles",
            "argHint": "<instruction>",
            "lifecycle": "agent_turn",
            "acceptsArgs": True,
            "arguments": [
                {
                    "name": "instruction",
                    "valueType": "text",
                    "description": "Task to complete with this Skill.",
                    "required": True,
                    "choices": [],
                }
            ],
            "noArgsBehavior": "show_usage",
            "usage": "/demo <instruction>",
            "order": 80,
        }
    ]
    assert command.ok is True
    assert command.data["lifecycle"] == "agent_turn"
    assert command.data["result"]["startAgentTurn"]["skillName"] == "demo"
    assert 'read_skill before acting' in command.data["result"]["startAgentTurn"]["text"]
    unavailable = application.invoke(
        "system.command.invoke",
        {
            "rawCommand": "/demo try again",
            "userId": "local:test",
            "agentId": "writer",
            "sessionId": "session-1",
        },
        _context(),
    )
    assert unavailable.error is not None
    assert unavailable.error.code == "command_not_found"
    assert disabled.data["status"] == "disabled"
    assert removed.data == {"kind": "skill", "id": "demo", "removed": True}


def test_install_rejects_source_drift_and_errors_are_redacted(tmp_path: Path) -> None:
    application = _application(tmp_path)
    source = _skill(tmp_path / "private-source")
    reference = {"type": "local_directory", "locator": str(source)}
    preview = application.invoke("extension.preview", {"kind": "skill", "source": reference}, _context())
    (source / "SKILL.md").write_text(
        (source / "SKILL.md").read_text(encoding="utf-8") + "\nchanged\n",
        encoding="utf-8",
    )

    drift = application.invoke(
        "extension.install",
        {
            "kind": "skill",
            "source": reference,
            "expectedDigest": preview.data["preview"]["digest"],
            "expectedRevision": None,
        },
        _context(confirmed=True),
    )
    missing = application.invoke(
        "extension.get",
        {"kind": "skill", "extensionId": "missing"},
        _context(),
    )

    assert drift.error is not None and drift.error.code == "source_changed"
    assert missing.error is not None and missing.error.code == "extension_not_found"
    assert str(tmp_path) not in str(drift)
    assert str(tmp_path) not in str(missing)


def test_extension_write_permission_is_enforced_before_source_access(tmp_path: Path) -> None:
    application = _application(tmp_path)

    denied = application.invoke(
        "extension.preview",
        {"kind": "skill", "source": {"type": "local_directory", "locator": str(tmp_path / "missing")}},
        _context(write=False),
    )

    assert denied.error is not None
    assert denied.error.code == "permission_denied"


def test_mcp_and_app_connection_actions_run_live_tool_discovery(tmp_path: Path) -> None:
    application = _application(tmp_path)
    fixture = Path("tests/eval/mock_mcp_server.py").resolve()
    mcp_resource = _server(
        "probe",
        transport={
            "type": "stdio",
            "command": sys.executable,
            "args": [str(fixture)],
            "environment": {},
        },
    )
    created_mcp = application.invoke(
        "mcp.create",
        {"resource": mcp_resource.model_dump(mode="json", by_alias=True), "expectedRevision": None},
        _context(),
    )
    installed_app = application.invoke(
        "app.definition.install",
        {
            "resource": _definition(auth_type="none").model_dump(mode="json", by_alias=True),
            "expectedRevision": None,
        },
        _context(),
    )
    created_connection = application.invoke(
        "app.connection.create",
        {
            "resource": _connection().model_copy(
                update={
                    "spec": _connection().spec.model_copy(
                        update={"credential_refs": {}, "app_id": "fixture-app"}
                    )
                }
            ).model_dump(mode="json", by_alias=True),
            "expectedRevision": None,
        },
        _context(),
    )

    mcp_result = application.invoke("mcp.test", {"serverId": "probe"}, _context())
    app_result = application.invoke(
        "app.connection.test",
        {"connectionId": "fixture-account"},
        _context(),
    )
    health = application.invoke(
        "extension.health.history",
        {"kind": "mcp", "extensionId": "probe", "limit": 10},
        _context(),
    )

    assert created_mcp.ok and installed_app.ok and created_connection.ok
    assert mcp_result.ok is True
    assert mcp_result.data["ready"] is True
    assert mcp_result.data["status"] == "ok"
    assert mcp_result.data["toolNames"] == ["mcp_probe_echo_context"]
    assert app_result.ok is True
    assert app_result.data["ready"] is True
    assert app_result.data["toolNames"] == [
        "app_fixture_app_fixture_account_echo_context"
    ]
    assert health.ok is True
    assert health.data["summary"]["lastSuccessAtMs"] is not None
    assert health.data["items"][0]["ready"] is True
    assert health.data["items"][0]["toolCount"] == 1


def test_live_test_returns_safe_blocked_result_for_static_dependency_failure(tmp_path: Path) -> None:
    application = _application(tmp_path)
    blocked = _server(
        "blocked",
        transport={
            "type": "stdio",
            "command": "definitely-not-installed-openppx",
            "args": [],
            "environment": {},
        },
    )
    application.invoke(
        "mcp.create",
        {"resource": blocked.model_dump(mode="json", by_alias=True), "expectedRevision": None},
        _context(),
    )

    result = application.invoke("mcp.test", {"serverId": "blocked"}, _context())

    assert result.ok is True
    assert result.data["ready"] is False
    assert result.data["status"] == "blocked"
    assert result.data["issues"] == ["executable_missing"]
    assert result.data["toolNames"] == []
