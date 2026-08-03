"""Transport-independent Extension Action lifecycle tests."""

from __future__ import annotations

from pathlib import Path

from openppx.actions import ActionContext
from openppx.config import InMemorySecretStore
from openppx.control_plane import build_control_plane
from openppx.extensions import AppManager, ExtensionRegistry, McpManager, PluginManager, SkillManager
from openppx.extensions.indexes import ExtensionReferenceIndex, ResourceIdentityIndex
from openppx.extensions.prefixes import ToolPrefixIndex
from tests.extensions.test_skill_registry import _skill


def _context(*, confirmed: bool = False, write: bool = True) -> ActionContext:
    capabilities = frozenset({"extension.read", "extension.write", "extension.auth"})
    permissions = capabilities if write else frozenset({"extension.read"})
    return ActionContext(
        request_id="req_extensions",
        correlation_id="corr_extensions",
        actor_id="local:test",
        capabilities=capabilities,
        permissions=permissions,
        confirmed=confirmed,
    )


def _application(tmp_path: Path):
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
        allowed_runtime_capabilities=frozenset({"runtime.task-observability"}),
    )
    mcp = McpManager(node, secrets, prefix_index=prefixes, identity_index=identities)
    apps = AppManager(
        node,
        secrets,
        prefix_index=prefixes,
        identity_index=identities,
        reference_index=references,
        owner_enabled=plugins.is_enabled,
    )
    apps.register_definition_provider("plugins", plugins.app_definitions)
    plugins.register_app_definition_validator("apps", apps.validate_managed_definitions)
    skills = SkillManager(node, identity_index=identities)
    inventory = ExtensionRegistry(skills=skills, mcp=mcp, apps=apps, plugins=plugins)
    application.attach_extensions(
        inventory,
        skills=skills,
        mcp=mcp,
        apps=apps,
        plugins=plugins,
    )
    return application


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
