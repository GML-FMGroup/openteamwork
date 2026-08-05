"""Strict Action inputs owned by the Control Plane application boundary."""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, StrictInt, StringConstraints, field_validator, model_validator
from pydantic.alias_generators import to_camel

from openppx.config import AgentConfig, NodeConfig
from openppx.config import SecretRef
from openppx.extensions.app_models import AppConnection, AppDefinition
from openppx.extensions.mcp_models import McpServer
from openppx.extensions.models import ExtensionSourceRef
from openppx.extensions.plugin_marketplace import PluginMarketplaceSourceSpec
from openppx.modeling import ModelCapability, ModelProfile
from openppx.setup import SetupApplyRequest


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
    projection: Literal["cli", "slash", "desktop", "mobile"] | None = None


class SlashCommandInvokeInput(ActionInput):
    """One raw slash command plus explicit client resource context."""

    raw_command: Annotated[str, StringConstraints(min_length=1, max_length=512)]
    user_id: PrincipalId
    agent_id: ResourceId | None = None
    session_id: RunId | None = None
    run_id: RunId | None = None


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


class AgentCreateInput(AgentReadInput):
    """Create one Node-enabled Agent from bounded product fields."""

    display_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    workspace: Annotated[str, StringConstraints(max_length=1024)] | None = None
    owner_principal_id: PrincipalId
    privilege_level: Literal["low", "medium", "high", "root"] = "medium"
    model_profile_id: ResourceId
    instruction: Annotated[str, StringConstraints(max_length=16_384)] = ""

    @field_validator("display_name", "workspace", "instruction")
    @classmethod
    def agent_text_must_not_contain_controls(cls, value: str | None) -> str | None:
        """Reject control-bearing product text before entering Config mutation."""
        if value is not None and any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("value must not contain control characters")
        return value


class AgentUpdateInput(AgentReadInput):
    """Update the product-owned settings of one existing Agent."""

    display_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    workspace: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    privilege_level: Literal["low", "medium", "high", "root"]
    model_profile_id: ResourceId
    instruction: Annotated[str, StringConstraints(max_length=16_384)] = ""
    expected_revision: Annotated[str, StringConstraints(min_length=1, max_length=128)]

    @field_validator("display_name", "workspace", "instruction")
    @classmethod
    def update_text_must_not_contain_controls(cls, value: str) -> str:
        """Reject unsafe control characters in Agent-editable text."""
        if any((ord(character) < 32 and character not in {"\n", "\t"}) or ord(character) == 127 for character in value):
            raise ValueError("value must not contain control characters")
        return value


class AgentEnableInput(AgentReadInput):
    """Enable or disable one existing Agent on its Node."""

    enabled: bool


class AgentDeleteInput(AgentReadInput):
    """Remove one disabled Agent configuration while retaining its workspace."""

    expected_revision: Annotated[str, StringConstraints(min_length=1, max_length=128)]


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


class ModelProfileReadInput(ActionInput):
    """Identify one persisted Model Profile."""

    profile_id: ResourceId


class ModelProfileMutationInput(ModelProfileReadInput):
    """Create or replace one strict Model Profile under a revision precondition."""

    candidate: ModelProfile
    expected_revision: str | None


