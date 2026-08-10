"""Stable metadata and result types for product Actions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel


ActionScope = Literal["node", "agent", "session", "run", "task", "goal", "flow", "automation", "extension"]
ActionRisk = Literal["low", "medium", "high"]
ActionConfirmation = Literal["never", "required"]
ActionExecution = Literal["sync", "long_running"]
ActionOperation = Literal["read", "mutation"]
ActionSuccessPresentation = Literal["inline", "panel", "navigate", "toast"]
ActionProjection = Literal["cli", "slash", "desktop", "mobile"]
SlashCommandLifecycle = Literal["side_channel", "finalize_active_turn", "stop_active_turn", "agent_turn"]
SlashCommandArgumentType = Literal["string", "text", "integer", "boolean", "enum", "resource_id"]
SlashCommandNoArgsBehavior = Literal["invoke", "show_usage"]

_ACTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SLASH_COMMAND_PATTERN = re.compile(r"^/[a-z][a-z0-9-]*(?::[a-z][a-z0-9-]*)?$")
_SLASH_ARGUMENT_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class SlashCommandArgumentSpec:
    """Ordered, client-safe positional argument metadata for one slash command."""

    name: str
    value_type: SlashCommandArgumentType
    description: str
    required: bool = False
    choices: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if _SLASH_ARGUMENT_PATTERN.fullmatch(self.name) is None:
            raise ValueError("slash argument name must be a lowercase identifier")
        if not self.description.strip():
            raise ValueError("slash argument description must contain visible text")
        if self.value_type not in {"string", "text", "integer", "boolean", "enum", "resource_id"}:
            raise ValueError("slash argument type is not supported")
        if self.value_type == "enum" and not self.choices:
            raise ValueError("enum slash arguments require choices")
        if self.value_type != "enum" and self.choices:
            raise ValueError("slash argument choices require enum type")
        if len(self.choices) != len(set(self.choices)) or any(not choice.strip() for choice in self.choices):
            raise ValueError("slash argument choices must be unique visible values")


@dataclass(frozen=True, slots=True)
class SlashCommandSpec:
    """Client-safe presentation and turn-lifecycle metadata for one Action alias."""

    command: str
    title: str
    description: str
    icon: str
    arg_hint: str = ""
    lifecycle: SlashCommandLifecycle = "side_channel"
    accepts_args: bool = False
    arguments: tuple[SlashCommandArgumentSpec, ...] = ()
    no_args_behavior: SlashCommandNoArgsBehavior = "invoke"
    order: int = 100

    def __post_init__(self) -> None:
        if _SLASH_COMMAND_PATTERN.fullmatch(self.command) is None:
            raise ValueError("slash command must be a lowercase slash identifier with at most one namespace")
        if not self.title.strip() or not self.description.strip() or not self.icon.strip():
            raise ValueError("slash command presentation fields must contain visible text")
        if self.lifecycle not in {"side_channel", "finalize_active_turn", "stop_active_turn", "agent_turn"}:
            raise ValueError("slash command lifecycle is not supported")
        if self.no_args_behavior not in {"invoke", "show_usage"}:
            raise ValueError("slash command no-args behavior is not supported")
        if self.arguments:
            object.__setattr__(self, "accepts_args", True)
        if len({argument.name for argument in self.arguments}) != len(self.arguments):
            raise ValueError("slash argument names must be unique")
        optional_seen = False
        for argument in self.arguments:
            if not argument.required:
                optional_seen = True
            elif optional_seen:
                raise ValueError("required arguments must precede optional arguments")
        text_positions = [index for index, argument in enumerate(self.arguments) if argument.value_type == "text"]
        if text_positions and text_positions != [len(self.arguments) - 1]:
            raise ValueError("text slash arguments must be the final positional argument")
        if self.arg_hint and not self.accepts_args:
            raise ValueError("slash command arg_hint requires accepts_args")
        if self.no_args_behavior == "show_usage" and not self.arguments:
            raise ValueError("show_usage requires a typed slash argument schema")
        if self.order < 0:
            raise ValueError("slash command order must be non-negative")

    @property
    def usage(self) -> str:
        """Return stable usage text generated from the typed argument contract."""
        if not self.arguments:
            return self.command
        suffix = " ".join(
            f"<{argument.name}>" if argument.required else f"[{argument.name}]"
            for argument in self.arguments
        )
        return f"{self.command} {suffix}"


@dataclass(frozen=True, slots=True)
class SlashInvocationContext:
    """Explicit resource identities supplied by a slash-capable client."""

    user_id: str
    agent_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None


@dataclass(frozen=True, slots=True)
class ActionSpec:
    """Declarative Action identity, authorization, and client projection metadata."""

    action_id: str
    namespace: str
    title: str
    description: str
    input_model: type[BaseModel]
    scope: ActionScope
    required_capabilities: frozenset[str]
    permission: str
    risk: ActionRisk = "low"
    confirmation: ActionConfirmation = "never"
    execution: ActionExecution = "sync"
    operation: ActionOperation = "read"
    preview_action_id: str | None = None
    success_presentation: ActionSuccessPresentation = "inline"
    projections: tuple[ActionProjection, ...] = ()
    slash_commands: tuple[SlashCommandSpec, ...] = ()

    def __post_init__(self) -> None:
        """Reject ambiguous metadata before it reaches any client catalog."""
        if _ACTION_ID_PATTERN.fullmatch(self.action_id) is None:
            raise ValueError("action_id must be a lowercase dotted identifier")
        if _NAMESPACE_PATTERN.fullmatch(self.namespace) is None:
            raise ValueError("namespace must be a lowercase identifier")
        if self.action_id.split(".", 1)[0] != self.namespace:
            raise ValueError("action_id namespace must match namespace")
        if not self.title.strip() or not self.description.strip():
            raise ValueError("title and description must contain visible text")
        if not isinstance(self.input_model, type) or not issubclass(self.input_model, BaseModel):
            raise TypeError("input_model must be a Pydantic BaseModel type")
        if _PERMISSION_PATTERN.fullmatch(self.permission) is None:
            raise ValueError("permission must be a lowercase dotted identifier")
        if self.scope not in {"node", "agent", "session", "run", "task", "goal", "flow", "automation", "extension"}:
            raise ValueError("scope is not supported")
        if self.risk not in {"low", "medium", "high"}:
            raise ValueError("risk is not supported")
        if self.confirmation not in {"never", "required"}:
            raise ValueError("confirmation is not supported")
        if self.risk == "high" and self.confirmation != "required":
            raise ValueError("high-risk Actions must require confirmation")
        if self.execution not in {"sync", "long_running"}:
            raise ValueError("execution is not supported")
        if self.operation not in {"read", "mutation"}:
            raise ValueError("operation is not supported")
        if self.preview_action_id is not None and _ACTION_ID_PATTERN.fullmatch(self.preview_action_id) is None:
            raise ValueError("preview_action_id must be a lowercase dotted Action identifier")
        if self.preview_action_id == self.action_id:
            raise ValueError("an Action cannot preview itself")
        if self.success_presentation not in {"inline", "panel", "navigate", "toast"}:
            raise ValueError("success presentation is not supported")
        invalid_capabilities = [
            capability
            for capability in self.required_capabilities
            if _PERMISSION_PATTERN.fullmatch(capability) is None
        ]
        if invalid_capabilities:
            raise ValueError("required capabilities must be lowercase dotted identifiers")
        if len(self.projections) != len(set(self.projections)):
            raise ValueError("projections must be unique")
        if any(projection not in {"cli", "slash", "desktop", "mobile"} for projection in self.projections):
            raise ValueError("projection is not supported")
        if bool(self.slash_commands) != ("slash" in self.projections):
            raise ValueError("slash projection and slash_commands must be declared together")
        if len({command.command for command in self.slash_commands}) != len(self.slash_commands):
            raise ValueError("slash commands must be unique within one Action")


@dataclass(frozen=True, slots=True)
class ActionContext:
    """Immutable authenticated request facts supplied by a control surface."""

    request_id: str
    correlation_id: str
    actor_id: str
    capabilities: frozenset[str]
    permissions: frozenset[str]
    client_id: str | None = None
    device_id: str | None = None
    principal_id: str | None = None
    privilege_level: str | None = None
    confirmed: bool = False
    node_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None

    def __post_init__(self) -> None:
        """Require stable non-empty identities without embedding transport state."""
        for field_name in ("request_id", "correlation_id", "actor_id"):
            value = getattr(self, field_name)
            if not value.strip() or any(ord(character) < 32 for character in value):
                raise ValueError(f"{field_name} must contain visible text")
        for field_name in ("client_id", "device_id", "principal_id", "privilege_level"):
            value = getattr(self, field_name)
            if value is not None and (not value.strip() or any(ord(character) < 32 for character in value)):
                raise ValueError(f"{field_name} must contain visible text when provided")


@dataclass(frozen=True, slots=True)
class ActionError:
    """Stable redacted Action error suitable for later wire projection."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False


