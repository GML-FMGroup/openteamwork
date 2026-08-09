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
GoalId = Annotated[str, StringConstraints(pattern=r"^goal_[a-f0-9]{20}$")]
FlowId = Annotated[str, StringConstraints(pattern=r"^flow_[a-f0-9]{20}$")]
AutomationId = Annotated[str, StringConstraints(pattern=r"^auto_[a-f0-9]{20}$")]
AutomationRunId = Annotated[str, StringConstraints(pattern=r"^arun_[a-f0-9]{20}$")]
GoalStatus = Literal["active", "waiting", "paused", "blocked", "completed", "cancelled", "failed"]
TaskFlowStepStatus = Literal["pending", "running", "waiting", "blocked", "completed", "cancelled", "failed"]


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


class GoalCreateInput(ActionInput):
    """Create one durable Goal bound to an Agent Session."""

    user_id: PrincipalId
    agent_id: ResourceId
    session_id: RunId
    objective: Annotated[str, StringConstraints(min_length=1, max_length=16_384)]
    completion_criteria: list[Annotated[str, StringConstraints(min_length=1, max_length=2_048)]] = Field(
        default_factory=list,
        max_length=50,
    )
    constraints: list[Annotated[str, StringConstraints(min_length=1, max_length=2_048)]] = Field(
        default_factory=list,
        max_length=50,
    )
    workspace_ref: Annotated[str, StringConstraints(max_length=1_024)] = ""
    budget_policy: dict[str, object] = Field(default_factory=dict)

    @field_validator("objective", "workspace_ref")
    @classmethod
    def goal_text_must_not_contain_controls(cls, value: str) -> str:
        """Normalize user-visible Goal text and reject hidden controls."""
        normalized = value.strip()
        if any((ord(character) < 32 and character not in {"\n", "\t"}) or ord(character) == 127 for character in normalized):
            raise ValueError("value must not contain control characters")
        return normalized


class GoalIdentityInput(ActionInput):
    """Identify one Goal while retaining its requesting principal."""

    goal_id: GoalId
    user_id: PrincipalId


