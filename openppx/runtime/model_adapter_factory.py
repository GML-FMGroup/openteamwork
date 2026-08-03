"""Explicit Model Profile to Google ADK model adapter boundary."""

from __future__ import annotations

from typing import Any

from openppx.config.secrets import SecretStore
from openppx.core.provider import normalize_model_name, provider_default_api_base, validate_provider_runtime
from openppx.core.provider_registry import RUNTIME_CODEX, RUNTIME_GOOGLE, RUNTIME_LITELLM, find_provider_spec
from openppx.modeling import ModelResolution


class ModelAdapterError(RuntimeError):
    """Raised when a selected, ready profile cannot become an ADK model."""


class ModelAdapterFactory:
    """Build ADK adapters from resolved profiles and protected Secrets.

    This Runtime boundary is the only place allowed to reveal a profile
    credential. It passes credentials directly to SDK objects and never
    projects them into process environment variables.
    """

    def __init__(self, secrets: SecretStore) -> None:
        self._secrets = secrets

    def build(self, resolution: ModelResolution) -> Any:
        """Return the concrete ADK model for one selected profile."""
        provider = resolution.provider
        issue = validate_provider_runtime(provider)
        if issue:
            raise ModelAdapterError(issue)
        spec = find_provider_spec(provider)
        if spec is None:
            raise ModelAdapterError(f"Model Provider '{provider}' is not registered.")

        model_name = normalize_model_name(provider, resolution.model)
        credential = None
        if resolution.profile.spec.credential is not None:
            credential = self._secrets.resolve(resolution.profile.spec.credential).reveal()

        if spec.runtime == RUNTIME_GOOGLE:
            if not credential:
                raise ModelAdapterError(f"Model Profile '{resolution.profile_id}' has no usable credential.")
            return _build_google_model(model_name, credential)

        if spec.runtime == RUNTIME_LITELLM:
            from google.adk.models.lite_llm import LiteLlm

            kwargs: dict[str, Any] = {"drop_params": True}
            if credential:
                kwargs["api_key"] = credential
            api_base = provider_default_api_base(provider)
            if api_base:
                kwargs["api_base"] = api_base
            return LiteLlm(model=model_name, **kwargs)

        if spec.runtime == RUNTIME_CODEX:
            from openppx.core.openai_codex_llm import OpenAICodexLlm

            return OpenAICodexLlm(
                model=model_name,
                codex_url=provider_default_api_base(provider),
            )

        raise ModelAdapterError(f"Model Provider '{provider}' has no supported ADK adapter.")


def _build_google_model(model_name: str, api_key: str) -> Any:
    """Build native ADK Gemini with an explicitly credentialed GenAI client."""
    from google.adk.models.google_llm import Gemini
    from google.genai import Client

    model = Gemini(model=model_name)
    # The default cached property reads ambient credentials. Seeding it keeps
    # credential provenance explicit and snapshot-scoped.
    object.__setattr__(model, "api_client", Client(api_key=api_key))
    return model


__all__ = ["ModelAdapterError", "ModelAdapterFactory"]
