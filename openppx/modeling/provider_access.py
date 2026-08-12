"""Node-owned model catalog and provider authentication application service."""

from __future__ import annotations

from pathlib import Path

from openppx.core.codex_auth import (
    CodexAuthError,
    CodexCliTokenStorage,
    CodexDeviceLoginManager,
    get_codex_token,
    project_codex_auth_status,
)

from .catalog import ModelCatalog


class ProviderAccessError(RuntimeError):
    """Stable provider access error suitable for an Action boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


class ProviderAccessService:
    """Expose provider-owned authentication and model catalogs to clients."""

    def __init__(
        self,
        catalog: ModelCatalog,
        *,
        codex_home: Path | None = None,
        codex_storage: CodexCliTokenStorage | None = None,
        codex_login: CodexDeviceLoginManager | None = None,
    ) -> None:
        self.catalog = catalog
        self.codex_storage = codex_storage or CodexCliTokenStorage(codex_home)
        self.codex_login = codex_login or CodexDeviceLoginManager(self.codex_storage)

    def list_models(self, provider_id: str) -> dict[str, object]:
        """Return safe model choices for one registered provider."""
        provider = self.catalog.get(provider_id)
        if provider is None:
            raise ProviderAccessError("provider_not_found", "The selected model provider is not registered.")
        snapshot = self.catalog.list_models(provider_id)
        return {
            "providerId": provider_id,
            "source": snapshot.source,
            "authoritative": snapshot.authoritative,
            "defaultModel": provider.default_model,
            "items": [
                {
                    "id": item.model_id,
                    "displayName": item.display_name,
                    "description": item.description,
                    "defaultReasoningEffort": item.default_reasoning_effort,
                    "reasoningEfforts": list(item.reasoning_efforts),
                    "contextWindowTokens": item.context_window_tokens,
                }
                for item in snapshot.models
            ],
        }

    def auth_status(self, provider_id: str) -> dict[str, object]:
        """Return secret-free authentication state for an OAuth provider."""
        self._require_codex(provider_id)
        return project_codex_auth_status(self.codex_storage, self.codex_login.status())

    def begin_auth(self, provider_id: str) -> dict[str, object]:
        """Begin a Node-side device-code login usable from local or LAN clients."""
        self._require_codex(provider_id)
        try:
            session = self.codex_login.begin()
        except CodexAuthError as exc:
            raise ProviderAccessError(exc.code, str(exc)) from None
        return project_codex_auth_status(self.codex_storage, session)

    def refresh_auth(self, provider_id: str) -> dict[str, object]:
        """Reconcile the newest Codex login and verify token refreshability."""
        self._require_codex(provider_id)
        try:
            get_codex_token(storage=self.codex_storage)
        except CodexAuthError as exc:
            raise ProviderAccessError(exc.code, str(exc)) from None
        return self.auth_status(provider_id)

    def close(self) -> None:
        """Stop a pending provider login owned by this service."""
        self.codex_login.close()

    def _require_codex(self, provider_id: str) -> None:
        provider = self.catalog.get(provider_id)
        if provider is None:
            raise ProviderAccessError("provider_not_found", "The selected model provider is not registered.")
        if provider_id != "openai_codex":
            raise ProviderAccessError(
                "provider_auth_not_supported",
                "Interactive authentication is not available for this provider.",
            )