class GoalListInput(ActionInput):
    """Filter visible Goals without exposing storage queries."""

    user_id: PrincipalId
    session_id: RunId | None = None
    statuses: list[GoalStatus] = Field(default_factory=list, max_length=7)
    limit: StrictInt = Field(default=20, ge=1, le=200)

    @field_validator("statuses")
    @classmethod
    def goal_statuses_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep Goal filters deterministic."""
        if len(value) != len(set(value)):
            raise ValueError("statuses entries must be unique")
        return value


class GoalUpdateInput(GoalIdentityInput):
    """Edit mutable Goal policy fields with optimistic concurrency."""

    expected_revision: StrictInt = Field(ge=1)
    objective: Annotated[str, StringConstraints(min_length=1, max_length=16_384)] | None = None
    completion_criteria: list[Annotated[str, StringConstraints(min_length=1, max_length=2_048)]] | None = Field(
        default=None,
        max_length=50,
    )
    constraints: list[Annotated[str, StringConstraints(min_length=1, max_length=2_048)]] | None = Field(
        default=None,
        max_length=50,
    )
    budget_policy: dict[str, object] | None = None


class CompletionEvidenceInput(ActionInput):
    """Reference one independently persisted completion fact."""

    type: Literal["task_run", "artifact", "delivery", "user_confirmation"]
    ref: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    label: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    run_id: RunId | None = None
    mime_type: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    version: StrictInt | None = Field(default=None, ge=0)
    criteria: list[Annotated[str, StringConstraints(min_length=1, max_length=2_048)]] = Field(
        default_factory=list,
        max_length=50,
    )
    criterion_indexes: list[StrictInt] = Field(default_factory=list, max_length=50)


class GoalTransitionInput(GoalIdentityInput):
    """Request an explicit non-completion Goal lifecycle transition."""

    expected_revision: StrictInt = Field(ge=1)
    reason: Annotated[str, StringConstraints(max_length=2_048)] = ""


class GoalRetryStepInput(GoalIdentityInput):
    """Retry one recoverable TaskFlow step under optimistic concurrency."""

    expected_revision: StrictInt = Field(ge=1)
    step_id: ResourceId | None = None


class GoalCompleteInput(GoalTransitionInput):
    """Complete one Goal using evidence or explicit user confirmation."""

    completion_evidence: list[CompletionEvidenceInput] = Field(default_factory=list, max_length=100)
    user_confirmed: StrictBool = False


class GoalHistoryInput(GoalIdentityInput):
    """Read the append-only event history for one visible Goal."""

    limit: StrictInt = Field(default=100, ge=1, le=500)


class GoalCommandInput(ActionInput):
    """Typed `/goal` command request projected to the Goal domain."""

    user_id: PrincipalId
    agent_id: ResourceId
    session_id: RunId
    operation: Literal["status", "create", "update", "pause", "resume", "retry", "complete", "cancel", "history"]
    text: Annotated[str, StringConstraints(max_length=16_384)] = ""


class TaskFlowIdentityInput(ActionInput):
    """Identify one TaskFlow and its requesting principal."""

    flow_id: FlowId
    user_id: PrincipalId


class TaskFlowListInput(ActionInput):
    """List TaskFlows belonging to one visible Goal."""

    goal_id: GoalId
    user_id: PrincipalId
    limit: StrictInt = Field(default=20, ge=1, le=200)


class TaskFlowStepInput(ActionInput):
    """One declarative TaskFlow step; execution remains a TaskRun fact."""

    step_id: ResourceId
    title: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    description: Annotated[str, StringConstraints(max_length=4_096)] = ""
    status: TaskFlowStepStatus = "pending"
    depends_on: list[ResourceId] = Field(default_factory=list, max_length=50)
    completion_criteria: list[Annotated[str, StringConstraints(min_length=1, max_length=1_024)]] = Field(
        default_factory=list,
        max_length=20,
    )


class TaskFlowUpdateInput(TaskFlowIdentityInput):
    """Replace a TaskFlow plan under optimistic concurrency."""

    expected_revision: StrictInt = Field(ge=1)
    steps: list[TaskFlowStepInput] = Field(max_length=200)


class TaskFlowAdvanceInput(TaskFlowIdentityInput):
    """Advance one TaskFlow step after dependency checks."""

    expected_revision: StrictInt = Field(ge=1)
    step_id: ResourceId
    status: TaskFlowStepStatus


class TaskFlowBindTaskInput(TaskFlowIdentityInput):
    """Bind an existing durable TaskRun reference to a Flow step."""

    expected_revision: StrictInt = Field(ge=1)
    step_id: ResourceId
    task_id: RunId


class TaskFlowFinishInput(TaskFlowIdentityInput):
    """Finish a TaskFlow only when all declared steps are complete."""

    expected_revision: StrictInt = Field(ge=1)


class AutomationScheduleInput(ActionInput):
    """One schedule trigger definition owned by an Automation."""

    kind: Literal["every", "cron", "at"]
    every_seconds: StrictInt | None = Field(default=None, ge=60, le=31_536_000)
    cron_expr: Annotated[str, StringConstraints(max_length=128)] = ""
    at_ms: StrictInt | None = Field(default=None, ge=1)
    timezone: Annotated[str, StringConstraints(max_length=128)] = ""

    @model_validator(mode="after")
    def exactly_one_schedule_shape(self) -> "AutomationScheduleInput":
        """Reject ambiguous or incomplete schedule configurations."""
        if self.kind == "every" and self.every_seconds is None:
            raise ValueError("everySeconds is required for an every schedule")
        if self.kind == "cron" and not self.cron_expr.strip():
            raise ValueError("cronExpr is required for a cron schedule")
        if self.kind == "at" and self.at_ms is None:
            raise ValueError("atMs is required for an at schedule")
        return self


class AutomationLocalEventInput(ActionInput):
    """One explicitly declared trusted-LAN event trigger."""

    event_key: ResourceId
    input_schema: dict[str, object] = Field(
        default_factory=lambda: {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
    )

    @field_validator("input_schema")
    @classmethod
    def schema_must_be_bounded_object(cls, value: dict[str, object]) -> dict[str, object]:
        """Accept the small JSON Schema subset enforced by the Automation runtime."""
        if value.get("type") != "object":
            raise ValueError("local event inputSchema.type must be object")
        properties = value.get("properties", {})
        required = value.get("required", [])
        if not isinstance(properties, dict) or len(properties) > 64:
            raise ValueError("local event inputSchema properties must contain at most 64 fields")
        if not isinstance(required, list) or len(required) > 64 or any(not isinstance(item, str) for item in required):
            raise ValueError("local event inputSchema required must be a string array")
        if not set(required).issubset(properties):
            raise ValueError("local event required fields must exist in properties")
        if value.get("additionalProperties", False) not in {True, False}:
            raise ValueError("local event additionalProperties must be boolean")
        return value


class AutomationConcurrencyPolicyInput(ActionInput):
    """Deterministic behavior when a previous Run is still active."""

    mode: Literal["skip", "queue-one", "parallel-with-limit"] = "skip"
    limit: StrictInt = Field(default=1, ge=1, le=16)

    @model_validator(mode="after")
    def valid_limit(self) -> "AutomationConcurrencyPolicyInput":
        """Keep serial modes unambiguous and bounded."""
        if self.mode in {"skip", "queue-one"} and self.limit != 1:
            raise ValueError("limit must be 1 for skip and queue-one")
        return self


class AutomationMissedRunPolicyInput(ActionInput):
    """Bounded policy for occurrences missed while the Node was offline."""

    mode: Literal["skip", "run-latest", "bounded-catch-up"] = "run-latest"
    max_catch_up: StrictInt = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def valid_catch_up(self) -> "AutomationMissedRunPolicyInput":
        """Only bounded catch-up may request more than one occurrence."""
        if self.mode != "bounded-catch-up" and self.max_catch_up != 1:
            raise ValueError("maxCatchUp must be 1 unless mode is bounded-catch-up")
        return self


class AutomationRetryPolicyInput(ActionInput):
    """Bounded retry policy for retryable runtime failures."""

    max_attempts: StrictInt = Field(default=1, ge=1, le=5)
    backoff_seconds: StrictInt = Field(default=30, ge=0, le=3_600)


class AutomationBudgetPolicyInput(ActionInput):
    """Per-run safety budget enforced around the normal ADK turn."""

    timeout_seconds: StrictInt = Field(default=1_800, ge=1, le=86_400)
    max_tool_calls: StrictInt | None = Field(default=None, ge=1, le=10_000)
    max_input_tokens: StrictInt | None = Field(default=None, ge=1, le=10_000_000)
    max_output_tokens: StrictInt | None = Field(default=None, ge=1, le=10_000_000)


class AutomationMonitorPolicyInput(ActionInput):
    """Quiet-completion rules for monitor-style Automations."""

    enabled: StrictBool = False
    notify_on_change_only: StrictBool = True
    stop_when_contains: Annotated[str, StringConstraints(max_length=1_024)] = ""


class AutomationIdentityInput(ActionInput):
    """Identify one user-owned Automation."""

    automation_id: AutomationId
    user_id: PrincipalId


class AutomationListInput(ActionInput):
    """List user-created Automations without runtime infrastructure records."""

    user_id: PrincipalId
    statuses: list[Literal["active", "paused", "blocked"]] = Field(default_factory=list, max_length=3)
    limit: StrictInt = Field(default=100, ge=1, le=200)


class AutomationCreateInput(ActionInput):
    """Create one durable Automation Definition and optional schedule."""

    user_id: PrincipalId
    agent_id: ResourceId
    name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    description: Annotated[str, StringConstraints(max_length=1_024)] = ""
    instructions: Annotated[str, StringConstraints(min_length=1, max_length=16_384)]
    output_requirements: list[Annotated[str, StringConstraints(min_length=1, max_length=1_024)]] = Field(default_factory=list, max_length=50)
    workspace_ref: Annotated[str, StringConstraints(max_length=1_024)] = ""
    context_mode: Literal["isolated", "rolling", "session"] = "isolated"
    model_profile_ref: ResourceId | None = None
    extension_policy: dict[str, object] = Field(default_factory=dict)
    permission_policy: dict[str, object] = Field(default_factory=dict)
    permissions_confirmed: StrictBool = False
    delivery_policy: dict[str, object] = Field(default_factory=dict)
    concurrency_policy: AutomationConcurrencyPolicyInput = Field(default_factory=AutomationConcurrencyPolicyInput)
    missed_run_policy: AutomationMissedRunPolicyInput = Field(default_factory=AutomationMissedRunPolicyInput)
    retry_policy: AutomationRetryPolicyInput = Field(default_factory=AutomationRetryPolicyInput)
    budget_policy: AutomationBudgetPolicyInput = Field(default_factory=AutomationBudgetPolicyInput)
    monitor_policy: AutomationMonitorPolicyInput = Field(default_factory=AutomationMonitorPolicyInput)
    schedule: AutomationScheduleInput | None = None
    local_event: AutomationLocalEventInput | None = None

    @model_validator(mode="after")
    def only_one_automatic_trigger(self) -> "AutomationCreateInput":
        """Keep the first version's automatic trigger model unambiguous."""
        if self.schedule is not None and self.local_event is not None:
            raise ValueError("schedule and localEvent cannot both be configured")
        return self


