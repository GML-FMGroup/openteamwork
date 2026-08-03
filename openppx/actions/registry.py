"""Deterministic Action registration and caller-aware catalog queries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel

from .models import ActionCatalogEntry, ActionContext, ActionProjection, ActionSpec, SlashCommandSpec, SlashInvocationContext


class ActionHandler(Protocol):
    """Execute one validated Action input without rendering or transport concerns."""

    def __call__(self, context: ActionContext, input_data: BaseModel) -> dict[str, Any]: ...


AvailabilityCheck = Callable[[ActionContext], str | None]
SlashInputAdapter = Callable[[SlashCommandSpec, str, SlashInvocationContext], Mapping[str, object]]


class ActionRegistrationError(ValueError):
    """Raised when Action identity would make the catalog ambiguous."""


class SlashCommandError(ValueError):
    """Stable command-resolution failure before target Action validation."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class RegisteredAction:
    """Internal handler binding excluded from public catalog serialization."""

    spec: ActionSpec
    handler: ActionHandler
    availability: AvailabilityCheck | None
    slash_input: SlashInputAdapter | None


@dataclass(frozen=True, slots=True)
class ResolvedSlashCommand:
    """One normalized command bound to its target Action and raw argument text."""

    registered: RegisteredAction
    command: SlashCommandSpec
    args: str


class ActionRegistry:
    """Own the unique process-local catalog of product Actions."""

    def __init__(self) -> None:
        self._actions: dict[str, RegisteredAction] = {}
        self._slash_commands: dict[str, tuple[RegisteredAction, SlashCommandSpec]] = {}

    def register(
        self,
        spec: ActionSpec,
        handler: ActionHandler,
        *,
        availability: AvailabilityCheck | None = None,
        slash_input: SlashInputAdapter | None = None,
    ) -> None:
        """Register one immutable spec and handler under a unique Action ID."""
        if spec.action_id in self._actions:
            raise ActionRegistrationError(f"Action '{spec.action_id}' is already registered")
        if spec.slash_commands and slash_input is None:
            raise ActionRegistrationError("Actions with slash commands require a slash input adapter")
        if not spec.slash_commands and slash_input is not None:
            raise ActionRegistrationError("slash input adapters require declared slash commands")
        duplicates = [item.command for item in spec.slash_commands if item.command in self._slash_commands]
        if duplicates:
            raise ActionRegistrationError(f"Slash command '{duplicates[0]}' is already registered")
        registered = RegisteredAction(spec, handler, availability, slash_input)
        self._actions[spec.action_id] = registered
        for command in spec.slash_commands:
            self._slash_commands[command.command] = (registered, command)

    def resolve(self, action_id: str) -> RegisteredAction | None:
        """Return one internal binding without raising for unknown IDs."""
        return self._actions.get(action_id)

    def catalog(
        self,
        context: ActionContext,
        *,
        namespace: str | None = None,
        projection: ActionProjection | None = None,
    ) -> tuple[ActionCatalogEntry, ...]:
        """Return a stable sorted catalog with server-computed availability."""
        items: list[ActionCatalogEntry] = []
        for action_id in sorted(self._actions):
            registered = self._actions[action_id]
            if namespace is not None and registered.spec.namespace != namespace:
                continue
            if projection is not None and projection not in registered.spec.projections:
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

    def resolve_slash(self, raw: str) -> ResolvedSlashCommand:
        """Resolve one normalized slash command or raise a stable command error."""
        normalized = str(raw or "").strip()
        token, separator, remainder = normalized.partition(" ")
        if "@" in token:
            base, suffix = token.rsplit("@", 1)
            if base.startswith("/") and suffix.replace("_", "").isalnum():
                token = base
        binding = self._slash_commands.get(token.lower())
        if binding is None:
            raise SlashCommandError("command_not_found", "The requested slash command is not registered.")
        registered, command = binding
        args = remainder.strip() if separator else ""
        if args and not command.accepts_args:
            raise SlashCommandError("command_arguments_not_allowed", "This slash command does not accept arguments.")
        return ResolvedSlashCommand(registered=registered, command=command, args=args)

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
