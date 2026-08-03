"""Strict Action inputs owned by the Control Plane application boundary."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StringConstraints, field_validator
from pydantic.alias_generators import to_camel

from openppx.config import AgentConfig, NodeConfig


ResourceId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=63, pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"),
]
PrincipalId = Annotated[str, StringConstraints(min_length=1, max_length=128)]
RunId = Annotated[
    str,
    StringConstraints(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$"),
]


class ActionInput(BaseModel):
    """Strict camel-cased base for all first-party Action inputs."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        loc_by_alias=True,
        extra="forbid",
        strict=True,
    )


class EmptyInput(ActionInput):
    """Input for Actions without arguments."""


class SystemHelpInput(ActionInput):
    """Optional namespace filter for the caller-aware Action catalog."""

    namespace: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")] | None = None


class NodeValidateInput(ActionInput):
    """Raw Node candidate retained for non-raising schema diagnostics."""

    candidate: dict[str, object]


class NodeMutationInput(ActionInput):
    """Strict typed Node candidate and optimistic concurrency precondition."""

    candidate: NodeConfig
    expected_revision: str | None


class AgentReadInput(ActionInput):
    """Identify one Agent resource."""

    agent_id: ResourceId


class AgentValidateInput(AgentReadInput):
    """Raw Agent candidate retained for non-raising schema diagnostics."""

    candidate: dict[str, object]


class AgentMutationInput(AgentReadInput):
    """Strict typed Agent candidate and optimistic concurrency precondition."""

    candidate: AgentConfig
    expected_revision: str | None


class ModelSelectionInput(AgentReadInput):
    """Per-request Model selection constraints without persisted side effects."""

    role: Literal["fast", "reasoning", "vision"] | None = None
    run_override: ResourceId | None = None
    required_capabilities: list[
        Literal[
            "text",
            "vision",
            "audio_input",
            "audio_output",
            "tool_calling",
            "structured_output",
            "reasoning",
            "long_context",
        ]
    ] = Field(default_factory=list)
    privacy: Literal["any", "local_only"] = "any"
    min_context_tokens: StrictInt | None = Field(default=None, ge=1)
    max_input_cost_per_million_usd: str | None = None
    max_output_cost_per_million_usd: str | None = None

    @field_validator("required_capabilities")
    @classmethod
    def capabilities_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep requirements deterministic for catalog and selection results."""
        if len(value) != len(set(value)):
            raise ValueError("requiredCapabilities entries must be unique")
        return value

    @field_validator("max_input_cost_per_million_usd", "max_output_cost_per_million_usd")
    @classmethod
    def costs_must_be_decimal_text(cls, value: str | None) -> str | None:
        """Reject blank cost constraints before Decimal conversion."""
        if value is not None:
            try:
                parsed = Decimal(value)
            except InvalidOperation as exc:
                raise ValueError("cost constraint must be decimal text or null") from exc
            if not parsed.is_finite() or parsed < 0:
                raise ValueError("cost constraint must be finite and non-negative")
        return value


class SessionNewInput(AgentReadInput):
    """Create one new Session for an Agent and authenticated principal."""

    user_id: PrincipalId


class RunStopInput(ActionInput):
    """Identify one active Run for cooperative cancellation."""

    run_id: RunId