class AutomationUpdateInput(AutomationIdentityInput):
    """Edit one Automation under optimistic concurrency."""

    expected_revision: StrictInt = Field(ge=1)
    agent_id: ResourceId | None = None
    name: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    description: Annotated[str, StringConstraints(max_length=1_024)] | None = None
    instructions: Annotated[str, StringConstraints(min_length=1, max_length=16_384)] | None = None
    output_requirements: list[Annotated[str, StringConstraints(min_length=1, max_length=1_024)]] | None = Field(default=None, max_length=50)
    workspace_ref: Annotated[str, StringConstraints(max_length=1_024)] | None = None
    context_mode: Literal["isolated", "rolling", "session"] | None = None
    model_profile_ref: ResourceId | None = None
    extension_policy: dict[str, object] | None = None
    permission_policy: dict[str, object] | None = None
    delivery_policy: dict[str, object] | None = None
    concurrency_policy: AutomationConcurrencyPolicyInput | None = None
    missed_run_policy: AutomationMissedRunPolicyInput | None = None
    retry_policy: AutomationRetryPolicyInput | None = None
    budget_policy: AutomationBudgetPolicyInput | None = None
    monitor_policy: AutomationMonitorPolicyInput | None = None
    schedule: AutomationScheduleInput | None = None
    local_event: AutomationLocalEventInput | None = None

    @model_validator(mode="after")
    def only_one_automatic_trigger(self) -> "AutomationUpdateInput":
        """Reject an update that attempts to install two automatic triggers."""
        if self.schedule is not None and self.local_event is not None:
            raise ValueError("schedule and localEvent cannot both be configured")
        return self


