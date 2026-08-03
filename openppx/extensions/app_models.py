"""Strict product App definition and user connection resource models."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias
from urllib.parse import urlsplit

from pydantic import Field, StrictBool, StrictInt, StringConstraints, field_validator, model_validator

from openppx.config.models import DisplayName, ResourceMetadata, ResourceName, StrictConfigModel
from openppx.config.secrets import SecretRef

from .mcp_models import McpJobProtocolSpec
from .models import ExtensionSourceIdentity


VisibleValue: TypeAlias = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
ToolName: TypeAlias = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")]
EnvironmentName: TypeAlias = Annotated[str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")]
HeaderName: TypeAlias = Annotated[
    str,
    StringConstraints(pattern=r"^[!#$%&'*+.^_`|~0-9A-Za-z-]{1,128}$"),
]


def _require_visible(value: str) -> str:
    if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("value must contain visible characters")
    return value


class AppLiteralValue(StrictConfigModel):
    """Non-sensitive fixed value in an App transport template."""

    kind: Literal["literal"]
    value: Annotated[str, StringConstraints(max_length=4096)]

    @field_validator("value")
    @classmethod
    def value_cannot_contain_controls(cls, value: str) -> str:
        """Reject transport injection characters."""
        if any(character in value for character in "\r\n\0"):
            raise ValueError("literal value cannot contain line or NUL characters")
        return value


class AppCredentialValue(StrictConfigModel):
    """Named credential slot resolved through one AppConnection."""

    kind: Literal["credential"]
    credential_slot: ResourceName
    prefix: Annotated[str, StringConstraints(max_length=128)] = ""
    suffix: Annotated[str, StringConstraints(max_length=128)] = ""

    @field_validator("prefix", "suffix")
    @classmethod
    def decoration_cannot_contain_controls(cls, value: str) -> str:
        """Keep credential-rendered transport values on one line."""
        if any(character in value for character in "\r\n\0"):
            raise ValueError("credential decoration cannot contain line or NUL characters")
        return value


AppTemplateValue: TypeAlias = Annotated[
    AppLiteralValue | AppCredentialValue,
    Field(discriminator="kind"),
]


class AppStdioMcpTemplate(StrictConfigModel):
    """Local MCP transport template owned by an App definition."""

    type: Literal["stdio"]
    command: VisibleValue
    args: list[Annotated[str, StringConstraints(max_length=4096)]] = Field(default_factory=list)
    cwd: VisibleValue | None = None
    environment: dict[EnvironmentName, AppTemplateValue] = Field(default_factory=dict)

    @field_validator("command", "cwd")
    @classmethod
    def paths_must_be_visible(cls, value: str | None) -> str | None:
        """Reject blank or control-bearing executable paths."""
        return _require_visible(value) if value is not None else None

    @field_validator("args")
    @classmethod
    def args_are_bounded(cls, value: list[str]) -> list[str]:
        """Keep invocation argv-only and bounded."""
        if len(value) > 128 or any(any(character in item for character in "\0\r\n") for item in value):
            raise ValueError("args are invalid")
        return value

    @field_validator("environment")
    @classmethod
    def environment_is_bounded(cls, value: dict[str, AppTemplateValue]) -> dict[str, AppTemplateValue]:
        """Bound generated process environment."""
        if len(value) > 64:
            raise ValueError("environment exceeds the allowed entry count")
        return value


class AppRemoteMcpTemplate(StrictConfigModel):
    """Remote MCP transport template owned by an App definition."""

    type: Literal["streamable_http", "sse"]
    url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    headers: dict[HeaderName, AppTemplateValue] = Field(default_factory=dict)

    @field_validator("url")
    @classmethod
    def url_must_be_safe_http_endpoint(cls, value: str) -> str:
        """Reject credential-bearing or ambiguous endpoints."""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("url cannot contain control characters")
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError("url must be an absolute HTTP(S) endpoint")
        if parsed.username is not None or parsed.password is not None or parsed.query or parsed.fragment:
            raise ValueError("url cannot contain userinfo, query, or fragment")
        return value

    @field_validator("headers")
    @classmethod
    def headers_are_bounded(cls, value: dict[str, AppTemplateValue]) -> dict[str, AppTemplateValue]:
        """Bound generated request headers."""
        if len(value) > 64:
            raise ValueError("headers exceed the allowed entry count")
        return value


AppMcpTemplate: TypeAlias = Annotated[
    AppStdioMcpTemplate | AppRemoteMcpTemplate,
    Field(discriminator="type"),
]


class AppCredentialSpec(StrictConfigModel):
    """One protected credential required by an App definition."""

    name: ResourceName
    label: DisplayName
    required: StrictBool = True


class AppAuthSpec(StrictConfigModel):
    """Generic auth declaration; provider-specific browser flows remain adapters."""

    type: Literal["none", "secret", "oauth"]
    credentials: list[AppCredentialSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def auth_shape_must_be_consistent(self) -> "AppAuthSpec":
        """Require unique credential slots and a meaningful auth shape."""
        names = [credential.name for credential in self.credentials]
        if len(names) != len(set(names)):
            raise ValueError("credential slots must be unique")
        if self.type == "none" and self.credentials:
            raise ValueError("auth type none cannot declare credentials")
        if self.type != "none" and not self.credentials:
            raise ValueError("authenticated Apps must declare credential slots")
        return self


class AppToolSpec(StrictConfigModel):
    """Product-facing tool metadata and default security posture."""

    name: ToolName
    title: DisplayName
    description: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    access: Literal["read", "write"]
    risk: Literal["low", "medium", "high"] = "medium"
    enabled_by_default: StrictBool = True


class AppDefaultPolicy(StrictConfigModel):
    """Definition-owned defaults that a connection may only narrow."""

    require_confirmation: StrictBool = False
    progress_events: StrictBool = True
    long_task_proxy: StrictBool = True
    inline_budget_ms: StrictInt = Field(default=5000, ge=100, le=60_000)
    job_protocol: McpJobProtocolSpec | None = None


class AppOwnerRef(StrictConfigModel):
    """Optional Product Plugin owner for a declared App mapping."""

    kind: Literal["plugin"]
    name: ResourceName


class AppDefinitionSpec(StrictConfigModel):
    """Stable App identity, auth contract, tool catalog, and MCP template."""

    display_name: DisplayName
    description: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    category: Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
    developer: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    icon_url: Annotated[str, StringConstraints(max_length=2048)] | None = None
    source: ExtensionSourceIdentity
    auth: AppAuthSpec
    mcp: AppMcpTemplate
    tools: list[AppToolSpec] = Field(min_length=1, max_length=256)
    policy: AppDefaultPolicy = Field(default_factory=AppDefaultPolicy)
    managed_by: AppOwnerRef | None = None

    @field_validator("icon_url")
    @classmethod
    def icon_must_be_public_https(cls, value: str | None) -> str | None:
        """Keep branding references free of credentials and ambiguity."""
        if value is None:
            return None
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("iconUrl must be a clean HTTPS URL")
        return value

    @model_validator(mode="after")
    def references_must_match_declared_contract(self) -> "AppDefinitionSpec":
        """Validate tool identities and credential template references."""
        tool_names = [tool.name for tool in self.tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError("App tool names must be unique")
        declared = {credential.name for credential in self.auth.credentials}
        bindings = (
            self.mcp.environment.values()
            if isinstance(self.mcp, AppStdioMcpTemplate)
            else self.mcp.headers.values()
        )
        referenced = {
            binding.credential_slot
            for binding in bindings
            if isinstance(binding, AppCredentialValue)
        }
        if not referenced.issubset(declared):
            raise ValueError("MCP template references an undeclared credential slot")
        return self


class AppDefinition(StrictConfigModel):
    """Versioned product App definition owned by one Node."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["AppDefinition"]
    metadata: ResourceMetadata
    spec: AppDefinitionSpec


