"""Shared tool-prefix conflict index across Runtime MCP projections."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .errors import ExtensionError


@dataclass(frozen=True, slots=True)
class ToolPrefixReservation:
    """One Agent-scoped prefix owned by an extension resource."""

    prefix: str
    owner_key: str


PrefixProvider = Callable[[str], tuple[ToolPrefixReservation, ...]]


class ToolPrefixIndex:
    """Compose prefix reservations without coupling extension managers."""

    def __init__(self) -> None:
        self._providers: dict[str, PrefixProvider] = {}

    def register(self, provider_id: str, provider: PrefixProvider) -> None:
        """Register one stable provider during Node composition."""
        if provider_id in self._providers:
            raise ValueError(f"Tool prefix provider '{provider_id}' is already registered.")
        self._providers[provider_id] = provider

    def reservations(self, agent_id: str) -> tuple[ToolPrefixReservation, ...]:
        """Return every deterministic reservation for one Agent."""
        values = [
            reservation
            for provider_id in sorted(self._providers)
            for reservation in self._providers[provider_id](agent_id)
        ]
        return tuple(sorted(values, key=lambda item: (item.prefix, item.owner_key)))

    def require_available(self, prefix: str, agent_id: str, *, owner_key: str) -> None:
        """Reject one prefix already reserved by a different resource."""
        for reservation in self.reservations(agent_id):
            if reservation.prefix == prefix and reservation.owner_key != owner_key:
                raise ExtensionError(
                    "extension_conflict",
                    "Tool-name prefix conflicts with another resource enabled for this Agent.",
                )


__all__ = ["ToolPrefixIndex", "ToolPrefixReservation"]
