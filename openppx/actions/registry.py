"""Deterministic Action registration and caller-aware catalog queries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
import shlex
from typing import Any, Protocol

from pydantic import BaseModel

from .models import ActionCatalogEntry, ActionContext, ActionProjection, ActionSpec, SlashCommandSpec, SlashInvocationContext


class ActionHandler(Protocol):
    """Execute one validated Action input without rendering or transport concerns."""

    def __call__(self, context: ActionContext, input_data: BaseModel) -> dict[str, Any]: ...


AvailabilityCheck = Callable[[ActionContext], str | None]
SlashInputAdapter = Callable[[SlashCommandSpec, str, SlashInvocationContext], Mapping[str, object]]
DynamicSlashCommandProvider = Callable[[ActionContext], tuple[SlashCommandSpec, ...]]


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
    parsed_args: tuple[object, ...] = ()


class ActionRegistry:
    """Own the unique process-local catalog of product Actions."""

    def __init__(self) -> None:
        self._actions: dict[str, RegisteredAction] = {}
        self._slash_commands: dict[str, tuple[RegisteredAction, SlashCommandSpec]] = {}
        self._dynamic_slash_commands: dict[
            str,
            tuple[DynamicSlashCommandProvider, SlashInputAdapter],
        ] = {}

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

    def register_dynamic_slash(
        self,
        action_id: str,
        provider: DynamicSlashCommandProvider,
        *,
        slash_input: SlashInputAdapter,
    ) -> None:
        """Attach caller-contextual slash aliases to one existing Action.

        Dynamic aliases are intended for resources such as Agent-visible Skills
        whose command set cannot be frozen into the process-wide Action spec.
        """
        registered = self._actions.get(action_id)
        if registered is None:
            raise ActionRegistrationError(
                f"Dynamic slash commands require registered Action '{action_id}'"
            )
        if registered.spec.slash_commands:
            raise ActionRegistrationError(
                "Dynamic slash commands cannot be mixed with static aliases on one Action"
            )
        if action_id in self._dynamic_slash_commands:
            raise ActionRegistrationError(
                f"Dynamic slash commands are already registered for Action '{action_id}'"
            )
        self._dynamic_slash_commands[action_id] = (provider, slash_input)

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
        dynamic_bindings = (
            self._dynamic_command_bindings(context)
            if projection in {None, "slash"}
            else {}
        )
        for action_id in sorted(self._actions):
            registered = self._actions[action_id]
            if namespace is not None and registered.spec.namespace != namespace:
                continue
            dynamic_commands = tuple(
                command
                for _, (binding, command) in sorted(dynamic_bindings.items())
                if binding.spec.action_id == action_id
            )
            if (
                projection is not None
                and projection not in registered.spec.projections
                and not (projection == "slash" and dynamic_commands)
            ):
                continue
            projected_spec = registered.spec
            if dynamic_commands:
                projected_spec = replace(
                    registered.spec,
                    projections=(*registered.spec.projections, "slash"),
                    slash_commands=dynamic_commands,
                )
            reason = self.availability_reason(registered, context)
            items.append(
                ActionCatalogEntry(
                    spec=projected_spec,
                    available=reason is None,
                    availability_reason=reason,
                )
            )
        return tuple(items)

    def resolve_slash(
        self,
        raw: str,
        *,
        context: ActionContext | None = None,
    ) -> ResolvedSlashCommand:
        """Resolve one normalized slash command or raise a stable command error."""
        normalized = str(raw or "").strip()
        token, separator, remainder = normalized.partition(" ")
        if "@" in token:
            base, suffix = token.rsplit("@", 1)
            if base.startswith("/") and suffix.replace("_", "").isalnum():
                token = base
        binding = self._slash_commands.get(token.lower())
        if binding is None and context is not None:
            binding = self._dynamic_command_bindings(context).get(token.lower())
        if binding is None:
            raise SlashCommandError("command_not_found", "The requested slash command is not registered.")
        registered, command = binding
        args = remainder.strip() if separator else ""
        if args and not command.accepts_args:
            raise SlashCommandError("command_arguments_not_allowed", "This slash command does not accept arguments.")
        parsed_args = self._validate_slash_arguments(command, args)
        return ResolvedSlashCommand(
            registered=registered,
            command=command,
            args=args,
            parsed_args=parsed_args,
        )

    def _dynamic_command_bindings(
        self,
        context: ActionContext,
    ) -> dict[str, tuple[RegisteredAction, SlashCommandSpec]]:
        """Return unambiguous contextual aliases after reserving static names."""
        candidates: dict[
            str,
            list[tuple[RegisteredAction, SlashCommandSpec]],
        ] = {}
        for action_id in sorted(self._dynamic_slash_commands):
            provider, slash_input = self._dynamic_slash_commands[action_id]
            registered = self._actions[action_id]
            try:
                commands = provider(context)
            except Exception:
                # Catalog discovery must fail closed when a contextual resource
                # snapshot cannot be read. Invocation will report unknown rather
                # than exposing stale or partially resolved commands.
                continue
            dynamic_registered = replace(registered, slash_input=slash_input)
            for command in commands:
                if command.command in self._slash_commands:
                    continue
                candidates.setdefault(command.command, []).append(
                    (dynamic_registered, command)
                )
        return {
            command: bindings[0]
            for command, bindings in candidates.items()
            if len(bindings) == 1
        }

    @staticmethod
    def _validate_slash_arguments(command: SlashCommandSpec, raw_args: str) -> tuple[object, ...]:
        """Validate positional command text against its projected typed schema."""
        if not command.arguments:
            return ()
        text_argument = command.arguments[-1] if command.arguments[-1].value_type == "text" else None
        try:
            tokens = shlex.split(raw_args)
        except ValueError:
            raise SlashCommandError("command_arguments_invalid", f"Usage: {command.usage}") from None
        if not tokens:
            if command.no_args_behavior == "show_usage":
                raise SlashCommandError("command_usage_required", f"Usage: {command.usage}")
            return ()
        required_count = sum(1 for argument in command.arguments if argument.required)
        maximum_count = None if text_argument is not None else len(command.arguments)
        if len(tokens) < required_count or (maximum_count is not None and len(tokens) > maximum_count):
            raise SlashCommandError("command_argument_count_invalid", f"Usage: {command.usage}")
        values: list[object] = []
        token_index = 0
        for argument in command.arguments:
            if token_index >= len(tokens):
                break
            token = " ".join(tokens[token_index:]) if argument.value_type == "text" else tokens[token_index]
            try:
                if argument.value_type == "integer":
                    value: object = int(token)
                elif argument.value_type == "boolean":
                    normalized = token.lower()
                    if normalized not in {"true", "false"}:
                        raise ValueError
                    value = normalized == "true"
                elif argument.value_type == "enum":
                    if token not in argument.choices:
                        raise ValueError
                    value = token
                else:
                    value = token
            except ValueError:
                raise SlashCommandError("command_argument_type_invalid", f"Usage: {command.usage}") from None
            values.append(value)
            token_index = len(tokens) if argument.value_type == "text" else token_index + 1
        return tuple(values)

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
