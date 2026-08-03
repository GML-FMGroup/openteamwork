"""Extension Source Adapter implementations."""

from .archive import LocalArchiveSourceAdapter
from .base import SourceAdapter, SourceLimits, StagedExtension, StagingStore, content_digest
from .catalog import CatalogArtifact, CatalogProvider, CatalogSourceAdapter
from .filesystem import BuiltinSourceAdapter, LocalDirectorySourceAdapter
from .git import GitSourceAdapter

__all__ = [
    "BuiltinSourceAdapter",
    "CatalogArtifact",
    "CatalogProvider",
    "CatalogSourceAdapter",
    "GitSourceAdapter",
    "LocalArchiveSourceAdapter",
    "LocalDirectorySourceAdapter",
    "SourceAdapter",
    "SourceLimits",
    "StagedExtension",
    "StagingStore",
    "content_digest",
]
