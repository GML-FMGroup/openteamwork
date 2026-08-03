"""Model Profile, catalog, readiness, and selection boundaries."""

from .catalog import CatalogProvider, ModelCatalog
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
    "CatalogProvider",
    "ModelCatalog",
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
    "export_model_profile_schema",
]