class AutomationTransitionInput(AutomationIdentityInput):
    """Pause, resume, or delete one Automation revision."""

    expected_revision: StrictInt = Field(ge=1)


class AutomationRunInput(AutomationIdentityInput):
    """Start one independent Automation Run now."""

    input: dict[str, object] = Field(default_factory=dict)


class AutomationHistoryInput(AutomationIdentityInput):
    """Read Automation runs and append-only events."""

    limit: StrictInt = Field(default=50, ge=1, le=200)


class AutomationRunReadInput(ActionInput):
    """Read one Automation Run under owner visibility."""

    automation_run_id: AutomationRunId
    user_id: PrincipalId


class AutomationTriggerInput(AutomationIdentityInput):
    """Submit one trusted local typed event through the normal run path."""

    event_key: ResourceId
    event_id: RunId
    input: dict[str, object] = Field(default_factory=dict)


class AutomationTemplateReadInput(ActionInput):
    """Identify one built-in reviewed Automation template."""

    template_id: ResourceId


class AutomationPermissionRevokeInput(AutomationIdentityInput):
    """Revoke Automation-specific standing permissions."""

    expected_revision: StrictInt = Field(ge=1)


class AutomationCommandInput(ActionInput):
    """Typed `/automation` command request for the current principal."""

    user_id: PrincipalId
    agent_id: ResourceId
    operation: Literal["list", "create", "show", "run", "pause", "resume", "history", "delete"]
    target: Annotated[str, StringConstraints(max_length=256)] = ""
    text: Annotated[str, StringConstraints(max_length=16_384)] = ""


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


class ModelSessionCommandInput(SessionIdentityInput):
    """Inspect or mutate the Model Profile override for one Session's future Runs."""

    operation: Literal["list", "status", "select", "reset"] = "list"
    profile_id: ResourceId | None = None
    expected_revision: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def selection_requires_profile(self) -> "ModelSessionCommandInput":
        """Keep command and direct Action inputs semantically identical."""
        if self.operation == "select" and self.profile_id is None:
            raise ValueError("profileId is required for select")
        if self.operation != "select" and self.profile_id is not None:
            raise ValueError("profileId is only valid for select")
        return self


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


