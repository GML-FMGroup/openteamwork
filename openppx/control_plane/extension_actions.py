"""Extension inventory and lifecycle Actions over the four domain managers."""

from __future__ import annotations

from typing import cast

from openppx.actions import (
    ActionError,
    ActionFailure,
    ActionRegistry,
    ActionSpec,
    SlashCommandSpec,
    SlashInvocationContext,
)
from openppx.extensions import (
    AppManager,
    ExtensionError,
    ExtensionRegistry,
    McpManager,
    PluginManager,
    SkillManager,
)

from .errors import raise_extension_failure
from .input_models import (
    AppConnectionEnablementInput,
    AppConnectionIdentityInput,
    AppConnectionMutationInput,
    AppConnectionReauthorizeInput,
    AppDefinitionMutationInput,
    AppDefinitionRemoveInput,
    ExtensionEnablementInput,
    ExtensionIdentityInput,
    ExtensionInstallInput,
    ExtensionListInput,
    ExtensionPreviewInput,
    ExtensionRemoveInput,
    McpCreateInput,
    McpUpdateInput,
)


def register_extension_actions(
    actions: ActionRegistry,
    inventory: ExtensionRegistry,
    *,
    skills: SkillManager,
    mcp: McpManager,
    apps: AppManager,
    plugins: PluginManager,
) -> None:
    """Register common inventory plus domain-owned lifecycle operations."""
    actions.register(
        _spec("extension.list", "List Extensions", "List installed Extension resources.", ExtensionListInput, "extension.read"),
        lambda _context, value: _call(lambda: _list(inventory, cast(ExtensionListInput, value))),
        slash_input=_extension_list_slash_input,
    )
    actions.register(
        _spec("extension.get", "Get Extension", "Read one installed Extension resource.", ExtensionIdentityInput, "extension.read"),
        lambda _context, value: _call(lambda: inventory.get(cast(ExtensionIdentityInput, value).kind, cast(ExtensionIdentityInput, value).extension_id).to_payload()),
    )
    actions.register(
        _spec("extension.readiness", "Check Extension readiness", "Check one Extension without exposing credentials.", ExtensionIdentityInput, "extension.read"),
        lambda _context, value: _call(lambda: inventory.readiness(cast(ExtensionIdentityInput, value).kind, cast(ExtensionIdentityInput, value).extension_id)),
    )
    actions.register(
        _spec("extension.preview", "Preview Extension install", "Stage and validate a Skill or Plugin source.", ExtensionPreviewInput, "extension.write"),
        lambda _context, value: _call(lambda: _preview(skills, plugins, cast(ExtensionPreviewInput, value))),
    )
    actions.register(
        _spec("extension.install", "Install Extension", "Install a confirmed Skill or Plugin source.", ExtensionInstallInput, "extension.write", risk="high", confirmation="required"),
        lambda context, value: _call(lambda: _install(skills, plugins, cast(ExtensionInstallInput, value), confirmed=context.confirmed)),
    )
    actions.register(
        _spec("extension.enable", "Enable Extension", "Enable a Skill, MCP, or Plugin for an Agent.", ExtensionEnablementInput, "extension.write", risk="high", confirmation="required"),
        lambda context, value: _call(lambda: _enable(skills, mcp, plugins, cast(ExtensionEnablementInput, value), confirmed=context.confirmed)),
    )
    actions.register(
        _spec("extension.disable", "Disable Extension", "Disable a Skill, MCP, or Plugin for an Agent.", ExtensionEnablementInput, "extension.write"),
        lambda _context, value: _call(lambda: _disable(skills, mcp, plugins, cast(ExtensionEnablementInput, value))),
    )
    actions.register(
        _spec("extension.remove", "Remove Extension", "Remove an inactive Skill, MCP, or Plugin.", ExtensionRemoveInput, "extension.write", risk="high", confirmation="required"),
        lambda _context, value: _call(lambda: _remove(skills, mcp, plugins, cast(ExtensionRemoveInput, value))),
    )

    actions.register(
        _domain_spec("mcp.create", "Create MCP Server", "Create one direct MCP resource.", McpCreateInput, "extension.write"),
        lambda _context, value: _call(lambda: _versioned(mcp.create(cast(McpCreateInput, value).resource, expected_revision=None))),
    )
    actions.register(
        _domain_spec("mcp.update", "Update MCP Server", "Update one direct MCP resource.", McpUpdateInput, "extension.write"),
        lambda _context, value: _call(lambda: _versioned(mcp.update(cast(McpUpdateInput, value).resource, expected_revision=cast(McpUpdateInput, value).expected_revision))),
    )
    actions.register(
        _domain_spec("app.definition.install", "Install App definition", "Install one directly managed App definition.", AppDefinitionMutationInput, "extension.write"),
        lambda _context, value: _call(lambda: _versioned(apps.install_definition(cast(AppDefinitionMutationInput, value).resource, expected_revision=cast(AppDefinitionMutationInput, value).expected_revision))),
    )
    actions.register(
        _domain_spec("app.definition.update", "Update App definition", "Update one directly managed App definition.", AppDefinitionMutationInput, "extension.write"),
        lambda _context, value: _call(lambda: _versioned(apps.update_definition(cast(AppDefinitionMutationInput, value).resource, expected_revision=_required_revision(cast(AppDefinitionMutationInput, value).expected_revision)))),
    )
    actions.register(
        _domain_spec("app.definition.remove", "Remove App definition", "Remove one unreferenced App definition.", AppDefinitionRemoveInput, "extension.write", risk="high", confirmation="required"),
        lambda _context, value: _call(lambda: _remove_app_definition(apps, cast(AppDefinitionRemoveInput, value))),
    )
    actions.register(
        _domain_spec("app.connection.create", "Create App connection", "Create one App authorization instance.", AppConnectionMutationInput, "extension.write"),
        lambda _context, value: _call(lambda: _versioned(apps.create_connection(cast(AppConnectionMutationInput, value).resource, expected_revision=cast(AppConnectionMutationInput, value).expected_revision))),
    )
    actions.register(
        _domain_spec("app.connection.update", "Update App connection", "Update one App connection policy.", AppConnectionMutationInput, "extension.write"),
        lambda _context, value: _call(lambda: _versioned(apps.update_connection(cast(AppConnectionMutationInput, value).resource, expected_revision=_required_revision(cast(AppConnectionMutationInput, value).expected_revision)))),
    )
    actions.register(
        _domain_spec("app.connection.reauthorize", "Reauthorize App connection", "Replace protected credential references.", AppConnectionReauthorizeInput, "extension.auth", risk="high", confirmation="required"),
        lambda _context, value: _call(lambda: _reauthorize(apps, cast(AppConnectionReauthorizeInput, value))),
    )
    actions.register(
        _domain_spec("app.connection.enable", "Enable App connection", "Enable one App connection for an Agent.", AppConnectionEnablementInput, "extension.write", risk="high", confirmation="required"),
        lambda context, value: _call(lambda: _enable_connection(apps, cast(AppConnectionEnablementInput, value), confirmed=context.confirmed)),
    )
    actions.register(
        _domain_spec("app.connection.disable", "Disable App connection", "Disable one App connection for an Agent.", AppConnectionEnablementInput, "extension.write"),
        lambda _context, value: _call(lambda: _disable_connection(apps, cast(AppConnectionEnablementInput, value))),
    )
    actions.register(
        _domain_spec("app.connection.remove", "Remove App connection", "Remove one inactive App connection.", AppConnectionIdentityInput, "extension.write", risk="high", confirmation="required"),
        lambda _context, value: _call(lambda: _remove_connection(apps, cast(AppConnectionIdentityInput, value))),
    )


