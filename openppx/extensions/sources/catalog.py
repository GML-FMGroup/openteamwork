"""Injected third-party Catalog Source Adapter."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from openppx.extensions.errors import ExtensionError
from openppx.extensions.models import ExtensionSourceIdentity, ExtensionSourceRef

from .archive import LocalArchiveSourceAdapter
from .base import StagedExtension, StagingStore
from .filesystem import _require_type


@dataclass(frozen=True, slots=True)
class CatalogArtifact:
    """Pinned archive returned by one Catalog provider implementation."""

    archive_path: Path
    version: str
    revision: str
    digest: str


class CatalogProvider(Protocol):
    """Fetch a fixed Catalog artifact without controlling installation."""

    def fetch(self, extension_id: str, version: str | None) -> CatalogArtifact: ...


class CatalogSourceAdapter:
    """Verify a provider artifact before exposing it as staged content."""

    source_type = "catalog"

    def __init__(self, provider_id: str, provider: CatalogProvider) -> None:
        self.provider_id = provider_id
        self.provider = provider

    def stage(self, reference: ExtensionSourceRef, store: StagingStore) -> StagedExtension:
        """Fetch a pinned Catalog version and verify its declared digest."""
        _require_type(reference, self.source_type)
        if reference.provider != self.provider_id or not reference.version:
            raise ExtensionError("invalid_source", "Catalog source requires a known provider and fixed version.")
        try:
            artifact = self.provider.fetch(reference.locator, reference.version)
        except ExtensionError:
            raise
        except Exception as exc:
            raise ExtensionError("invalid_source", "Catalog source is temporarily unavailable.") from exc
        if artifact.version != reference.version:
            raise ExtensionError("invalid_source", "Catalog returned a different version.")
        staged = LocalArchiveSourceAdapter().stage(
            ExtensionSourceRef(
                type="local_archive",
                locator=str(artifact.archive_path),
                version=artifact.version,
            ),
            store,
        )
        if staged.digest != artifact.digest:
            staged.cleanup()
            raise ExtensionError("digest_mismatch", "Catalog artifact digest does not match its metadata.")
        identity = ExtensionSourceIdentity(
            type="catalog",
            locator=f"catalog:{self.provider_id}/{reference.locator}",
            version=artifact.version,
            revision=artifact.revision,
            digest=artifact.digest,
        )
        return StagedExtension(
            content_root=staged.content_root,
            source=identity,
            digest=staged.digest,
            file_count=staged.file_count,
            size_bytes=staged.size_bytes,
        )


__all__ = ["CatalogArtifact", "CatalogProvider", "CatalogSourceAdapter"]