class ModelProfileWriteInput(ActionInput):
    """Mutable product fields shared by Profile creation and editing."""

    display_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    provider_id: Annotated[
        str,
        StringConstraints(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9_]*$"),
    ]
    model: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    execution_location: Literal["local", "remote"]
    api_base: Annotated[str, StringConstraints(min_length=1, max_length=2048)] | None = None
    capabilities: list[ModelCapability] = Field(default_factory=list)
    context_window_tokens: StrictInt | None = Field(default=None, ge=1)
    input_cost_per_million_usd: str | None = None
    output_cost_per_million_usd: str | None = None
    fallback_profile_ids: list[ResourceId] = Field(default_factory=list)
    enabled: StrictBool = True
    api_key: SecretStr | None = None

    @field_validator("display_name")
    @classmethod
    def profile_display_name_must_be_visible(cls, value: str) -> str:
        """Normalize surrounding whitespace and reject control-bearing names."""
        normalized = value.strip()
        if not normalized or any(ord(character) < 32 or ord(character) == 127 for character in normalized):
            raise ValueError("displayName must contain visible characters")
        return normalized

    @field_validator("capabilities", "fallback_profile_ids")
    @classmethod
    def profile_lists_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep saved Profile selection metadata deterministic."""
        if len(value) != len(set(value)):
            raise ValueError("entries must be unique")
        return value

    @field_validator("input_cost_per_million_usd", "output_cost_per_million_usd")
    @classmethod
    def profile_costs_must_be_decimal_text(cls, value: str | None) -> str | None:
        """Parse exact non-negative cost text before lifecycle orchestration."""
        if value is None:
            return None
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("cost must be decimal text or null") from exc
        if not parsed.is_finite() or parsed < 0:
            raise ValueError("cost must be finite and non-negative")
        return value


class ModelProfileCreateInput(ModelProfileWriteInput):
    """Create a Node-owned Profile whose immutable ID is generated by the Node."""


class ModelProfileUpdateInput(ModelProfileWriteInput):
    """Update one existing Profile under a required revision precondition."""

    profile_id: ResourceId
    expected_revision: Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ModelProviderInput(ActionInput):
    """Identify one registered model provider."""

    provider_id: Annotated[
        str,
        StringConstraints(min_length=1, max_length=63, pattern=r"^[a-z][a-z0-9_]*$"),
    ]


class SecretStatusInput(ActionInput):
    """Inspect one protected credential without resolving its value."""

    ref: SecretRef


class SecretPutInput(SecretStatusInput):
    """Write credential material through the protected SecretStore boundary."""

    value: SecretStr


class SetupApplyInput(ActionInput):
    """Apply one complete first-run baseline."""

    request: SetupApplyRequest


class SetupHelloInput(AgentReadInput):
    """Create a first Session and run one real model turn."""

    user_id: PrincipalId
    text: Annotated[str, StringConstraints(min_length=1, max_length=2000)] = "Hello"


class SessionNewInput(AgentReadInput):
    """Create one new Session for an Agent and authenticated principal."""

    user_id: PrincipalId


class SessionHistoryInput(AgentReadInput):
    """Read bounded visible conversation history for one Session."""

    user_id: PrincipalId
    session_id: RunId
    limit: StrictInt = Field(default=20, ge=1, le=100)


class SessionIdentityInput(AgentReadInput):
    """Identify one principal-scoped Session."""

    user_id: PrincipalId
    session_id: RunId


class SessionRenameInput(SessionIdentityInput):
    """Assign a user-facing title without modifying ADK event history."""

    title: Annotated[str, StringConstraints(min_length=1, max_length=120)]

    @field_validator("title")
    @classmethod
    def title_must_be_visible(cls, value: str) -> str:
        """Reject blank or control-bearing Session titles."""
        if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("title must contain visible text")
        return value.strip()


class SessionArchiveInput(SessionIdentityInput):
    """Set the recoverable archive state of one Session."""

    archived: bool


class SessionRewindInput(AgentReadInput):
    """Rewind one Session before an explicit or latest visible invocation."""

    user_id: PrincipalId
    session_id: RunId
    before_invocation_id: RunId | None = None


class RunStopInput(ActionInput):
    """Identify one active Run for cooperative cancellation."""

    run_id: RunId


class TaskListInput(ActionInput):
    """Read bounded durable Tasks, optionally scoped to one Session."""

    session_id: RunId | None = None
    limit: StrictInt = Field(default=20, ge=1, le=100)


class OperationsTaskIdentityInput(ActionInput):
    """Identify one durable Task owned by the Node."""

    task_id: RunId


class OperationsTaskControlInput(OperationsTaskIdentityInput):
    """Dispatch one runner-supported control operation for a durable Task."""

    action: Literal["interrupt", "cancel", "pause", "resume", "restart", "send_input"]
    content: Annotated[str, StringConstraints(max_length=8000)] = ""
    inline_budget_ms: StrictInt | None = Field(default=None, ge=100, le=300_000)

    @model_validator(mode="after")
    def content_matches_action(self) -> "OperationsTaskControlInput":
        """Require user input only for the explicit send-input action."""
        if self.action == "send_input" and not self.content.strip():
            raise ValueError("content is required for send_input")
        if self.action != "send_input" and self.content:
            raise ValueError("content is only valid for send_input")
        return self


class OperationsCronListInput(ActionInput):
    """Read Cron jobs and bounded recent history."""

    include_disabled: StrictBool = False
    history_limit: StrictInt = Field(default=20, ge=1, le=100)


class OperationsCronScheduleInput(ActionInput):
    """One strict interval, cron-expression, or one-shot schedule."""

    kind: Literal["every", "cron", "at"]
    every_seconds: StrictInt | None = Field(default=None, ge=1, le=31_536_000)
    cron_expression: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    at_ms: StrictInt | None = Field(default=None, ge=1)
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None

    @model_validator(mode="after")
    def exactly_one_schedule_shape(self) -> "OperationsCronScheduleInput":
        """Reject ambiguous or incomplete Cron schedule inputs."""
        present = {
            "every": self.every_seconds is not None,
            "cron": self.cron_expression is not None,
            "at": self.at_ms is not None,
        }
        if not present[self.kind] or sum(present.values()) != 1:
            raise ValueError("schedule fields must match kind exactly")
        if self.timezone is not None and self.kind != "cron":
            raise ValueError("timezone is only valid for cron schedules")
        return self


class OperationsCronCreateInput(ActionInput):
    """Create one Agent-scoped Node Cron job."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    agent_id: ResourceId
    user_id: PrincipalId
    message: Annotated[str, StringConstraints(min_length=1, max_length=8000)]
    schedule: OperationsCronScheduleInput
    delete_after_run: StrictBool = False


