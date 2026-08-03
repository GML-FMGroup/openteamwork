"""Stable metadata and result types for product Actions."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

from pydantic import BaseModel


ActionScope = Literal["node", "agent", "session", "run", "task", "extension"]
ActionRisk = Literal["low", "medium", "high"]
ActionConfirmation = Literal["never", "required"]
ActionExecution = Literal["sync", "long_running"]
ActionProjection = Literal["cli", "slash", "desktop", "mobile"]
SlashCommandLifecycle = Literal["side_channel", "finalize_active_turn", "stop_active_turn"]

_ACTION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_NAMESPACE_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_PERMISSION_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_SLASH_COMMAND_PATTERN = re.compile(r"^/[a-z][a-z0-9-]*$")


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
    order: int = 100

    def __post_init__(self) -> None:
        if _SLASH_COMMAND_PATTERN.fullmatch(self.command) is None:
            raise ValueError("slash command must be a lowercase slash identifier")
        if not self.title.strip() or not self.description.strip() or not self.icon.strip():
            raise ValueError("slash command presentation fields must contain visible text")
        if self.lifecycle not in {"side_channel", "finalize_active_turn", "stop_active_turn"}:
            raise ValueError("slash command lifecycle is not supported")
        if self.arg_hint and not self.accepts_args:
            raise ValueError("slash command arg_hint requires accepts_args")
        if self.order < 0:
            raise ValueError("slash command order must be non-negative")


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
        if self.scope not in {"node", "agent", "session", "run", "task", "extension"}:
            raise ValueError("scope is not supported")
        if self.risk not in {"low", "medium", "high"}:
            raise ValueError("risk is not supported")
        if self.confirmation not in {"never", "required"}:
            raise ValueError("confirmation is not supported")
        if self.execution not in {"sync", "long_running"}:
            raise ValueError("execution is not supported")
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
