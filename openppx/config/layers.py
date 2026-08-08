"""Immutable effective Config snapshot and provenance values."""

from __future__ import annotations

from dataclasses import dataclass

from openppx.modeling.selection import ModelResolution
from openppx.permissions import ResolvedPermissionSnapshot

from .models import AgentConfig, NodeConfig


@dataclass(frozen=True, slots=True)
class ConfigOrigin:
    """Resource identity and revision contributing to one snapshot."""

    resource_id: str
    revision: str


@dataclass(frozen=True, slots=True)
class ConfigSnapshot:
    """Validated immutable inputs for a future runtime construction boundary."""

    node: NodeConfig
    agent: AgentConfig
    model: ModelResolution
    permissions: ResolvedPermissionSnapshot
    origins: tuple[ConfigOrigin, ...]
    revision: str