class OperationsCronIdentityInput(ActionInput):
    """Identify one Node-owned Cron job."""

    job_id: Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9._:-]+$")]


class OperationsCronUpdateInput(OperationsCronCreateInput, OperationsCronIdentityInput):
    """Replace mutable fields for one existing Cron job."""


class OperationsCronEnableInput(OperationsCronIdentityInput):
    """Set one Cron job enabled state."""

    enabled: StrictBool


class OperationsCronRunInput(OperationsCronIdentityInput):
    """Run one Cron job immediately."""

    force: StrictBool = False


class OperationsHeartbeatRunInput(ActionInput):
    """Trigger one Node heartbeat for an operator-provided reason."""

    reason: Annotated[str, StringConstraints(min_length=1, max_length=120)] = "manual"


class OperationsHeartbeatActiveHoursInput(ActionInput):
    """Optional local-time window for scheduled heartbeat turns."""

    start: Annotated[str, StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")] | None = None
    end: Annotated[str, StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")] | None = None
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=128)] = "user"

    @model_validator(mode="after")
    def complete_window(self) -> "OperationsHeartbeatActiveHoursInput":
        """Reject half-configured active-hour windows."""
        if (self.start is None) != (self.end is None):
            raise ValueError("heartbeat active hours must include both start and end")
        return self


class OperationsHeartbeatConfigureInput(ActionInput):
    """Persist one complete Node heartbeat policy."""

    enabled: StrictBool
    every_seconds: StrictInt = Field(ge=30, le=604800)
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=4000)]
    active_hours: OperationsHeartbeatActiveHoursInput = Field(default_factory=OperationsHeartbeatActiveHoursInput)


class OperationsUsageReadInput(ActionInput):
    """Read bounded Node-local model token usage."""

    limit: StrictInt = Field(default=20, ge=1, le=100)
    provider: ResourceId | None = None


class OperationsAuditListInput(ActionInput):
    """Read bounded redacted Action audit facts."""

    limit: StrictInt = Field(default=50, ge=1, le=200)
    actor_id: PrincipalId | None = None
    agent_id: ResourceId | None = None
    run_id: RunId | None = None
    extension_id: ResourceId | None = None
    action_id: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")] | None = None
    outcome: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None