def _spec(action_id, title, description, input_model, permission, *, risk="low", confirmation="never") -> ActionSpec:
    slash_commands = ()
    projections = ("cli", "desktop", "mobile")
    if action_id == "extension.list":
        projections = ("cli", "slash", "desktop", "mobile")
        slash_commands = (
            SlashCommandSpec(
                command="/skills",
                title="Show skills",
                description="List Skills available to the selected Agent.",
                icon="sparkles",
                order=80,
            ),
            SlashCommandSpec(
                command="/extensions",
                title="Show extensions",
                description="List installed Plugins, Apps, MCP servers, and Skills.",
                icon="blocks",
                order=90,
            ),
        )
    return ActionSpec(
        action_id=action_id,
        namespace="extension",
        title=title,
        description=description,
        input_model=input_model,
        scope="extension",
        required_capabilities=frozenset({permission}),
        permission=permission,
        risk=risk,
        confirmation=confirmation,
        projections=projections,
        slash_commands=slash_commands,
    )


def _extension_list_slash_input(
    command: SlashCommandSpec,
    _args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    if command.command == "/skills":
        return {"kind": "skill", "agentId": context.agent_id}
    return {"kind": None, "agentId": None}


def _domain_spec(action_id, title, description, input_model, permission, *, risk="low", confirmation="never") -> ActionSpec:
    return ActionSpec(
        action_id=action_id,
        namespace=action_id.split(".", 1)[0],
        title=title,
        description=description,
        input_model=input_model,
        scope="extension",
        required_capabilities=frozenset({permission}),
        permission=permission,
        risk=risk,
        confirmation=confirmation,
        projections=("cli", "desktop", "mobile"),
    )


def _call(operation):
    try:
        return operation()
    except ExtensionError as exc:
        raise_extension_failure(exc)


def _list(inventory: ExtensionRegistry, value: ExtensionListInput) -> dict[str, object]:
    return {"items": [item.to_payload() for item in inventory.list(kind=value.kind, agent_id=value.agent_id)]}


def _preview(skills: SkillManager, plugins: PluginManager, value: ExtensionPreviewInput) -> dict[str, object]:
    manager = skills if value.kind == "skill" else plugins
    staged = manager.stage(value.source)
    try:
        preview = manager.preview(staged)
        payload = _json_value(preview)
        return {"kind": value.kind, "preview": payload}
    finally:
        staged.extension.cleanup()


def _install(skills: SkillManager, plugins: PluginManager, value: ExtensionInstallInput, *, confirmed: bool) -> dict[str, object]:
    manager = skills if value.kind == "skill" else plugins
    staged = manager.stage(value.source)
    if staged.extension.digest != value.expected_digest:
        staged.extension.cleanup()
        raise ActionFailure(ActionError("source_changed", "The Extension source changed after preview."))
    if value.kind == "skill":
        installed = skills.install(staged, expected_revision=value.expected_revision)
    else:
        installed = plugins.install(staged, expected_revision=value.expected_revision, confirmed=confirmed)
    return _versioned(installed)


def _enable(skills, mcp, plugins, value: ExtensionEnablementInput, *, confirmed: bool):
    manager = {"skill": skills, "mcp": mcp, "plugin": plugins}[value.kind]
    return _versioned(manager.enable(value.extension_id, value.agent_id, expected_revision=value.expected_revision, confirmed=confirmed))


def _disable(skills, mcp, plugins, value: ExtensionEnablementInput):
    manager = {"skill": skills, "mcp": mcp, "plugin": plugins}[value.kind]
    return _versioned(manager.disable(value.extension_id, value.agent_id, expected_revision=value.expected_revision))


def _remove(skills, mcp, plugins, value: ExtensionRemoveInput):
    manager = {"skill": skills, "mcp": mcp, "plugin": plugins}[value.kind]
    manager.remove(value.extension_id, expected_revision=value.expected_revision)
    return {"kind": value.kind, "id": value.extension_id, "removed": True}


def _versioned(value) -> dict[str, object]:
    record = value.record
    return {
        "id": record.metadata.name,
        "kind": record.kind,
        "revision": value.revision,
        "status": value.status,
    }


def _remove_app_definition(apps: AppManager, value: AppDefinitionRemoveInput) -> dict[str, object]:
    apps.remove_definition(value.app_id, expected_revision=value.expected_revision)
    return {"id": value.app_id, "kind": "AppDefinition", "removed": True}


def _reauthorize(apps: AppManager, value: AppConnectionReauthorizeInput) -> dict[str, object]:
    return _versioned(apps.reauthorize(value.connection_id, value.credential_refs, expected_revision=value.expected_revision))


def _enable_connection(apps: AppManager, value: AppConnectionEnablementInput, *, confirmed: bool) -> dict[str, object]:
    return _versioned(apps.enable_connection(value.connection_id, value.agent_id, expected_revision=value.expected_revision, confirmed=confirmed))


def _disable_connection(apps: AppManager, value: AppConnectionEnablementInput) -> dict[str, object]:
    return _versioned(apps.disable_connection(value.connection_id, value.agent_id, expected_revision=value.expected_revision))


def _remove_connection(apps: AppManager, value: AppConnectionIdentityInput) -> dict[str, object]:
    apps.remove_connection(value.connection_id, expected_revision=value.expected_revision)
    return {"id": value.connection_id, "kind": "AppConnection", "removed": True}


def _required_revision(value: str | None) -> str:
    if value is None:
        raise ActionFailure(ActionError("invalid_action_input", "An existing resource requires expectedRevision."))
    return value


def _json_value(value) -> dict[str, object]:
    if hasattr(value, "__dataclass_fields__"):
        result: dict[str, object] = {}
        for name in value.__dataclass_fields__:
            item = getattr(value, name)
            key = {
                "plugin_id": "pluginId",
                "skill_id": "skillId",
                "display_name": "displayName",
                "source_type": "sourceType",
                "runtime_capabilities": "runtimeCapabilities",
                "resource_counts": "resourceCounts",
            }.get(name, name)
            if hasattr(item, "model_dump"):
                result[key] = item.model_dump(mode="json", by_alias=True)
            elif isinstance(item, tuple):
                result[key] = list(item)
            else:
                result[key] = item
        return result
    raise TypeError("Preview must be a dataclass")


__all__ = ["register_extension_actions"]
