"""In-process OpenPPX application façade shared by all control surfaces."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace

from openppx.actions import ActionContext, ActionExecutor, ActionOutcome, ActionRegistry
from openppx.actions import ActionError, ActionFailure, SlashCommandError, SlashInvocationContext
from openppx.agents import AgentLifecycleError, AgentLifecycleService
from openppx.config import ConfigError, ConfigService, FilesystemConfigRepository
from openppx.modeling import ModelProfileLifecycleService, ModelProfileRepository, ModelProfileSelector, ProviderAccessService
from openppx.setup import SetupService
from openppx.governance import ActionAuditStore, ActionPolicy
from openppx.permissions import PermissionAuditStore
from openppx.runtime.session_metadata_store import SessionMetadataStore
from openppx.runtime.goal_store import GoalStore
from openppx.extensions import default_extension_starter_catalog

from .config_actions import register_config_actions
from .agent_actions import register_agent_actions
from .automation_actions import register_automation_actions
from .extension_actions import register_extension_actions
from .goal_actions import register_goal_actions
from .make_skill_actions import register_make_skill_actions
from .model_actions import register_model_actions
from .operations_actions import register_operations_actions
from .permission_actions import register_permission_actions
from .runtime_actions import register_runtime_actions
from .system_actions import register_system_actions
from .setup_actions import register_setup_actions, register_setup_runtime_actions
from .input_models import SlashCommandInvokeInput


class ControlPlaneApplication:
    """Own one transport-independent application and its complete Action catalog."""

    def __init__(
        self,
        *,
        config_repository: FilesystemConfigRepository,
        config_service: ConfigService,
        profile_repository: ModelProfileRepository,
        model_profile_lifecycle: ModelProfileLifecycleService,
        model_selector: ModelProfileSelector,
        provider_access: ProviderAccessService,
        agent_lifecycle: AgentLifecycleService,
        setup_service: SetupService,
        audit_store: ActionAuditStore,
        product_version: str,
    ) -> None:
        self.config_repository = config_repository
        self.config_service = config_service
        self.profile_repository = profile_repository
        self.model_profile_lifecycle = model_profile_lifecycle
        self.model_selector = model_selector
        self.provider_access = provider_access
        self.agent_lifecycle = agent_lifecycle
        self.setup_service = setup_service
        self.audit_store = audit_store
        self.permission_audit = PermissionAuditStore(
            config_repository.paths.node_root / "database" / "permission_audit.db"
        )
        self.product_version = product_version
        self.runtime_supervisor = None
        self.extension_registry = None
        self.mcp_oauth_service = None
        self.operations_service = None
        self.automation_service = None
        self.make_skill_service = None
        self.session_metadata = SessionMetadataStore(
            config_repository.paths.node_root / "database" / "sessions.db"
        )
        self.goal_store = GoalStore(
            db_path=config_repository.paths.node_root / "database" / "goals.db"
        )
        registry = ActionRegistry()
        executor = ActionExecutor(registry, policy=ActionPolicy(), audit=audit_store)
        register_config_actions(registry, config_repository, config_service)
        register_permission_actions(registry, self.permission_audit)
        register_agent_actions(registry, agent_lifecycle)
        register_model_actions(
            registry,
            profile_repository,
            model_profile_lifecycle,
            model_selector,
            config_repository,
            provider_access,
            session_metadata=self.session_metadata,
            runtime_provider=lambda: self.runtime_supervisor,
        )
        register_goal_actions(
            registry,
            self.goal_store,
            config_repository,
            profile_repository,
            runtime_provider=lambda: self.runtime_supervisor,
        )
        register_setup_actions(registry, setup_service)
        register_system_actions(
            registry,
            config_repository,
            product_version=product_version,
            catalog_provider=lambda context, namespace, projection: registry.catalog(
                context,
                namespace=namespace,
                projection=projection,
            ),
            command_invoker=lambda context, input_data: self._invoke_slash_command(
                registry,
                executor,
                context,
                input_data,
            ),
        )
        self.registry = registry
        self.executor = executor

    @staticmethod
    def _invoke_slash_command(
        registry: ActionRegistry,
        executor: ActionExecutor,
        context: ActionContext,
        input_data: SlashCommandInvokeInput,
    ) -> dict[str, object]:
        """Resolve one command projection and execute its target Action once."""
        command_context = replace(
            context,
            agent_id=input_data.agent_id,
            session_id=input_data.session_id,
            run_id=input_data.run_id,
        )
        try:
            resolved = registry.resolve_slash(
                input_data.raw_command,
                context=command_context,
            )
            reason = registry.availability_reason(resolved.registered, command_context)
            if reason == "capability_required":
                raise ActionFailure(ActionError("capability_required", "The caller lacks a required capability."))
            if reason == "permission_denied":
                raise ActionFailure(ActionError("permission_denied", "The caller lacks the required permission."))
            if reason is not None:
                raise ActionFailure(
                    ActionError(
                        "action_unavailable",
                        "The requested Action is not currently available.",
                        details={"reason": reason},
                    )
                )
            adapter = resolved.registered.slash_input
            if adapter is None:  # pragma: no cover - rejected during registration
                raise SlashCommandError("command_not_available", "The slash command has no input adapter.")
            target_input = adapter(
                resolved.command,
                resolved.args,
                SlashInvocationContext(
                    user_id=input_data.user_id,
                    agent_id=input_data.agent_id,
                    session_id=input_data.session_id,
                    run_id=input_data.run_id,
                ),
            )
        except SlashCommandError as exc:
            raise ActionFailure(ActionError(exc.code, str(exc))) from exc
        outcome = executor.execute(
            resolved.registered.spec.action_id,
            target_input,
            command_context,
        )
        if not outcome.ok:
            assert outcome.error is not None
            raise ActionFailure(outcome.error)
        result = outcome.data or {}
        lifecycle = resolved.command.lifecycle
        start_agent_turn = result.get("startAgentTurn") if isinstance(result, dict) else None
        if (
            isinstance(start_agent_turn, dict)
            and str(start_agent_turn.get("text") or "").strip()
        ):
            # Mixed commands such as /goal use side-channel behavior for status
            # operations, but creating a Goal must enter the normal ADK turn.
            lifecycle = "agent_turn"
        return {
            "command": resolved.command.command,
            "lifecycle": lifecycle,
            "targetActionId": outcome.action_id,
            "result": result,
        }

    def attach_extensions(
        self,
        registry,
        *,
        skills,
        mcp,
        apps,
        plugins,
        mcp_probe,
        plugin_marketplaces=None,
        starters=None,
        health_store=None,
    ) -> None:
        """Attach the Node-owned Extension graph and register its shared Actions."""
        if self.extension_registry is not None:
            raise RuntimeError("An Extension Registry is already attached.")
        if plugin_marketplaces is None:
            from openppx.extensions import PluginMarketplaceManager

            plugin_marketplaces = PluginMarketplaceManager(self.config_repository.paths.node_root)
        if health_store is None:
            from openppx.extensions import ExtensionHealthStore

            health_store = ExtensionHealthStore(
                self.config_repository.paths.node_root / "database" / "extension_health.db"
            )
        register_extension_actions(
            self.registry,
            registry,
            skills=skills,
            mcp=mcp,
            apps=apps,
            plugins=plugins,
            plugin_marketplaces=plugin_marketplaces,
            mcp_probe=mcp_probe,
            starters=starters or default_extension_starter_catalog(),
            mcp_oauth=mcp_probe.oauth_service,
            health_store=health_store,
            agent_access=self._can_access_agent,
        )
        self.extension_registry = registry
        self.mcp_oauth_service = mcp_probe.oauth_service

    def _can_access_agent(self, context: ActionContext, agent_id: str) -> bool:
        """Return whether one authenticated caller may target an enabled Agent."""
        if context.principal_id is None:
            return True
        try:
            items = self.agent_lifecycle.list()
        except (AgentLifecycleError, ConfigError):
            return False
        item = next(
            (
                candidate
                for candidate in items
                if candidate.agent.document.metadata.name == agent_id
            ),
            None,
        )
        if item is None:
            return False
        if not item.enabled:
            return False
        return (
            context.privilege_level == "root"
            or item.agent.document.spec.owner_principal_id == context.principal_id
        )

    def attach_runtime(self, supervisor, *, task_controller=None, session_metadata=None) -> None:
        """Attach the one Node Runtime Supervisor and register its Actions."""
        if self.runtime_supervisor is not None:
            raise RuntimeError("A Runtime Supervisor is already attached.")
        register_runtime_actions(
            self.registry,
            supervisor,
            task_controller=task_controller,
            session_metadata=session_metadata or self.session_metadata,
        )
        register_setup_runtime_actions(self.registry, self.setup_service, supervisor)
        self.runtime_supervisor = supervisor

    def attach_skill_authoring(self, service) -> None:
        """Attach conversation Skill authoring after Runtime and Extensions exist."""
        if self.make_skill_service is not None:
            raise RuntimeError("A Skill authoring service is already attached.")
        register_make_skill_actions(self.registry, service)
        self.make_skill_service = service

    def attach_operations(self, service) -> None:
        """Attach the one Node-owned Operations facade and its Actions."""
        if self.operations_service is not None:
            raise RuntimeError("An Operations Service is already attached.")
        register_operations_actions(self.registry, service)
        self.operations_service = service

    def attach_automations(self, service) -> None:
        """Attach the formal User Automation domain after scheduler composition."""
        if self.automation_service is not None:
            raise RuntimeError("An Automation Service is already attached.")
        register_automation_actions(self.registry, service)
        self.automation_service = service

    def status(self, context: ActionContext) -> ActionOutcome:
        """Return system readiness through the same Action path used by clients."""
        return self.invoke("system.status", {}, context)

    def catalog(
        self,
        context: ActionContext,
        *,
        namespace: str | None = None,
        projection: str | None = None,
    ) -> ActionOutcome:
        """Return the caller-aware catalog through `system.help`."""
        return self.invoke(
            "system.help",
            {"namespace": namespace, "projection": projection},
            context,
        )

    def invoke(
        self,
        action_id: str,
        raw_input: Mapping[str, object],
        context: ActionContext,
    ) -> ActionOutcome:
        """Invoke one product Action without any transport or renderer dependency."""
        return self.executor.execute(action_id, raw_input, context)

    def close(self) -> None:
        """Release Node-owned control-plane background resources."""
        self.provider_access.close()
