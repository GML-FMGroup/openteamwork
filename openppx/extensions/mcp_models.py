"""Strict direct MCP Server resource models."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias
from urllib.parse import urlsplit

from pydantic import (
    Field,
    JsonValue,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)

from openppx.config.models import DisplayName, ResourceMetadata, ResourceName, StrictConfigModel
from openppx.config.secrets import SecretRef


VisibleValue: TypeAlias = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
ToolName: TypeAlias = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
ToolPrefix: TypeAlias = Annotated[str, StringConstraints(pattern=r"^[A-Za-z][A-Za-z0-9_]{0,127}$")]
EnvironmentName: TypeAlias = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
HeaderName: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$"),
]
QueryName: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.~-]{0,127}$"),
]


def _require_visible(value: str) -> str:
    """Reject blank and control-bearing persisted transport values."""
    if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("value must contain visible characters")
    return value


class McpLiteralValue(StrictConfigModel):
    """Non-sensitive literal passed to one MCP transport."""

    kind: Literal["literal"]
    value: Annotated[str, StringConstraints(max_length=4096)]

    @field_validator("value")
    @classmethod
    def value_cannot_contain_controls(cls, value: str) -> str:
        """Prevent line-based environment/header injection."""
        if any(character in value for character in "\r\n\0"):
            raise ValueError("literal value cannot contain line or NUL characters")
        return value


class McpSecretValue(StrictConfigModel):
    """Protected value resolved only while constructing a Runtime toolset."""

    kind: Literal["secret"]
    secret_ref: SecretRef
    prefix: Annotated[str, StringConstraints(max_length=128)] = ""
    suffix: Annotated[str, StringConstraints(max_length=128)] = ""

    @field_validator("prefix", "suffix")
    @classmethod
    def decoration_cannot_contain_controls(cls, value: str) -> str:
        """Keep rendered transport values on one line."""
        if any(character in value for character in "\r\n\0"):
            raise ValueError("secret decoration cannot contain line or NUL characters")
        return value


class McpEnvironmentValue(StrictConfigModel):
    """Value read from the Node process environment only at Runtime assembly."""

    kind: Literal["environment"]
    name: EnvironmentName
    prefix: Annotated[str, StringConstraints(max_length=128)] = ""
    suffix: Annotated[str, StringConstraints(max_length=128)] = ""

    @field_validator("prefix", "suffix")
    @classmethod
    def decoration_cannot_contain_controls(cls, value: str) -> str:
        """Keep rendered environment values on one line."""
        if any(character in value for character in "\r\n\0"):
            raise ValueError("environment decoration cannot contain line or NUL characters")
        return value


McpValueBinding: TypeAlias = Annotated[
    McpLiteralValue | McpSecretValue | McpEnvironmentValue,
    Field(discriminator="kind"),
]


class McpStdioTransport(StrictConfigModel):
    """Argv-only local MCP Server process configuration."""

    type: Literal["stdio"]
    command: VisibleValue
    args: list[Annotated[str, StringConstraints(max_length=4096)]] = Field(default_factory=list)
    cwd: VisibleValue | None = None
    environment: dict[EnvironmentName, McpValueBinding] = Field(default_factory=dict)

    @field_validator("command", "cwd")
    @classmethod
    def paths_must_be_visible(cls, value: str | None) -> str | None:
        """Reject shell control characters in executable and cwd fields."""
        return _require_visible(value) if value is not None else None

    @field_validator("args")
    @classmethod
    def args_are_bounded_and_control_free(cls, value: list[str]) -> list[str]:
        """Keep subprocess invocation bounded and strictly argv-based."""
        if len(value) > 128:
            raise ValueError("args exceed the allowed count")
        if any(any(character in item for character in "\0\r\n") for item in value):
            raise ValueError("args cannot contain line or NUL characters")
        return value

    @field_validator("environment")
    @classmethod
    def environment_is_bounded(cls, value: dict[str, McpValueBinding]) -> dict[str, McpValueBinding]:
        """Bound environment expansion before process construction."""
        if len(value) > 64:
            raise ValueError("environment exceeds the allowed entry count")
        return value


class McpRemoteTransport(StrictConfigModel):
    """Remote Streamable HTTP or legacy SSE MCP connection."""

    type: Literal["streamable_http", "sse"]
    url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    headers: dict[HeaderName, McpValueBinding] = Field(default_factory=dict)
    query: dict[QueryName, McpValueBinding] = Field(default_factory=dict)
    auth: Literal["none", "oauth"] = "none"

    @field_validator("url")
    @classmethod
    def url_must_be_safe_http_endpoint(cls, value: str) -> str:
        """Reject credential-bearing or ambiguous remote MCP URLs."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("url cannot contain control characters")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an absolute HTTP(S) endpoint")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("url userinfo is not allowed; use a SecretRef header")
        if parsed.query:
            raise ValueError("url query is not allowed; use an explicit query binding")
        if parsed.fragment:
            raise ValueError("url fragment is not allowed")
        return value

    @field_validator("headers")
    @classmethod
    def headers_are_bounded(cls, value: dict[str, McpValueBinding]) -> dict[str, McpValueBinding]:
        """Bound static header construction."""
        if len(value) > 64:
            raise ValueError("headers exceed the allowed entry count")
        return value

    @field_validator("query")
    @classmethod
    def query_is_bounded(cls, value: dict[str, McpValueBinding]) -> dict[str, McpValueBinding]:
        """Keep protected runtime query expansion bounded and explicit."""
        if len(value) > 64:
            raise ValueError("query exceeds the allowed entry count")
        return value


McpTransport: TypeAlias = Annotated[
    McpStdioTransport | McpRemoteTransport,
    Field(discriminator="type"),
]