class ActionFailure(RuntimeError):
    """Expected handler failure carrying one stable Action error."""

    def __init__(self, error: ActionError) -> None:
        self.error = error
        super().__init__(error.code)


@dataclass(frozen=True, slots=True)
class ActionOutcome:
    """Structured Action execution result with mutually exclusive data and error."""

    action_id: str
    ok: bool
    data: dict[str, Any] | None = None
    error: ActionError | None = None

    def __post_init__(self) -> None:
        if self.ok and (self.data is None or self.error is not None):
            raise ValueError("successful Action outcome requires data and forbids error")
        if not self.ok and (self.data is not None or self.error is None):
            raise ValueError("failed Action outcome requires error and forbids data")

    @classmethod
    def success(cls, action_id: str, data: dict[str, Any]) -> "ActionOutcome":
        """Build a successful Action outcome."""
        return cls(action_id=action_id, ok=True, data=data)

    @classmethod
    def failure(cls, action_id: str, error: ActionError) -> "ActionOutcome":
        """Build a failed Action outcome."""
        return cls(action_id=action_id, ok=False, error=error)


@dataclass(frozen=True, slots=True)
class ActionCatalogEntry:
    """One Action spec plus server-computed availability for a caller."""

    spec: ActionSpec
    available: bool
    availability_reason: str | None = None
