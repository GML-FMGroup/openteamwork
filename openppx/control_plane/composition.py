"""Explicit dependency assembly for the in-process Control Plane."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from openppx.config import ConfigService, FilesystemConfigRepository, SecretStore, SystemCredentialSecretStore
from openppx.modeling import ModelCatalog, ModelProfileRepository, ModelProfileSelector

from .application import ControlPlaneApplication


def build_control_plane(
    node_root: Path,
    *,
    secret_store: SecretStore | None = None,
    product_version: str | None = None,
) -> ControlPlaneApplication:
    """Assemble the final in-process kernel from one explicit Node root."""
    config_repository = FilesystemConfigRepository(node_root)
    profile_repository = ModelProfileRepository(node_root)
    selector = ModelProfileSelector(
        profile_repository,
        ModelCatalog(),
        secret_store or SystemCredentialSecretStore(),
    )
    config_service = ConfigService(config_repository, profile_repository, selector)
    return ControlPlaneApplication(
        config_repository=config_repository,
        config_service=config_service,
        profile_repository=profile_repository,
        model_selector=selector,
        product_version=product_version or _product_version(),
    )


def _product_version() -> str:
    try:
        return version("openppx")
    except PackageNotFoundError:
        return "unknown"