class AppConnectionSpec(StrictConfigModel):
    """User connection/auth instance separated from its App definition."""

    app_id: ResourceName
    display_name: DisplayName
    credential_refs: dict[ResourceName, SecretRef] = Field(default_factory=dict)
    enabled_tools: list[ToolName] | None = None
    require_confirmation: StrictBool = False
    enabled_agent_ids: list[ResourceName] = Field(default_factory=list)

    @field_validator("enabled_tools", "enabled_agent_ids")
    @classmethod
    def lists_must_be_unique(cls, value: list[str] | None) -> list[str] | None:
        """Keep connection policy and Agent projection deterministic."""
        if value is not None and len(value) != len(set(value)):
            raise ValueError("entries must be unique")
        return value


class AppConnection(StrictConfigModel):
    """Versioned App authorization and Agent-enablement resource."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["AppConnection"]
    metadata: ResourceMetadata
    spec: AppConnectionSpec


__all__ = [
    "AppAuthSpec",
    "AppConnection",
    "AppConnectionSpec",
    "AppCredentialSpec",
    "AppCredentialValue",
    "AppDefaultPolicy",
    "AppDefinition",
    "AppDefinitionSpec",
    "AppLiteralValue",
    "AppMcpTemplate",
    "AppOwnerRef",
    "AppRemoteMcpTemplate",
    "AppStdioMcpTemplate",
    "AppTemplateValue",
    "AppToolSpec",
]
