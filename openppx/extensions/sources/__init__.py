"""Extension Source Adapter implementations."""

from .archive import LocalArchiveSourceAdapter
from .base import SourceAdapter, SourceLimits, StagedExtension, StagingStore, content_digest
from .catalog import CatalogArtifact, CatalogProvider, CatalogSourceAdapter
from .filesystem import BuiltinSourceAdapter, LocalDirectorySourceAdapter
from .git import GitSourceAdapter
from .npm import NpmSourceAdapter

__all__ = [
    "BuiltinSourceAdapter",
    "CatalogArtifact",
    "CatalogProvider",
    "CatalogSourceAdapter",
    "GitSourceAdapter",
    "LocalArchiveSourceAdapter",
    "LocalDirectorySourceAdapter",
    "NpmSourceAdapter",
    "SourceAdapter",
    "SourceLimits",
    "StagedExtension",
    "StagingStore",
    "content_digest",
]
