"""Extension inventory and lifecycle Actions over the four domain managers."""

from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import UTC, datetime
from time import perf_counter
from typing import Callable, cast

from openppx.actions import (
    ActionError,
    ActionFailure,
    ActionContext,
    ActionRegistry,
    ActionSpec,
    SlashCommandArgumentSpec,
    SlashCommandSpec,
    SlashInvocationContext,
)
from openppx.extensions import (
    AppManager,
    ExtensionError,
    ExtensionHealthStore,
    ExtensionRegistry,
    ExtensionStarterCatalog,
    McpManager,
    PluginManager,
    SkillManager,
    SkillSnapshotEntry,
)
from openppx.extensions.app_models import AppDefinition

from .errors import raise_extension_failure
from .input_models import (
    AppConnectionEnablementInput,
    AppConnectionIdentityInput,
    AppConnectionMutationInput,
    AppConnectionReauthorizeInput,
    AppConnectionTestInput,
    AppDefinitionMutationInput,
    AppDefinitionRemoveInput,
    ExtensionEnablementInput,
    ExtensionIdentityInput,
    ExtensionInstallInput,
    ExtensionListInput,
    ExtensionPreviewInput,
    ExtensionRemoveInput,
    ExtensionStarterIdentityInput,
    ExtensionStarterListInput,
    ExtensionHealthHistoryInput,
    McpCreateInput,
    McpOAuthInput,
    McpTestInput,
    McpUpdateInput,
    PluginHookTrustInput,
    PluginMarketplaceIdentityInput,
    PluginMarketplaceListInput,
    PluginMarketplaceMutationInput,
    SkillCommandInput,
)


