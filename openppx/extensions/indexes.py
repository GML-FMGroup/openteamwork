"""Small provider indexes for cross-domain Extension invariants."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

from .errors import ExtensionError


@dataclass(frozen=True, slots=True)
class ResourceIdentityReservation:
    """One globally visible identity owned by an Extension resource."""

    kind: str
    name: str
    owner_key: str


IdentityProvider = Callable[[], tuple[ResourceIdentityReservation, ...]]


class ResourceIdentityIndex:
    """Compose identities without making one universal lifecycle model."""

    def __init__(self) -> None:
        self._providers: dict[str, IdentityProvider] = {}

    def register(self, provider_id: str, provider: IdentityProvider) -> None:
        """Register one stable identity provider at composition time."""
        if provider_id in self._providers:
            raise ValueError(f"Resource identity provider '{provider_id}' is already registered.")
        self._providers[provider_id] = provider

    def reservations(self) -> tuple[ResourceIdentityReservation, ...]:
        """Return every identity in deterministic order."""
        values = [
            reservation
            for provider_id in sorted(self._providers)
            for reservation in self._providers[provider_id]()
        ]
        return tuple(sorted(values, key=lambda item: (item.kind, item.name, item.owner_key)))

    def require_available(self, kind: str, name: str, *, owner_key: str) -> None:
        """Reject an identity reserved by a different owner."""
        for reservation in self.reservations():
            if (
                reservation.kind == kind
                and reservation.name == name
                and reservation.owner_key != owner_key
            ):
                raise ExtensionError(
                    "extension_conflict",
                    f"{kind} identity conflicts with another installed Extension.",
                )


ReferenceProvider = Callable[[str], tuple[str, ...]]


class ExtensionReferenceIndex:
    """Compose owner references without inspecting another domain's files."""

    def __init__(self) -> None:
        self._providers: dict[str, ReferenceProvider] = {}

    def register(self, provider_id: str, provider: ReferenceProvider) -> None:
        """Register one stable reference provider at composition time."""
        if provider_id in self._providers:
            raise ValueError(f"Extension reference provider '{provider_id}' is already registered.")
        self._providers[provider_id] = provider

    def references(self, owner_key: str) -> tuple[str, ...]:
        """Return every client-safe reference identity for one owner."""
        values = [
            reference
            for provider_id in sorted(self._providers)
            for reference in self._providers[provider_id](owner_key)
        ]
        return tuple(sorted(set(values)))

    def require_unreferenced(self, owner_key: str) -> None:
        """Reject removal while another domain still references the owner."""
        references = self.references(owner_key)
        if references:
            raise ExtensionError(
                "extension_in_use",
                "Extension is still referenced by another resource.",
                details={"references": list(references)},
            )


__all__ = [
    "ExtensionReferenceIndex",
    "ResourceIdentityIndex",
    "ResourceIdentityReservation",
]
