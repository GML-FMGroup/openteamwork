"""Read-only Model Catalog projected from the existing Provider Registry."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from openppx.core.provider_registry import ProviderSpec, find_provider_spec


@dataclass(frozen=True, slots=True)
class CatalogProvider:
    """Selection-relevant provider facts projected from ProviderSpec."""

    provider_id: str
    display_name: str
    runtime: str
    credential_mode: Literal["api_key", "oauth", "none"]
    credential_required: bool
    default_model: str


class ModelCatalog:
    """Provider lookup facade that does not duplicate provider identity data."""

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
