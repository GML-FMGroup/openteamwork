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
    slash_commands: list["SlashCommandItem"] = Field(default_factory=list)
    available: StrictBool
    availability_reason: Identifier | None = None


class SlashCommandItem(WireModel):
    """Client-safe command alias projected from one Action."""

    command: Annotated[str, StringConstraints(pattern=r"^/[a-z][a-z0-9-]*$")]
    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    description: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    icon: Identifier
    arg_hint: Annotated[str, StringConstraints(max_length=120)] = ""
    lifecycle: Literal["side_channel", "finalize_active_turn", "stop_active_turn"]
    accepts_args: StrictBool = False
    order: StrictInt = Field(ge=0)


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


class SlashCommandInvokeInput(WireModel):
    """Structured context for the shared command invocation Action."""

    raw_command: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    user_id: Identifier
    agent_id: Identifier | None = None
    session_id: Identifier | None = None
    run_id: Identifier | None = None


class SlashCommandInvokeResult(WireModel):
    """Structured command outcome retaining target Action provenance."""

    command: Annotated[str, StringConstraints(pattern=r"^/[a-z][a-z0-9-]*$")]
    lifecycle: Literal["side_channel", "finalize_active_turn", "stop_active_turn"]
    target_action_id: Identifier
    result: dict[str, Any]


class ExtensionSourceSummary(WireModel):
    """Non-sensitive Extension provenance category and trust posture."""

    type: Literal["builtin", "local_directory", "local_archive", "git", "catalog", "direct", "plugin"]
    trust: Literal["builtin", "local", "third_party"]


class ExtensionReadiness(WireModel):
    """Common Extension readiness without backend exception or Secret material."""

    ready: StrictBool
    issues: list[Identifier] = Field(default_factory=list)


class ExtensionPresentation(WireModel):
    """Client-safe Extension icon identity without remote image data."""

    icon: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"),
    ]
    brand_color: Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")] | None = None


class ExtensionSummaryItem(WireModel):
    """One stable row in the unified four-kind Extension inventory."""

    kind: Literal["plugin", "app", "mcp", "skill"]
    id: Identifier
    display_name: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    description: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    status: Identifier
    revision: Identifier
    source: ExtensionSourceSummary
    risk: Literal["low", "medium", "high"]
    enabled_agent_ids: list[Identifier] = Field(default_factory=list)
    readiness: ExtensionReadiness
    presentation: ExtensionPresentation
    managed_by: Identifier | None = None


class ExtensionListPayload(WireModel):
    """Unified Extension inventory result."""

    items: list[ExtensionSummaryItem]


class ExtensionDetailPayload(ExtensionSummaryItem):
    """One Extension row plus bounded domain-specific fields."""

    details: dict[str, Any]


class ExtensionPreviewPayload(WireModel):
    """Confirmed source digest and client-safe install preview."""

    kind: Literal["plugin", "skill"]
    preview: dict[str, Any]


class ClientContractBundle(WireModel):
    """Schema-only root that retains every Increment 3 contract definition."""

    envelope: ClientEnvelope
    action_catalog: ActionCatalogPayload
    action_invoke: ActionInvokeRequest
    slash_command_input: SlashCommandInvokeInput
    slash_command_result: SlashCommandInvokeResult
    extension_list: ExtensionListPayload
    extension_detail: ExtensionDetailPayload
    extension_preview: ExtensionPreviewPayload
