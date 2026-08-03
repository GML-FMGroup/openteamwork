"""Identity dataclasses used by Node/runtime integration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class ResolvedPrincipal:
    """One resolved principal ready to be projected into ADK runtime state."""

    principal_id: str
    principal_type: str
    privilege_level: str
    account_kind: str
    display_name: str
    authenticated: bool = True
    external_subject_id: str | None = None
    external_display_id: str | None = None
    memory_ingest_enabled: bool = True
    metadata: dict[str, Any] = field(default_factory=dict)
