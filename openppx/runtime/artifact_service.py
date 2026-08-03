"""Artifact service factory for ADK runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.artifacts import FileArtifactService

from .paths import resolve_node_root


@dataclass(slots=True)
class ArtifactConfig:
    """Runtime artifact storage configuration."""

    enabled: bool
    root_dir: str


def _default_artifact_dir(node_root: Path | None = None) -> Path:
    """Return the default filesystem root for local artifacts."""
    root_dir = resolve_node_root(node_root) / "artifacts"
    root_dir.mkdir(parents=True, exist_ok=True)
    return root_dir


def load_artifact_config(node_root: Path | None = None) -> ArtifactConfig:
    """Build deterministic artifact storage for one Node root."""
    return ArtifactConfig(enabled=True, root_dir=str(_default_artifact_dir(node_root)))


def create_artifact_service(config: ArtifactConfig | None = None) -> Any | None:
    """Create a local file-backed ADK artifact service from runtime config."""
    cfg = config or load_artifact_config()
    if not cfg.enabled:
        return None
    return FileArtifactService(root_dir=cfg.root_dir)
