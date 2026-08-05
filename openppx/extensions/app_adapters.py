"""Trusted native adapter boundary for branded product Apps.

Native adapters are Node code, not code loaded from App definitions or starter
catalog entries.  This keeps third-party configuration declarative while giving
OpenPPX one explicit port for verified OpenWorker-style connectors.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from openppx.config import SecretRef, SecretStore, SecretValue

from .app_models import AppConnection, AppDefinition, AppToolSpec
from .errors import ExtensionError


@dataclass(frozen=True, slots=True)
class NativeAppAdapterReadiness:
    """Non-sensitive dependency state returned by one native adapter."""

    ready: bool
    issues: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class NativeAppContext:
    """Bounded execution context exposed to a trusted native App adapter."""

    definition: AppDefinition
    connection: AppConnection
    tools: tuple[AppToolSpec, ...]
    secret_store: SecretStore

    def credential_ref(self, slot: str) -> SecretRef:
        """Return one protected reference without revealing its value."""
        ref = self.connection.spec.credential_refs.get(slot)
        if ref is None:
            raise ExtensionError("dependency_missing", "App credential binding is missing.")
        return ref

    def credential(self, slot: str) -> SecretValue:
        """Resolve one credential only at the trusted adapter boundary."""
        return self.secret_store.resolve(self.credential_ref(slot))


class NativeAppAdapter(Protocol):
    """Port implemented by verified, Node-shipped product integrations."""

    adapter_id: str

    def readiness(self, context: NativeAppContext) -> NativeAppAdapterReadiness:
        """Check adapter-specific dependencies without returning secret values."""
        ...

    def build_tools(self, context: NativeAppContext) -> tuple[Any, ...]:
        """Build Google ADK-compatible tools for one immutable connection."""
        ...


class NativeAppAdapterRegistry:
    """Explicit registry of trusted native adapters available in one Node."""

    def __init__(self) -> None:
        self._adapters: dict[str, NativeAppAdapter] = {}

    def register(self, adapter: NativeAppAdapter) -> None:
        """Register one stable adapter identity exactly once."""
        adapter_id = adapter.adapter_id
        if not adapter_id or adapter_id in self._adapters:
            raise ValueError(f"Native App adapter '{adapter_id}' is invalid or already registered.")
        self._adapters[adapter_id] = adapter

    def get(self, adapter_id: str) -> NativeAppAdapter | None:
        """Return one adapter when it is shipped by this Node."""
        return self._adapters.get(adapter_id)

    def require(self, adapter_id: str) -> NativeAppAdapter:
        """Return one adapter or raise a stable Extension error."""
        adapter = self.get(adapter_id)
        if adapter is None:
            raise ExtensionError("dependency_missing", "Native App adapter is not installed on this Node.")
        return adapter

    @property
    def adapter_ids(self) -> tuple[str, ...]:
        """Return deterministic registered identities for diagnostics."""
        return tuple(sorted(self._adapters))


__all__ = [
    "NativeAppAdapter",
    "NativeAppAdapterReadiness",
    "NativeAppAdapterRegistry",
    "NativeAppContext",
]