ExtensionKind = Literal["plugin", "app", "mcp", "skill"]
InstallableExtensionKind = Literal["plugin", "skill"]
AgentEnablementKind = Literal["plugin", "mcp", "skill"]


class ExtensionListInput(ActionInput):
    """Optional Extension inventory filters."""

    kind: ExtensionKind | None = None
    agent_id: ResourceId | None = None


class ExtensionStarterListInput(ActionInput):
    """Filter the safe first-party Extension starter catalog."""

    kind: ExtensionKind | None = None
    query: Annotated[str, StringConstraints(max_length=256)] | None = None


class ExtensionStarterIdentityInput(ActionInput):
    """Identify one Extension starter catalog entry."""

    starter_id: ResourceId


class McpOAuthInput(ActionInput):
    """Identify one OAuth MCP server and optional browser callback origin."""

    server_id: ResourceId
    callback_base: Annotated[str, StringConstraints(min_length=8, max_length=2048)] | None = None


class ExtensionIdentityInput(ActionInput):
    """Identify one Extension resource."""

    kind: ExtensionKind
    extension_id: ResourceId


class ExtensionPreviewInput(ActionInput):
    """Stage and inspect one installable Skill or Product Plugin source."""

    kind: InstallableExtensionKind
    source: ExtensionSourceRef


class ExtensionInstallInput(ExtensionPreviewInput):
    """Install one source only if it still matches the confirmed preview digest."""

    expected_digest: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    expected_revision: str | None


class ExtensionEnablementInput(ActionInput):
    """Enable or disable one Agent-scoped Extension resource."""

    kind: AgentEnablementKind
    extension_id: ResourceId
    agent_id: ResourceId
    expected_revision: str


class ExtensionRemoveInput(ActionInput):
    """Remove one inactive directly managed or Product Plugin resource."""

    kind: AgentEnablementKind
    extension_id: ResourceId
    expected_revision: str


class PluginHookTrustInput(ActionInput):
    """Identify one installed Plugin and its optimistic revision for Hook trust."""

    plugin_id: ResourceId
    expected_revision: str


class PluginMarketplaceListInput(ActionInput):
    """Filter configured Plugin marketplace entries."""

    query: Annotated[str, StringConstraints(max_length=256)] | None = None


class PluginMarketplaceMutationInput(ActionInput):
    """Create or update one Plugin marketplace source."""

    marketplace_id: ResourceId
    spec: PluginMarketplaceSourceSpec
    expected_revision: str | None


class PluginMarketplaceIdentityInput(ActionInput):
    """Identify one Plugin marketplace source under optimistic concurrency."""

    marketplace_id: ResourceId
    expected_revision: str


class McpCreateInput(ActionInput):
    """Create one direct MCP resource."""

    resource: McpServer
    expected_revision: None = None


class McpUpdateInput(ActionInput):
    """Update one direct MCP resource."""

    resource: McpServer
    expected_revision: str


class McpTestInput(ActionInput):
    """Identify one direct MCP resource for a live connectivity test."""

    server_id: ResourceId


class AppDefinitionMutationInput(ActionInput):
    """Install or update one directly managed App definition."""

    resource: AppDefinition
    expected_revision: str | None


class AppDefinitionRemoveInput(ActionInput):
    """Remove one unreferenced direct App definition."""

    app_id: ResourceId
    expected_revision: str


class AppConnectionMutationInput(ActionInput):
    """Create or update one App connection without resolving Secret values."""

    resource: AppConnection
    expected_revision: str | None


class AppConnectionTestInput(ActionInput):
    """Identify one App connection for a live connectivity test."""

    connection_id: ResourceId


class AppConnectionIdentityInput(ActionInput):
    """Identify one App connection under an optimistic revision."""

    connection_id: ResourceId
    expected_revision: str


class AppConnectionEnablementInput(AppConnectionIdentityInput):
    """Enable or disable one App connection for an Agent."""

    agent_id: ResourceId


class AppConnectionReauthorizeInput(AppConnectionIdentityInput):
    """Replace protected credential references for one App connection."""

    credential_refs: dict[ResourceId, SecretRef]
