"""Model Profile, catalog, readiness, and selection boundaries."""

from .catalog import CatalogModel, CatalogProvider, ModelCatalog, ModelCatalogSnapshot
from .provider_access import ProviderAccessError, ProviderAccessService
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
    "ModelProfileRepository",
    "ModelProfileSelector",
    "ModelProfileSpec",
    "ModelRequirements",
    "ModelResolution",
    "ModelSelectionAttempt",
    "ModelSelectionError",
    "ProviderId",
    "ProviderAccessError",
    "ProviderAccessService",
    "export_model_profile_schema",
]
