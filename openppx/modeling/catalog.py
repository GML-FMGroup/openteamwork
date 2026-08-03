"""Read-only Model Catalog projected from the existing Provider Registry."""

from __future__ import annotations

from dataclasses import dataclass

from openppx.core.provider_registry import ProviderSpec, find_provider_spec


@dataclass(frozen=True, slots=True)
class CatalogProvider:
    """Selection-relevant provider facts projected from ProviderSpec."""

    provider_id: str
    display_name: str
    runtime: str
    credential_required: bool


class ModelCatalog:
    """Provider lookup facade that does not duplicate provider identity data."""

    def get(self, provider_id: str) -> CatalogProvider | None:
        """Return selection-relevant facts for one registered provider."""
        spec = find_provider_spec(provider_id)
        if spec is None:
            return None
        return self._project(spec)

    @staticmethod
    def _project(spec: ProviderSpec) -> CatalogProvider:
        return CatalogProvider(
            provider_id=spec.name,
            display_name=spec.display_name,
            runtime=spec.runtime,
            credential_required=spec.is_oauth or bool(spec.api_key_env),
        )