def register_extension_actions(
    actions: ActionRegistry,
    inventory: ExtensionRegistry,
    *,
    skills: SkillManager,
    mcp: McpManager,
    apps: AppManager,
    plugins: PluginManager,
    plugin_marketplaces,
    mcp_probe,
    starters: ExtensionStarterCatalog,
    mcp_oauth,
    health_store: ExtensionHealthStore,
    agent_access: Callable[[ActionContext, str], bool],
) -> None:
    """Register common inventory plus domain-owned lifecycle operations."""
    actions.register(
        _spec("extension.list", "List Extensions", "List installed Extension resources.", ExtensionListInput, "extension.read"),
        lambda _context, value: _call(lambda: _list(inventory, cast(ExtensionListInput, value))),
        slash_input=_extension_list_slash_input,
    )
    actions.register(
        ActionSpec(
            action_id="extension.skill.command",
            namespace="extension",
            title="Use Skill",
            description="List Agent-visible Skills or select one for a normal Google ADK turn.",
            input_model=SkillCommandInput,
            scope="extension",
            required_capabilities=frozenset({"extension.read"}),
            permission="extension.read",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda context, value: _call(
            lambda: _skill_command(
                skills,
                plugins,
                cast(SkillCommandInput, value),
                context=context,
                agent_access=agent_access,
            )
        ),
    )
    actions.register_dynamic_slash(
        "extension.skill.command",
        lambda context: _skill_slash_commands(
            skills,
            plugins,
            context,
            agent_access=agent_access,
        ),
        slash_input=_skill_command_slash_input,
    )
    actions.register(
        _domain_spec("mcp.oauth.begin", "Connect MCP OAuth", "Start an explicit browser authorization flow.", McpOAuthInput, "extension.auth", risk="medium"),
        lambda _context, value: _call(lambda: _begin_mcp_oauth(mcp, mcp_oauth, cast(McpOAuthInput, value))),
    )
    actions.register(
        _domain_spec("mcp.oauth.status", "Get MCP OAuth status", "Read non-sensitive MCP authorization status.", McpOAuthInput, "extension.read"),
        lambda _context, value: _call(lambda: mcp_oauth.status(cast(McpOAuthInput, value).server_id)),
    )
    actions.register(
        _domain_spec("mcp.oauth.signout", "Disconnect MCP OAuth", "Remove protected MCP OAuth credentials.", McpOAuthInput, "extension.auth", risk="high", confirmation="required"),
        lambda _context, value: _call(lambda: mcp_oauth.sign_out(cast(McpOAuthInput, value).server_id)),
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
        _domain_spec("extension.starter.list", "List Extension starters", "Discover safe first-party Extension starters.", ExtensionStarterListInput, "extension.read"),
        lambda _context, value: _call(lambda: _list_starters(starters, cast(ExtensionStarterListInput, value))),
    )
    actions.register(
        _domain_spec("extension.starter.get", "Get Extension starter", "Read one safe first-party Extension starter.", ExtensionStarterIdentityInput, "extension.read"),
        lambda _context, value: _call(lambda: starters.get(cast(ExtensionStarterIdentityInput, value).starter_id).to_payload()),
    )
    actions.register(
        _domain_spec(
            "extension.health.history",
            "Extension health history",
            "Read recent explicit connection-test observations without credentials.",
            ExtensionHealthHistoryInput,
            "extension.read",
        ),
        lambda _context, value: _call(
            lambda: _health_history(health_store, cast(ExtensionHealthHistoryInput, value))
        ),
    )
    actions.register(
        _domain_spec(
            "app.starter.install",
            "Install App starter",
            "Install one Node-shipped App definition from the validated starter catalog.",
            ExtensionStarterIdentityInput,
            "extension.write",
            risk="medium",
        ),
        lambda _context, value: _call(
            lambda: _install_app_starter(
                starters,
                apps,
                cast(ExtensionStarterIdentityInput, value),
            )
        ),
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
        _domain_spec(
            "plugin.hooks.status",
            "Get Plugin Hook status",
            "Inspect exact-definition Hook trust and runtime support.",
            PluginHookTrustInput,
            "extension.read",
        ),
        lambda _context, value: _call(
            lambda: plugins.hook_status(cast(PluginHookTrustInput, value).plugin_id).to_payload()
        ),
    )
    actions.register(
        _domain_spec(
            "plugin.marketplace.source.list",
            "List Plugin marketplaces",
            "List configured Plugin marketplace sources.",
            PluginMarketplaceListInput,
            "extension.read",
        ),
        lambda _context, _value: _call(
            lambda: {"items": [item.to_payload() for item in plugin_marketplaces.list()]}
        ),
    )
    actions.register(
        _domain_spec(
            "plugin.marketplace.entry.list",
            "List marketplace Plugins",
            "List cached Plugin packages across configured marketplaces.",
            PluginMarketplaceListInput,
            "extension.read",
        ),
        lambda _context, value: _call(
            lambda: {
                "items": [
                    item.to_payload()
                    for item in plugin_marketplaces.list_entries(
                        query=cast(PluginMarketplaceListInput, value).query
                    )
                ]
            }
        ),
    )
    actions.register(
        _domain_spec(
            "plugin.marketplace.source.create",
            "Add Plugin marketplace",
            "Add one local or Git Plugin marketplace source.",
            PluginMarketplaceMutationInput,
            "extension.write",
        ),
        lambda _context, value: _call(
            lambda: plugin_marketplaces.create(
                cast(PluginMarketplaceMutationInput, value).marketplace_id,
                cast(PluginMarketplaceMutationInput, value).spec,
                expected_revision=None,
            ).to_payload()
        ),
    )
    actions.register(
        _domain_spec(
            "plugin.marketplace.source.update",
            "Update Plugin marketplace",
            "Update and invalidate one Plugin marketplace source.",
            PluginMarketplaceMutationInput,
            "extension.write",
        ),
        lambda _context, value: _call(
            lambda: plugin_marketplaces.update(
                cast(PluginMarketplaceMutationInput, value).marketplace_id,
                cast(PluginMarketplaceMutationInput, value).spec,
                expected_revision=_required_revision(
                    cast(PluginMarketplaceMutationInput, value).expected_revision
                ),
            ).to_payload()
        ),
    )
    actions.register(
        _domain_spec(
            "plugin.marketplace.source.refresh",
            "Refresh Plugin marketplace",
            "Resolve and cache an immutable marketplace snapshot.",
            PluginMarketplaceIdentityInput,
            "extension.write",
            risk="medium",
        ),
        lambda _context, value: _call(
            lambda: plugin_marketplaces.refresh(
                cast(PluginMarketplaceIdentityInput, value).marketplace_id,
                expected_revision=cast(PluginMarketplaceIdentityInput, value).expected_revision,
            ).to_payload()
        ),
    )
    actions.register(
        _domain_spec(
            "plugin.marketplace.source.remove",
            "Remove Plugin marketplace",
            "Remove one configured Plugin marketplace source.",
            PluginMarketplaceIdentityInput,
            "extension.write",
            risk="high",
            confirmation="required",
        ),
        lambda _context, value: _call(
            lambda: _remove_marketplace(plugin_marketplaces, cast(PluginMarketplaceIdentityInput, value))
        ),
    )
    actions.register(
        _domain_spec(
            "plugin.hooks.trust",
            "Trust Plugin Hooks",
            "Allow the exact installed Hook definitions to execute host commands.",
            PluginHookTrustInput,
            "extension.auth",
            risk="high",
            confirmation="required",
        ),
        lambda _context, value: _call(
            lambda: plugins.trust_hooks(
                cast(PluginHookTrustInput, value).plugin_id,
                expected_revision=cast(PluginHookTrustInput, value).expected_revision,
            ).to_payload()
        ),
    )
    actions.register(
        _domain_spec(
            "plugin.hooks.untrust",
            "Revoke Plugin Hook trust",
            "Stop executing the installed Plugin Hook definitions.",
            PluginHookTrustInput,
            "extension.auth",
        ),
        lambda _context, value: _call(
            lambda: plugins.untrust_hooks(
                cast(PluginHookTrustInput, value).plugin_id,
                expected_revision=cast(PluginHookTrustInput, value).expected_revision,
            ).to_payload()
        ),
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
        _domain_spec(
            "mcp.test",
            "Test MCP Server",
            "Connect to one MCP resource and discover its current tools.",
            McpTestInput,
            "extension.auth",
            risk="medium",
        ),
        lambda _context, value: _call(
            lambda: _test_mcp(mcp, mcp_probe, health_store, cast(McpTestInput, value))
        ),
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
        _domain_spec(
            "app.connection.test",
            "Test App connection",
            "Connect through one App authorization instance and discover its current tools.",
            AppConnectionTestInput,
            "extension.auth",
            risk="medium",
        ),
        lambda _context, value: _call(
            lambda: _test_app_connection(apps, mcp_probe, health_store, cast(AppConnectionTestInput, value))
        ),
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
            SlashCommandSpec(
                command="/plugins",
                title="Show plugins",
                description="List installed Plugin packages.",
                icon="package",
                order=91,
            ),
            SlashCommandSpec(
                command="/apps",
                title="Show apps",
                description="List installed App definitions and connections.",
                icon="app-window",
                order=92,
            ),
            SlashCommandSpec(
                command="/mcp",
                title="Show MCP servers",
                description="List directly managed MCP servers.",
                icon="plug",
                order=93,
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
        operation=(
            "mutation"
            if action_id in {
                "extension.install",
                "extension.enable",
                "extension.disable",
                "extension.remove",
            }
            else "read"
        ),
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
    if command.command == "/plugins":
        return {"kind": "plugin", "agentId": context.agent_id}
    if command.command == "/apps":
        return {"kind": "app", "agentId": context.agent_id}
    if command.command == "/mcp":
        return {"kind": "mcp", "agentId": context.agent_id}
    return {"kind": None, "agentId": None}


def _skill_command_slash_input(
    command: SlashCommandSpec,
    args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    """Project one Agent-visible Skill alias into a typed command input."""
    return {
        "agentId": context.agent_id,
        "skillName": command.command.removeprefix("/"),
        "instruction": args,
    }


def _skill_slash_commands(
    skills: SkillManager,
    plugins: PluginManager,
    context: ActionContext,
    *,
    agent_access: Callable[[ActionContext, str], bool],
) -> tuple[SlashCommandSpec, ...]:
    """Project the selected Agent's current Skill snapshot as direct aliases."""
    if context.agent_id is None or not agent_access(context, context.agent_id):
        return ()
    commands: list[SlashCommandSpec] = []
    for item in _agent_skill_entries(skills, plugins, context.agent_id):
        if not item.name or not item.name[0].isalpha():
            # Skill resource IDs may begin with a digit, while slash commands
            # intentionally require a leading lowercase letter.
            continue
        commands.append(
            SlashCommandSpec(
                command=f"/{item.name}",
                title=item.name,
                description=item.description,
                icon="sparkles",
                arg_hint="<instruction>",
                arguments=(
                    SlashCommandArgumentSpec(
                        name="instruction",
                        value_type="text",
                        description="Task to complete with this Skill.",
                        required=True,
                    ),
                ),
                no_args_behavior="show_usage",
                lifecycle="agent_turn",
                order=80,
            )
        )
    return tuple(commands)


def _domain_spec(action_id, title, description, input_model, permission, *, risk="low", confirmation="never") -> ActionSpec:
    mutation_actions = {
        "app.connection.create",
        "app.connection.disable",
        "app.connection.enable",
        "app.connection.reauthorize",
        "app.connection.remove",
        "app.connection.update",
        "app.definition.install",
        "app.definition.remove",
        "app.definition.update",
        "app.starter.install",
        "mcp.create",
        "mcp.oauth.begin",
        "mcp.oauth.signout",
        "mcp.update",
        "plugin.hooks.trust",
        "plugin.hooks.untrust",
        "plugin.marketplace.source.create",
        "plugin.marketplace.source.refresh",
        "plugin.marketplace.source.remove",
        "plugin.marketplace.source.update",
    }
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
        operation="mutation" if action_id in mutation_actions else "read",
        projections=("cli", "desktop", "mobile"),
    )


def _call(operation):
    try:
        return operation()
    except ExtensionError as exc:
        raise_extension_failure(exc)


def _list(inventory: ExtensionRegistry, value: ExtensionListInput) -> dict[str, object]:
    return {"items": [item.to_payload() for item in inventory.list(kind=value.kind, agent_id=value.agent_id)]}


def _skill_command(
    skills: SkillManager,
    plugins: PluginManager,
    value: SkillCommandInput,
    *,
    context: ActionContext,
    agent_access: Callable[[ActionContext, str], bool],
) -> dict[str, object]:
    """Resolve only Skills frozen into the selected Agent's next Runtime."""
    if value.agent_id is None:
        raise ExtensionError("invalid_operation", "Select an Agent before using a Skill.")
    if not agent_access(context, value.agent_id):
        raise ExtensionError(
            "extension_not_found",
            "The selected Skill is not available to this Agent.",
        )
    entries = _agent_skill_entries(skills, plugins, value.agent_id)
    if value.skill_name is None:
        return {
            "items": [
                {"name": item.name, "description": item.description, "source": item.source}
                for item in entries
            ]
        }
    selected = next((item for item in entries if item.name == value.skill_name), None)
    if selected is None:
        raise ExtensionError(
            "extension_not_found",
            "The selected Skill is not available to this Agent.",
            details={"skillName": value.skill_name},
        )
    request = value.instruction.strip() or "Explain this Skill's purpose and ask what task I want to complete with it."
    return {
        "skill": {"name": selected.name, "description": selected.description, "source": selected.source},
        "startAgentTurn": {
            "text": (
                f'Use the installed Skill "{selected.name}" for this request. '
                f'Read it with read_skill before acting. User request: {request}'
            ),
            "skillName": selected.name,
        },
    }


def _agent_skill_entries(
    skills: SkillManager,
    plugins: PluginManager,
    agent_id: str,
) -> tuple[SkillSnapshotEntry, ...]:
    """Return one deterministic combined direct-and-Plugin Skill snapshot."""
    direct = skills.snapshot_for_agent(agent_id)
    plugin = plugins.snapshot_for_agent(agent_id).skills
    return tuple(sorted((*direct.skills, *plugin.skills), key=lambda item: item.name))


def _list_starters(
    starters: ExtensionStarterCatalog,
    value: ExtensionStarterListInput,
) -> dict[str, object]:
    """Return safe catalog entries plus deterministic aggregate counts."""
    items = starters.list(kind=value.kind, query=value.query)
    return {
        "items": [item.to_payload() for item in items],
        "counts": {
            kind: len(starters.list(kind=kind, query=value.query))
            for kind in ("plugin", "app", "mcp", "skill")
        },
    }


def _install_app_starter(
    starters: ExtensionStarterCatalog,
    apps: AppManager,
    value: ExtensionStarterIdentityInput,
) -> dict[str, object]:
    """Install one validated direct-App starter without trusting client JSON."""
    starter = starters.get(value.starter_id)
    if starter.kind != "app" or starter.install_mode != "direct_app":
        raise ExtensionError(
            "invalid_operation",
            "This starter is not a directly installable App definition.",
        )
    raw = deepcopy(starter.template.get("definition"))
    if not isinstance(raw, dict):
        raise ExtensionError("invalid_catalog", "The App starter definition is missing.")
    spec = raw.get("spec")
    if not isinstance(spec, dict):
        raise ExtensionError("invalid_catalog", "The App starter definition spec is missing.")
    spec["presentation"] = starter.presentation.model_dump(mode="json", by_alias=True)
    try:
        definition = AppDefinition.model_validate(raw)
    except Exception as exc:
        raise ExtensionError("invalid_catalog", "The App starter definition is invalid.") from exc
    try:
        current = apps.get_definition(definition.metadata.name)
    except ExtensionError as exc:
        if exc.code != "extension_not_found":
            raise
    else:
        if current.record != definition:
            raise ExtensionError(
                "revision_conflict",
                "An App definition with this identity is already installed from another source.",
            )
        return _versioned(current)
    return _versioned(apps.install_definition(definition, expected_revision=None))


def _begin_mcp_oauth(mcp: McpManager, oauth, value: McpOAuthInput) -> dict[str, object]:
    """Validate the persisted resource before starting its explicit OAuth flow."""
    from openppx.extensions.mcp_models import McpRemoteTransport

    record = mcp.get(value.server_id).record
    transport = record.spec.transport
    if not isinstance(transport, McpRemoteTransport) or transport.auth != "oauth":
        raise ExtensionError("invalid_operation", "This MCP server does not use OAuth.")
    if not value.callback_base:
        raise ExtensionError("invalid_oauth_callback", "An OAuth callback origin is required.")
    return oauth.begin(value.server_id, transport.url, value.callback_base)


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


def _remove_marketplace(manager, value: PluginMarketplaceIdentityInput) -> dict[str, object]:
    manager.remove(value.marketplace_id, expected_revision=value.expected_revision)
    return {"id": value.marketplace_id, "kind": "PluginMarketplaceSource", "removed": True}


def _test_mcp(
    mcp: McpManager,
    probe,
    health_store: ExtensionHealthStore,
    value: McpTestInput,
) -> dict[str, object]:
    """Run one live MCP probe after the inexpensive dependency check passes."""
    current = mcp.get(value.server_id)
    readiness = mcp.readiness(value.server_id)
    if not readiness.ready:
        result = _blocked_probe(
            kind="mcp",
            resource_id=value.server_id,
            revision=current.revision,
            issues=readiness.issues,
        )
        return _record_probe(health_store, result)
    result = _live_probe(
        probe,
        mcp.snapshot_for_probe(value.server_id),
        kind="mcp",
        resource_id=value.server_id,
        revision=current.revision,
    )
    return _record_probe(health_store, result)


def _test_app_connection(
    apps: AppManager,
    probe,
    health_store: ExtensionHealthStore,
    value: AppConnectionTestInput,
) -> dict[str, object]:
    """Probe one App execution adapter without changing Agent access."""
    current = apps.get_connection(value.connection_id)
    readiness = apps.readiness(value.connection_id)
    if not readiness.ready:
        result = _blocked_probe(
            kind="app_connection",
            resource_id=value.connection_id,
            revision=current.revision,
            issues=readiness.issues,
        )
        return _record_probe(health_store, result)
    if apps.execution_kind(value.connection_id) == "native":
        tools = apps.native_tools_for_probe(value.connection_id)
        names = sorted(
            {
                str(getattr(tool, "name", None) or getattr(tool, "__name__", type(tool).__name__))
                for tool in tools
            }
        )
        started = perf_counter()
        probe_result = asyncio.run(apps.probe_native_connection(value.connection_id))
        elapsed_ms = max(0, round((perf_counter() - started) * 1000))
        issues = ([] if probe_result.ready else [probe_result.issue or "connection_failed"])
        result = {
            "kind": "app_connection",
            "id": value.connection_id,
            "revision": current.revision,
            "checkedAt": _checked_at(),
            "ready": bool(names) and probe_result.ready,
            "status": "ok" if names and probe_result.ready else "error",
            "transport": "native",
            "elapsedMs": elapsed_ms,
            "attempts": 1,
            "toolCount": len(names),
            "toolNames": names,
            "issues": (["tool_projection_empty"] if not names else []) + issues,
            "errorKind": None if names and probe_result.ready else (
                "configuration" if not names else "authentication"
            ),
            "message": "" if probe_result.ready else (probe_result.issue or "connection_failed"),
        }
        return _record_probe(health_store, result)
    result = _live_probe(
        probe,
        apps.mcp_snapshot_for_probe(value.connection_id),
        kind="app_connection",
        resource_id=value.connection_id,
        revision=current.revision,
    )
    return _record_probe(health_store, result)


def _record_probe(
    health_store: ExtensionHealthStore,
    result: dict[str, object],
) -> dict[str, object]:
    """Persist one probe and include a compact durable summary in its response."""
    observation = health_store.record(result)
    return {**result, "health": health_store.summary(observation.kind, observation.resource_id)}


def _health_history(
    health_store: ExtensionHealthStore,
    value: ExtensionHealthHistoryInput,
) -> dict[str, object]:
    """Project recent observations plus stable last-success/failure facts."""
    return {
        "summary": health_store.summary(value.kind, value.extension_id),
        "items": [
            item.to_payload()
            for item in health_store.recent(value.kind, value.extension_id, limit=value.limit)
        ],
    }


def _live_probe(
    probe,
    snapshot,
    *,
    kind: str,
    resource_id: str,
    revision: str,
) -> dict[str, object]:
    """Normalize the shared ADK MCP probe into a client-safe Action result."""
    report = asyncio.run(probe.probe(snapshot))
    diagnostics = [item.code for item in report.diagnostics]
    result = report.results[0] if report.results else {}
    status = str(result.get("status", "blocked" if diagnostics else "error"))
    error_kind = str(result.get("error_kind", ""))
    issues = sorted(
        set(
            [*diagnostics]
            + ([f"connection_{error_kind}"] if error_kind else [])
            + (["connection_failed"] if status not in {"ok", "blocked"} and not error_kind else [])
        )
    )
    return {
        "kind": kind,
        "id": resource_id,
        "revision": revision,
        "checkedAt": _checked_at(),
        "ready": status == "ok" and not diagnostics,
        "status": status,
        "transport": str(result.get("transport", "unknown")),
        "elapsedMs": int(result.get("elapsed_ms", 0)),
        "attempts": int(result.get("attempts", 0)),
        "toolCount": int(result.get("tool_count", 0)),
        "toolNames": [str(item) for item in result.get("tool_names", [])],
        "issues": issues,
        "errorKind": error_kind or None,
        "message": str(result.get("error", "")),
    }


def _blocked_probe(
    *,
    kind: str,
    resource_id: str,
    revision: str,
    issues,
) -> dict[str, object]:
    """Return a stable non-exception result when local dependencies block a probe."""
    return {
        "kind": kind,
        "id": resource_id,
        "revision": revision,
        "checkedAt": _checked_at(),
        "ready": False,
        "status": "blocked",
        "transport": "unknown",
        "elapsedMs": 0,
        "attempts": 0,
        "toolCount": 0,
        "toolNames": [],
        "issues": list(issues),
        "errorKind": None,
        "message": "",
    }


def _checked_at() -> str:
    """Return an RFC 3339 UTC timestamp for a live probe observation."""
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


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