class McpJobProtocolSpec(StrictConfigModel):
    """Strict persisted form of OpenPPX's explicit external-job protocol."""

    job_id_path: VisibleValue
    status_tool: ToolName
    status_args: dict[str, JsonValue] = Field(default_factory=lambda: {"job_id": "{job_id}"})
    status_result_path: Annotated[str, StringConstraints(max_length=512)] = ""
    output_tool: ToolName | None = None
    output_args: dict[str, JsonValue] = Field(default_factory=lambda: {"job_id": "{job_id}"})
    output_result_path: Annotated[str, StringConstraints(max_length=512)] = ""
    cancel_tool: ToolName | None = None
    cancel_args: dict[str, JsonValue] = Field(default_factory=lambda: {"job_id": "{job_id}"})
    cancel_result_path: Annotated[str, StringConstraints(max_length=512)] = ""
    pause_tool: ToolName | None = None
    pause_args: dict[str, JsonValue] = Field(default_factory=lambda: {"job_id": "{job_id}"})
    pause_result_path: Annotated[str, StringConstraints(max_length=512)] = ""
    resume_tool: ToolName | None = None
    resume_args: dict[str, JsonValue] = Field(default_factory=lambda: {"job_id": "{job_id}"})
    resume_result_path: Annotated[str, StringConstraints(max_length=512)] = ""
    checkpoint_path: Annotated[str, StringConstraints(max_length=512)] = ""
    checkpoint_schema: Annotated[str, StringConstraints(max_length=256)] = ""
    checkpoint_schema_version: StrictInt | None = Field(default=None, ge=1)
    poll_timeout_ms: StrictInt = Field(default=5000, ge=100, le=60_000)

    @field_validator(
        "status_args",
        "output_args",
        "cancel_args",
        "pause_args",
        "resume_args",
    )
    @classmethod
    def args_are_bounded(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Prevent job control templates from becoming unbounded payload stores."""
        if len(value) > 64:
            raise ValueError("job protocol args exceed the allowed entry count")
        return value


RuntimeHeaderSource: TypeAlias = Annotated[str, StringConstraints(min_length=1, max_length=256)]


class McpToolPolicy(StrictConfigModel):
    """Tool naming, filtering, confirmation, progress, and task behavior."""

    tool_filter: list[ToolName] = Field(default_factory=list)
    disabled_tools: list[ToolName] = Field(default_factory=list)
    tool_name_prefix: ToolPrefix | None = None
    require_confirmation: StrictBool = False
    runtime_headers: dict[HeaderName, RuntimeHeaderSource] = Field(default_factory=dict)
    progress_events: StrictBool = True
    long_task_proxy: StrictBool = True
    inline_budget_ms: StrictInt = Field(default=5000, ge=100, le=60_000)
    job_protocol: McpJobProtocolSpec | None = None

    @field_validator("tool_filter", "disabled_tools")
    @classmethod
    def tool_filter_is_unique(cls, value: list[str]) -> list[str]:
        """Reject ambiguous duplicate tool policy entries."""
        if len(value) != len(set(value)):
            raise ValueError("toolFilter entries must be unique")
        if len(value) > 256:
            raise ValueError("toolFilter exceeds the allowed entry count")
        return value

    @model_validator(mode="after")
    def include_and_exclude_are_mutually_exclusive(self) -> "McpToolPolicy":
        """Keep the standard MCP allowlist and denylist semantics unambiguous."""
        if self.tool_filter and self.disabled_tools:
            raise ValueError("toolFilter and disabledTools cannot both be configured")
        return self

    @field_validator("runtime_headers")
    @classmethod
    def runtime_header_sources_are_supported(cls, value: dict[str, str]) -> dict[str, str]:
        """Allow only context fields understood by the runtime adapter."""
        exact = {"user_id", "session_id", "app_name", "invocation_id", "agent_name"}
        prefixes = ("metadata.", "custom_metadata.", "run_metadata.", "state.", "session.", "literal:")
        if len(value) > 32 or any(source not in exact and not source.startswith(prefixes) for source in value.values()):
            raise ValueError("runtimeHeaders contains an unsupported source")
        return value

    def resolved_prefix(self, server_id: str) -> str:
        """Return the stable ADK tool-name prefix for one resource."""
        return (self.tool_name_prefix or f"mcp_{server_id.replace('-', '_')}").rstrip("_")


class McpOwnerRef(StrictConfigModel):
    """Resource owner used by later Plugin/App projections."""

    kind: Literal["plugin", "app"]
    name: ResourceName


class McpServerSpec(StrictConfigModel):
    """Persistent direct MCP definition and Agent enablement."""

    display_name: DisplayName
    description: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    transport: McpTransport
    policy: McpToolPolicy = Field(default_factory=McpToolPolicy)
    risk: Literal["low", "medium", "high"] = "medium"
    enabled_agent_ids: list[ResourceName] = Field(default_factory=list)
    managed_by: McpOwnerRef | None = None

    @field_validator("enabled_agent_ids")
    @classmethod
    def enabled_agents_are_unique(cls, value: list[str]) -> list[str]:
        """Keep Agent projection deterministic."""
        if len(value) != len(set(value)):
            raise ValueError("enabledAgentIds entries must be unique")
        return value


class McpServer(StrictConfigModel):
    """Versioned Node-owned direct MCP Server resource."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["McpServer"]
    metadata: ResourceMetadata
    spec: McpServerSpec


__all__ = [
    "McpEnvironmentValue",
    "McpJobProtocolSpec",
    "McpLiteralValue",
    "McpOwnerRef",
    "McpRemoteTransport",
    "McpSecretValue",
    "McpServer",
    "McpServerSpec",
    "McpStdioTransport",
    "McpToolPolicy",
    "McpTransport",
    "McpValueBinding",
]
