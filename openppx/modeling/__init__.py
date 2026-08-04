"""Model Profile, catalog, readiness, and selection boundaries."""

from .catalog import CatalogModel, CatalogProvider, ModelCatalog, ModelCatalogSnapshot
from .provider_access import ProviderAccessError, ProviderAccessService
from .lifecycle import ModelProfileLifecycleError, ModelProfileLifecycleService, ModelProfileSaveResult
from .profiles import CapabilityId, ModelCapability, ModelProfile, ModelProfileSpec, ProviderId
from .repository import ModelProfileRepository
from .schema import export_model_profile_schema
from .selection import (
    ModelProfileSelector,
    ModelRequirements,
    ModelResolution,
    ModelSelectionAttempt,
    ModelSelectionError,
)

__all__ = [
    "CapabilityId",
    "CatalogModel",
    "CatalogProvider",
    "ModelCatalog",
    "ModelCatalogSnapshot",
    "ModelCapability",
    "ModelProfile",
    "ModelProfileLifecycleError",
    "ModelProfileLifecycleService",
    "ModelProfileRepository",
    "ModelProfileSelector",
    "ModelProfileSpec",
    "ModelProfileSaveResult",
    "ModelRequirements",
    "ModelResolution",
    "ModelSelectionAttempt",
    "ModelSelectionError",
    "ProviderId",
    "ProviderAccessError",
    "ProviderAccessService",
    "export_model_profile_schema",
]
