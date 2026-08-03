"""In-process OpenPPX application façade shared by all control surfaces."""

from __future__ import annotations

from collections.abc import Mapping

from openppx.actions import ActionContext, ActionExecutor, ActionOutcome, ActionRegistry
from openppx.actions import ActionError, ActionFailure, SlashCommandError, SlashInvocationContext
from openppx.config import ConfigService, FilesystemConfigRepository
from openppx.modeling import ModelProfileRepository, ModelProfileSelector
from openppx.setup import SetupService
from openppx.governance import ActionAuditStore, ActionPolicy

from .config_actions import register_config_actions
from .extension_actions import register_extension_actions
from .model_actions import register_model_actions
from .operations_actions import register_operations_actions
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
        model_selector: ModelProfileSelector,
        setup_service: SetupService,
        audit_store: ActionAuditStore,
        product_version: str,
    ) -> None:
        self.config_repository = config_repository
        self.config_service = config_service
        self.profile_repository = profile_repository
        self.model_selector = model_selector
        self.setup_service = setup_service
        self.audit_store = audit_store
        self.product_version = product_version
        registry = ActionRegistry()
        executor = ActionExecutor(registry, policy=ActionPolicy(), audit=audit_store)
        register_config_actions(registry, config_repository, config_service)
        register_model_actions(registry, profile_repository, model_selector, config_repository)
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
        self.runtime_supervisor = None
        self.extension_registry = None
        self.operations_service = None

    @staticmethod
    def _invoke_slash_command(
        registry: ActionRegistry,
        executor: ActionExecutor,
        context: ActionContext,
        input_data: SlashCommandInvokeInput,
    ) -> dict[str, object]:
        """Resolve one command projection and execute its target Action once."""
        try:
            resolved = registry.resolve_slash(input_data.raw_command)
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
            ActionContext(
                request_id=context.request_id,
                correlation_id=context.correlation_id,
                actor_id=context.actor_id,
                capabilities=context.capabilities,
                permissions=context.permissions,
                confirmed=context.confirmed,
                node_id=context.node_id,
                agent_id=input_data.agent_id,
                session_id=input_data.session_id,
                run_id=input_data.run_id,
                task_id=context.task_id,
            ),
        )
        if not outcome.ok:
            assert outcome.error is not None
            raise ActionFailure(outcome.error)
        return {
            "command": resolved.command.command,
            "lifecycle": resolved.command.lifecycle,
            "targetActionId": outcome.action_id,
            "result": outcome.data or {},
        }

    def attach_extensions(
        self,
        registry,
        *,
        skills,
        mcp,
        apps,
        plugins,
    ) -> None:
        """Attach the Node-owned Extension graph and register its shared Actions."""
        if self.extension_registry is not None:
            raise RuntimeError("An Extension Registry is already attached.")
        register_extension_actions(
            self.registry,
            registry,
            skills=skills,
            mcp=mcp,
            apps=apps,
            plugins=plugins,
        )
        self.extension_registry = registry

    def attach_runtime(self, supervisor, *, task_controller=None) -> None:
        """Attach the one Node Runtime Supervisor and register its Actions."""
        if self.runtime_supervisor is not None:
            raise RuntimeError("A Runtime Supervisor is already attached.")
        register_runtime_actions(self.registry, supervisor, task_controller=task_controller)
        register_setup_runtime_actions(self.registry, self.setup_service, supervisor)
        self.runtime_supervisor = supervisor

    def attach_operations(self, service) -> None:
        """Attach the one Node-owned Operations facade and its Actions."""
        if self.operations_service is not None:
            raise RuntimeError("An Operations Service is already attached.")
        register_operations_actions(self.registry, service)
        self.operations_service = service

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
