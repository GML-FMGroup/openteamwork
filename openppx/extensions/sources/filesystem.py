"""Builtin and local filesystem Source Adapters."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping

from openppx.extensions.errors import ExtensionError
from openppx.extensions.models import ExtensionSourceRef

from .base import StagedExtension, StagingStore, stage_directory


def _require_type(reference: ExtensionSourceRef, expected: str) -> None:
    if reference.type != expected:
        raise ExtensionError("invalid_source", "Source type does not match the selected adapter.")


class BuiltinSourceAdapter:
    """Stage only explicitly registered, trusted package resources."""

    source_type = "builtin"

    def __init__(self, roots: Mapping[str, Path]) -> None:
        self._roots = {identifier: root.expanduser().resolve(strict=False) for identifier, root in roots.items()}

    def stage(self, reference: ExtensionSourceRef, store: StagingStore) -> StagedExtension:
        """Copy one registered builtin Skill into controlled staging."""
        _require_type(reference, self.source_type)
        source = self._roots.get(reference.locator)
        if source is None:
            raise ExtensionError("invalid_source", "Builtin source is not registered.")
        return stage_directory(
            source,
            store,
            identity_type="builtin",
            locator=f"builtin:{reference.locator}",
            version=reference.version or "builtin",
        )


class LocalDirectorySourceAdapter:
    """Copy one local directory without retaining source-path execution."""

    source_type = "local_directory"

    def stage(self, reference: ExtensionSourceRef, store: StagingStore) -> StagedExtension:
        """Validate and copy one local directory into controlled staging."""
        _require_type(reference, self.source_type)
        source = Path(reference.locator).expanduser()
        return stage_directory(
            source,
            store,
            identity_type="local_directory",
            locator=f"local-directory:{source.name or 'skill'}",
            version=reference.version or "local",
        )


__all__ = ["BuiltinSourceAdapter", "LocalDirectorySourceAdapter"]
