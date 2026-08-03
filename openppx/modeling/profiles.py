"""Strict persisted Model Profile resources."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal, TypeAlias

from pydantic import Field, StrictBool, StrictInt, StringConstraints, field_validator, model_validator

from openppx.config.models import ResourceMetadata, ResourceName, StrictConfigModel
from openppx.config.secrets import SecretRef


ProviderId: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=63, pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$"),
]
CapabilityId: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9_]{0,62}$"),
]
ModelCapability: TypeAlias = Literal[
    "text",
    "vision",
    "audio_input",
    "audio_output",
    "tool_calling",
    "structured_output",
    "reasoning",
    "long_context",
]
ModelName: TypeAlias = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class ModelProfileSpec(StrictConfigModel):
    """Provider, model, cost, capability, and explicit fallback policy."""

    provider: ProviderId
    model: ModelName
    credential: SecretRef | None = None
    execution_location: Literal["local", "remote"]
    capabilities: list[ModelCapability] = Field(default_factory=list)
    context_window_tokens: StrictInt | None = Field(default=None, ge=1)
    input_cost_per_million_usd: Decimal | None = Field(default=None, ge=0)
    output_cost_per_million_usd: Decimal | None = Field(default=None, ge=0)
    fallback_profiles: list[ResourceName] = Field(default_factory=list)
    enabled: StrictBool = True

    @field_validator("model")
    @classmethod
    def model_must_be_visible(cls, value: str) -> str:
        """Reject blank/control-bearing provider model identifiers."""
        if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("model must contain visible characters")
        return value

    @field_validator("input_cost_per_million_usd", "output_cost_per_million_usd", mode="before")
    @classmethod
    def costs_must_be_exact_decimal_strings(cls, value: object) -> Decimal | None:
        """Accept exact decimal text while rejecting lossy JSON floating-point values."""
        if value is None or isinstance(value, Decimal):
            return value
        if not isinstance(value, str) or not value.strip():
            raise ValueError("cost must be an exact decimal string or null")
        try:
            return Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("cost must be a valid decimal string") from exc

    @field_validator("capabilities", "fallback_profiles")
    @classmethod
    def list_entries_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep capability and fallback traversal deterministic."""
        if len(value) != len(set(value)):
            raise ValueError("entries must be unique")
        return value


class ModelProfile(StrictConfigModel):
    """Versioned Model Profile resource owned by an OpenPPX Node."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["ModelProfile"]
    metadata: ResourceMetadata
    spec: ModelProfileSpec

    @model_validator(mode="after")
    def fallbacks_must_not_reference_self(self) -> "ModelProfile":
        """Reject an immediately recursive fallback at the resource boundary."""
        if self.metadata.name in self.spec.fallback_profiles:
            raise ValueError("fallbackProfiles must not reference the profile itself")
        return self
