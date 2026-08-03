"""In-process OpenPPX application façade shared by all control surfaces."""

from __future__ import annotations

from collections.abc import Mapping

from openppx.actions import ActionContext, ActionExecutor, ActionOutcome, ActionRegistry
from openppx.config import ConfigService, FilesystemConfigRepository
from openppx.modeling import ModelProfileRepository, ModelProfileSelector

from .config_actions import register_config_actions
from .model_actions import register_model_actions
from .system_actions import register_system_actions


class ControlPlaneApplication:
    """Own one transport-independent application and its complete Action catalog."""

    def __init__(
        self,
        *,
        config_repository: FilesystemConfigRepository,
        config_service: ConfigService,
        profile_repository: ModelProfileRepository,
        model_selector: ModelProfileSelector,
        product_version: str,
    ) -> None:
        self.config_repository = config_repository
        self.config_service = config_service
        self.profile_repository = profile_repository
        self.model_selector = model_selector
        self.product_version = product_version
        registry = ActionRegistry()
        register_config_actions(registry, config_repository, config_service)
        register_model_actions(registry, profile_repository, model_selector, config_repository)
        register_system_actions(
            registry,
            config_repository,
            product_version=product_version,
            catalog_provider=lambda context, namespace: registry.catalog(context, namespace=namespace),
        )
        self.registry = registry
        self.executor = ActionExecutor(registry)

    def status(self, context: ActionContext) -> ActionOutcome:
        """Return system readiness through the same Action path used by clients."""
        return self.invoke("system.status", {}, context)

    def catalog(self, context: ActionContext, *, namespace: str | None = None) -> ActionOutcome:
        """Return the caller-aware catalog through `system.help`."""
        return self.invoke("system.help", {"namespace": namespace}, context)

    def invoke(
        self,
        action_id: str,
        raw_input: Mapping[str, object],
        context: ActionContext,
    ) -> ActionOutcome:
        """Invoke one product Action without any transport or renderer dependency."""
        return self.executor.execute(action_id, raw_input, context)
