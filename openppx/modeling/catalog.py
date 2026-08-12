"""Read-only Model Catalog projected from the existing Provider Registry."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from openppx.core.codex_auth import default_codex_home
from openppx.core.provider import normalize_model_name
from openppx.core.provider_registry import ProviderSpec, find_provider_spec


_MAX_MODEL_CACHE_BYTES = 20_000_000


@dataclass(frozen=True, slots=True)
class CatalogProvider:
    """Selection-relevant provider facts projected from ProviderSpec."""

    provider_id: str
    display_name: str
    runtime: str
    credential_mode: Literal["api_key", "oauth", "none"]
    credential_required: bool
    default_model: str


@dataclass(frozen=True, slots=True)
class CatalogModel:
    """One safe model choice projected from a provider-owned catalog."""

    model_id: str
    display_name: str
    description: str
    default_reasoning_effort: str | None = None
    reasoning_efforts: tuple[str, ...] = ()
    context_window_tokens: int | None = None


@dataclass(frozen=True, slots=True)
class ModelCatalogSnapshot:
    """A provider model list plus whether it is authoritative for validation."""

    provider_id: str
    source: str
    authoritative: bool
    models: tuple[CatalogModel, ...]


class ModelCatalog:
    """Provider lookup facade that does not duplicate provider identity data."""

    def __init__(self, *, codex_home: Path | None = None) -> None:
        self.codex_home = (codex_home or default_codex_home()).expanduser()

    def get(self, provider_id: str) -> CatalogProvider | None:
        """Return selection-relevant facts for one registered provider."""
        spec = find_provider_spec(provider_id)
        if spec is None:
            return None
        return self._project(spec)

    def list(self) -> tuple[CatalogProvider, ...]:
        """Return the stable provider catalog in product display order."""
        from openppx.core.provider_registry import PROVIDERS

        return tuple(self._project(spec) for spec in PROVIDERS)

    def list_models(self, provider_id: str) -> ModelCatalogSnapshot:
        """Return safe model choices from the provider's Node-local catalog."""
        provider = self.get(provider_id)
        if provider is None:
            return ModelCatalogSnapshot(provider_id, "unknown", False, ())
        if provider_id == "openai_codex":
            models = self._read_codex_models()
            if models:
                return ModelCatalogSnapshot(provider_id, "codex_cli", True, models)
        return ModelCatalogSnapshot(
            provider_id,
            "provider_default",
            False,
            (
                CatalogModel(
                    model_id=provider.default_model,
                    display_name=provider.default_model.split("/", 1)[-1],
                    description=f"Default model for {provider.display_name}.",
                    context_window_tokens=_litellm_context_window_tokens(
                        provider_id,
                        provider.default_model,
                    ),
                ),
            ),
        )

    def context_window_tokens(self, provider_id: str, model_id: str) -> int | None:
        """Return a catalog context window when the selected model is known."""
        normalized = model_id.strip()
        for model in self.list_models(provider_id).models:
            if model.model_id == normalized:
                if model.context_window_tokens is not None:
                    return model.context_window_tokens
                break
        return _litellm_context_window_tokens(provider_id, normalized)

    def _read_codex_models(self) -> tuple[CatalogModel, ...]:
        path = self.codex_home / "models_cache.json"
        try:
            if not path.is_file() or path.stat().st_size > _MAX_MODEL_CACHE_BYTES:
                return ()
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return ()
        items = document.get("models") if isinstance(document, dict) else None
        if not isinstance(items, list):
            return ()
        models: list[CatalogModel] = []
        seen: set[str] = set()
        for item in items[:200]:
            if not isinstance(item, dict) or item.get("supported_in_api") is False:
                continue
            if item.get("visibility") not in {None, "list"}:
                continue
            slug = str(item.get("slug") or "").strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            efforts = tuple(
                str(level.get("effort"))
                for level in item.get("supported_reasoning_levels", [])
                if isinstance(level, dict) and level.get("effort")
            )
            models.append(
                CatalogModel(
                    model_id=f"openai-codex/{slug}",
                    display_name=str(item.get("display_name") or slug)[:120],
                    description=str(item.get("description") or "")[:500],
                    default_reasoning_effort=str(item.get("default_reasoning_level") or "") or None,
                    reasoning_efforts=efforts,
                    context_window_tokens=_positive_int(item.get("context_window")),
                )
            )
        return tuple(models)

    @staticmethod
    def _project(spec: ProviderSpec) -> CatalogProvider:
        credential_mode: Literal["api_key", "oauth", "none"]
        if spec.is_oauth:
            credential_mode = "oauth"
        elif spec.api_key_env:
            credential_mode = "api_key"
        else:
            credential_mode = "none"
        return CatalogProvider(
            provider_id=spec.name,
            display_name=spec.display_name,
            runtime=spec.runtime,
            credential_mode=credential_mode,
            credential_required=credential_mode == "api_key",
            default_model=spec.default_model,
        )


def _positive_int(value: object) -> int | None:
    """Project one positive integer without accepting booleans or coercion."""
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def _litellm_context_window_tokens(provider_id: str, model_id: str) -> int | None:
    """Read bundled LiteLLM metadata without making a provider request."""
    try:
        from litellm import model_cost
    except ImportError:
        return None
    normalized = normalize_model_name(provider_id, model_id)
    for candidate in dict.fromkeys((normalized, model_id)):
        info = model_cost.get(candidate)
        if isinstance(info, dict):
            value = _positive_int(info.get("max_input_tokens"))
            if value is not None:
                return value
    return None
