"""Explicit dependency assembly for the in-process Control Plane."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from openppx.config import ConfigService, FilesystemConfigRepository, SecretStore, SystemCredentialSecretStore
from openppx.modeling import ModelCatalog, ModelProfileRepository, ModelProfileSelector
from openppx.setup import SetupService
from openppx.governance import ActionAuditStore

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
    secrets = secret_store or SystemCredentialSecretStore()
    catalog = ModelCatalog()
    selector = ModelProfileSelector(
        profile_repository,
        catalog,
        secrets,
    )
    config_service = ConfigService(config_repository, profile_repository, selector)
    setup_service = SetupService(
        config_repository,
        config_service,
        profile_repository,
        catalog,
        secrets,
    )
    audit_store = ActionAuditStore(node_root / "database" / "audit.db")
    return ControlPlaneApplication(
        config_repository=config_repository,
        config_service=config_service,
        profile_repository=profile_repository,
        model_selector=selector,
        setup_service=setup_service,
        audit_store=audit_store,
        product_version=product_version or _product_version(),
    )


def _product_version() -> str:
    try:
        return version("openppx")
    except PackageNotFoundError:
        return "unknown"
