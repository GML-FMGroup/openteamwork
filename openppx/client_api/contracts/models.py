"""Strict common envelope and Action contract models."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StringConstraints
from pydantic.alias_generators import to_camel


Identifier = Annotated[str, StringConstraints(min_length=1, max_length=128)]


class WireModel(BaseModel):
    """Strict camel-cased base for every Client API wire model."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        loc_by_alias=True,
        extra="forbid",
        strict=True,
    )


class StableError(WireModel):
    """Redacted stable error shared by HTTP and future transports."""

    code: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*$")]
    message: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    retryable: StrictBool = False
    details: dict[str, Any] = Field(default_factory=dict)


class SuccessEnvelope(WireModel):
    """Successful common response envelope."""

    protocol_version: StrictInt = Field(ge=1)
    request_id: Identifier
    correlation_id: Identifier
    ok: Literal[True]
    result: dict[str, Any]


class ErrorEnvelope(WireModel):
    """Failed common response envelope."""

    protocol_version: StrictInt = Field(ge=1)
    request_id: Identifier
    correlation_id: Identifier
    ok: Literal[False]
    error: StableError


ClientEnvelope = Annotated[SuccessEnvelope | ErrorEnvelope, Field(discriminator="ok")]


class ActionCatalogItem(WireModel):
    """Client-safe Action metadata and caller-specific availability."""

    action_id: Identifier
    namespace: Identifier
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    description: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    scope: Literal["node", "agent", "session", "run", "task", "extension"]
    input_schema: dict[str, Any]
    required_capabilities: list[Identifier]
    permission: Identifier
    risk: Literal["low", "medium", "high"]
    confirmation: Literal["never", "required"]
    execution: Literal["sync", "long_running"]
    projections: list[Literal["cli", "slash", "desktop", "mobile"]]
    available: StrictBool
    availability_reason: Identifier | None = None


class ActionCatalogPayload(WireModel):
    """Caller-aware Action catalog result."""

    items: list[ActionCatalogItem]


class ActionInvokeRequest(WireModel):
    """Transport-neutral Action invocation request body."""

    request_id: Identifier
    correlation_id: Identifier
    action_id: Identifier
    input: dict[str, Any] = Field(default_factory=dict)
    confirmed: StrictBool = False


class ClientContractBundle(WireModel):
    """Schema-only root that retains every Increment 3 contract definition."""

    envelope: ClientEnvelope
    action_catalog: ActionCatalogPayload
    action_invoke: ActionInvokeRequest
