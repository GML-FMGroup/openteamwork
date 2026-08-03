"""Deterministic Action registration and caller-aware catalog queries."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from .models import ActionCatalogEntry, ActionContext, ActionSpec


class ActionHandler(Protocol):
    """Execute one validated Action input without rendering or transport concerns."""

    def __call__(self, context: ActionContext, input_data: BaseModel) -> dict[str, Any]: ...


AvailabilityCheck = Callable[[ActionContext], str | None]


class ActionRegistrationError(ValueError):
    """Raised when Action identity would make the catalog ambiguous."""


@dataclass(frozen=True, slots=True)
class RegisteredAction:
    """Internal handler binding excluded from public catalog serialization."""

    spec: ActionSpec
    handler: ActionHandler
    availability: AvailabilityCheck | None


class ActionRegistry:
    """Own the unique process-local catalog of product Actions."""

    def __init__(self) -> None:
        self._actions: dict[str, RegisteredAction] = {}

    def register(
        self,
        spec: ActionSpec,
        handler: ActionHandler,
        *,
        availability: AvailabilityCheck | None = None,
    ) -> None:
        """Register one immutable spec and handler under a unique Action ID."""
        if spec.action_id in self._actions:
            raise ActionRegistrationError(f"Action '{spec.action_id}' is already registered")
        self._actions[spec.action_id] = RegisteredAction(spec, handler, availability)

    def resolve(self, action_id: str) -> RegisteredAction | None:
        """Return one internal binding without raising for unknown IDs."""
        return self._actions.get(action_id)

    def catalog(
        self,
        context: ActionContext,
        *,
        namespace: str | None = None,
    ) -> tuple[ActionCatalogEntry, ...]:
        """Return a stable sorted catalog with server-computed availability."""
        items: list[ActionCatalogEntry] = []
        for action_id in sorted(self._actions):
            registered = self._actions[action_id]
            if namespace is not None and registered.spec.namespace != namespace:
                continue
            reason = self.availability_reason(registered, context)
            items.append(
                ActionCatalogEntry(
                    spec=registered.spec,
                    available=reason is None,
                    availability_reason=reason,
                )
            )
        return tuple(items)

    @staticmethod
    def availability_reason(registered: RegisteredAction, context: ActionContext) -> str | None:
        """Compute availability without duplicating executor authorization semantics."""
        if not registered.spec.required_capabilities.issubset(context.capabilities):
            return "capability_required"
        if registered.spec.permission not in context.permissions:
            return "permission_denied"
        if registered.availability is None:
            return None
        try:
            return registered.availability(context)
        except Exception:
            return "availability_check_failed"