class RuntimeInspectInput(ActionInput):
    """Select bounded, non-sensitive Runtime facts for diagnostics."""

    user_id: PrincipalId | None = None
    agent_id: ResourceId | None = None
    session_id: RunId | None = None
    run_id: RunId | None = None
    limit: StrictInt = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def resource_context_is_consistent(self) -> "RuntimeInspectInput":
        """Require an Agent whenever Session-scoped facts are requested."""
        if self.session_id is not None and self.agent_id is None:
            raise ValueError("agentId is required when sessionId is provided")
        return self


class TaskListInput(ActionInput):
    """Read bounded durable Tasks, optionally scoped to one Session."""

    session_id: RunId | None = None
    limit: StrictInt = Field(default=20, ge=1, le=100)


class TaskCommandInput(ActionInput):
    """Typed `/task` request over the existing durable TaskController facts."""

    operation: Literal["list", "show", "pause", "resume", "cancel", "retry", "output", "artifacts"]
    task_id: RunId | None = None
    session_id: RunId | None = None
    limit: StrictInt = Field(default=20, ge=1, le=100)

    @model_validator(mode="after")
    def operation_matches_identity(self) -> "TaskCommandInput":
        """Require a Task identity for every operation except list."""
        if self.operation == "list" and self.task_id is not None:
            raise ValueError("task_id is not valid for list")
        if self.operation != "list" and self.task_id is None:
            raise ValueError("task_id is required for this operation")
        return self


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


class PermissionAuditListInput(ActionInput):
    """Read bounded redacted Agent permission decisions."""

    limit: StrictInt = Field(default=50, ge=1, le=200)
    agent_id: ResourceId | None = None
    object: Literal["workspace", "external_path", "command", "process", "network", "tool"] | None = None
    outcome: Literal["allow", "deny", "requires_approval"] | None = None
    permission_revision: Annotated[
        str,
        StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$"),
    ] | None = None


ExtensionKind = Literal["plugin", "app", "mcp", "skill"]
InstallableExtensionKind = Literal["plugin", "skill"]
AgentEnablementKind = Literal["plugin", "mcp", "skill"]


class ExtensionListInput(ActionInput):
    """Optional Extension inventory filters."""

    kind: ExtensionKind | None = None
    agent_id: ResourceId | None = None


class SkillCommandInput(ActionInput):
    """Select one Agent-visible Skill for a normal ADK turn."""

    agent_id: ResourceId | None = None
    skill_name: ResourceId | None = None
    instruction: Annotated[str, StringConstraints(max_length=16_384)] = ""


class MakeSkillCommandInput(ActionInput):
    """Create, revise, approve, or cancel one current-Session Skill draft."""

    operation: Literal["create", "revise", "approve", "cancel"] = "create"
    agent_id: ResourceId
    user_id: PrincipalId
    session_id: RunId
    focus: Annotated[str, StringConstraints(max_length=2_000)] = ""
    revision_notes: Annotated[str, StringConstraints(max_length=2_000)] | None = None

    @model_validator(mode="after")
    def fields_match_operation(self) -> "MakeSkillCommandInput":
        """Reject ambiguous command payloads before service dispatch."""
        if self.operation == "revise" and not (self.revision_notes or "").strip():
            raise ValueError("revisionNotes is required for revise")
        if self.operation != "revise" and self.revision_notes is not None:
            raise ValueError("revisionNotes is only valid for revise")
        if self.operation != "create" and self.focus:
            raise ValueError("focus is only valid for create")
        return self


class ExtensionStarterListInput(ActionInput):
    """Filter the safe first-party Extension starter catalog."""

    kind: ExtensionKind | None = None
    query: Annotated[str, StringConstraints(max_length=256)] | None = None


class ExtensionStarterIdentityInput(ActionInput):
    """Identify one Extension starter catalog entry."""

    starter_id: ResourceId


class ExtensionHealthHistoryInput(ActionInput):
    """Read bounded connection-test history for one executable Extension."""

    kind: Literal["mcp", "app_connection"]
    extension_id: ResourceId
    limit: StrictInt = Field(default=20, ge=1, le=50)


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
