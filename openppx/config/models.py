"""Strict Pydantic models for OpenPPX Node and Agent resources."""

from __future__ import annotations

import ipaddress
import re
from typing import Annotated, Literal, TypeAlias

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel

from openppx.permissions.models import AgentPermissionSpec, NodePermissionSpec


ResourceName: TypeAlias = Annotated[
    str,
    StringConstraints(
        min_length=1,
        max_length=63,
        pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$",
    ),
]

_PRIVILEGE_ALIASES: dict[str, str] = {
    "low": "low",
    "minimal": "low",
    "medium": "medium",
    "standard": "medium",
    "high": "high",
    "root": "root",
}


def normalize_agent_privilege_level(value: object, *, default: str = "low") -> str:
    """Normalize one public privilege label into the strict Agent vocabulary."""
    raw = str(value or "").strip().lower()
    if not raw:
        return default
    canonical = _PRIVILEGE_ALIASES.get(raw)
    if canonical is None:
        choices = ", ".join(sorted(_PRIVILEGE_ALIASES))
        raise ValueError(f"unsupported agent privilege level '{raw}'; expected one of: {choices}")
    return canonical


def _visible_text(value: str) -> str:
    """Require meaningful display/path text without control characters."""
    if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("value must contain visible characters")
    return value


DisplayName: TypeAlias = Annotated[
    str,
    StringConstraints(min_length=1, max_length=80),
    AfterValidator(_visible_text),
]


class StrictConfigModel(BaseModel):
    """Base model for strict, camel-cased persisted resources."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        loc_by_alias=True,
        populate_by_name=True,
        strict=True,
    )


class ResourceMetadata(StrictConfigModel):
    """Identity and bounded open metadata shared by all resources."""

    name: ResourceName
    labels: dict[
        Annotated[str, StringConstraints(min_length=1, max_length=63)],
        Annotated[str, StringConstraints(max_length=128)],
    ] = Field(default_factory=dict)
    annotations: dict[
        Annotated[str, StringConstraints(min_length=1, max_length=128)],
        Annotated[str, StringConstraints(max_length=2048)],
    ] = Field(default_factory=dict)

    @field_validator("annotations")
    @classmethod
    def annotations_must_be_bounded(cls, value: dict[str, str]) -> dict[str, str]:
        """Prevent the open annotation namespace from becoming unbounded storage."""
        if sum(len(key.encode("utf-8")) + len(item.encode("utf-8")) for key, item in value.items()) > 8192:
            raise ValueError("annotations exceed the allowed total size")
        return value


class NodeClientApiSpec(StrictConfigModel):
    """Client API listener settings for one Node."""

    listen_host: Annotated[str, StringConstraints(min_length=1, max_length=253)] = "127.0.0.1"
    port: StrictInt = Field(default=18765, ge=1, le=65535)
    authentication: Literal["required", "disabled"] = "required"

    @field_validator("listen_host")
    @classmethod
    def listen_host_must_be_ip_or_hostname(cls, value: str) -> str:
        """Reject malformed listener identities before any socket is opened."""
        try:
            ipaddress.ip_address(value)
            return value
        except ValueError:
            hostname = value[:-1] if value.endswith(".") else value
            labels = hostname.split(".")
            if not hostname or any(
                len(label) > 63 or re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?", label) is None
                for label in labels
            ):
                raise ValueError("listenHost must be a valid IP address or hostname") from None
        return value

    @model_validator(mode="after")
    def require_authentication_outside_loopback(self) -> "NodeClientApiSpec":
        """Disallow unauthenticated listeners outside the local loopback boundary."""
        host = self.listen_host.lower().removesuffix(".")
        is_loopback = host == "localhost"
        if not is_loopback:
            try:
                is_loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                is_loopback = False
        if not is_loopback and self.authentication != "required":
            raise ValueError("authentication is required for non-loopback listeners")
        return self


class NodeHeartbeatActiveHours(StrictConfigModel):
    """Optional local-time window in which Node heartbeat turns may run."""

    start: Annotated[str, StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")] | None = None
    end: Annotated[str, StringConstraints(pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")] | None = None
    timezone: Annotated[str, StringConstraints(min_length=1, max_length=128)] = "user"

    @model_validator(mode="after")
    def start_and_end_must_be_declared_together(self) -> "NodeHeartbeatActiveHours":
        """Reject half-configured active-hour windows."""
        if (self.start is None) != (self.end is None):
            raise ValueError("heartbeat activeHours start and end must be configured together")
        return self


class NodeHeartbeatSpec(StrictConfigModel):
    """Typed Node-owned heartbeat settings without environment fallbacks."""

    enabled: StrictBool = False
    every_seconds: StrictInt = Field(default=1800, ge=30, le=604800)
    prompt: Annotated[str, StringConstraints(min_length=1, max_length=4000)] = (
        "Review current tasks and report only information that needs operator attention."
    )
    active_hours: NodeHeartbeatActiveHours = Field(default_factory=NodeHeartbeatActiveHours)

    @field_validator("prompt")
    @classmethod
    def prompt_must_be_visible(cls, value: str) -> str:
        """Reject blank or control-bearing automation prompts."""
        return _visible_text(value)


class NodeOperationsSpec(StrictConfigModel):
    """Lifecycle configuration for Node-owned schedulers and automation."""

    task_scheduler_enabled: StrictBool = True
    cron_enabled: StrictBool = True
    heartbeat: NodeHeartbeatSpec = Field(default_factory=NodeHeartbeatSpec)


class NodeSpec(StrictConfigModel):
    """Settings owned by a single OpenPPX Node."""

    display_name: DisplayName
    enabled_agents: list[ResourceName] = Field(default_factory=list)
    client_api: NodeClientApiSpec = Field(default_factory=NodeClientApiSpec)
    operations: NodeOperationsSpec = Field(default_factory=NodeOperationsSpec)
    permissions: NodePermissionSpec = Field(default_factory=NodePermissionSpec)

    @field_validator("enabled_agents")
    @classmethod
    def enabled_agents_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep Agent membership deterministic and unambiguous."""
        if len(value) != len(set(value)):
            raise ValueError("enabledAgents entries must be unique")
        return value


class NodeConfig(StrictConfigModel):
    """Versioned configuration resource for one OpenPPX Node."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["NodeConfig"]
    metadata: ResourceMetadata
    spec: NodeSpec


WorkspaceScope: TypeAlias = Literal["single_workspace"]
FilesystemAccess: TypeAlias = Literal["read_only", "read_write"]
ShellAccess: TypeAlias = Literal["denied", "restricted", "full"]
NetworkAccess: TypeAlias = Literal["denied", "restricted", "full"]
ToolAccess: TypeAlias = Literal["safe", "task_scoped", "broad"]
SecretAccess: TypeAlias = Literal["none", "limited"]
HighRiskActionAccess: TypeAlias = Literal["denied", "conditional"]
PrivilegeLevel: TypeAlias = Literal["low", "medium", "high", "root"]
ModelRole: TypeAlias = Literal["fast", "reasoning", "vision"]


class PermissionOverrides(StrictConfigModel):
    """Legacy coarse profile override retained until explicit permission migration."""

    workspace_scope: WorkspaceScope | None = None
    filesystem_access: FilesystemAccess | None = None
    shell_exec: ShellAccess | None = None
    network_access: NetworkAccess | None = None
    tool_access: ToolAccess | None = None
    secret_access: SecretAccess | None = None
    can_delegate: StrictBool | None = None
    can_approve_privilege_escalation: StrictBool | None = None
    high_risk_action_access: HighRiskActionAccess | None = None


class AgentModelPolicy(StrictConfigModel):
    """Persistent Model Profile assignments for one Agent.

    Per-run overrides intentionally do not belong in this persisted policy.
    """

    default_profile: ResourceName | None = None
    role_profiles: dict[ModelRole, ResourceName] = Field(default_factory=dict)


_PERMISSION_PROFILES: dict[str, dict[str, str | bool]] = {
    "low": {
        "workspace_scope": "single_workspace",
        "filesystem_access": "read_only",
        "shell_exec": "denied",
        "network_access": "denied",
        "tool_access": "safe",
        "secret_access": "none",
        "can_delegate": False,
        "can_approve_privilege_escalation": False,
        "high_risk_action_access": "denied",
    },
    "medium": {
        "workspace_scope": "single_workspace",
        "filesystem_access": "read_write",
        "shell_exec": "restricted",
        "network_access": "restricted",
        "tool_access": "task_scoped",
        "secret_access": "limited",
        "can_delegate": True,
        "can_approve_privilege_escalation": False,
        "high_risk_action_access": "denied",
    },
    "high": {
        "workspace_scope": "multi_workspace",
        "filesystem_access": "read_write",
        "shell_exec": "full",
        "network_access": "full",
        "tool_access": "broad",
        "secret_access": "limited",
        "can_delegate": True,
        "can_approve_privilege_escalation": True,
        "high_risk_action_access": "conditional",
    },
    "root": {
        "workspace_scope": "multi_workspace",
        "filesystem_access": "read_write",
        "shell_exec": "full",
        "network_access": "full",
        "tool_access": "broad",
        "secret_access": "limited",
        "can_delegate": True,
        "can_approve_privilege_escalation": True,
        "high_risk_action_access": "conditional",
    },
}

_PERMISSION_ORDER: dict[str, tuple[str, ...]] = {
    "workspace_scope": ("single_workspace", "multi_workspace"),
    "filesystem_access": ("read_only", "read_write"),
    "shell_exec": ("denied", "restricted", "full"),
    "network_access": ("denied", "restricted", "full"),
    "tool_access": ("safe", "task_scoped", "broad"),
    "secret_access": ("none", "limited"),
    "high_risk_action_access": ("denied", "conditional"),
}


class AgentSpec(StrictConfigModel):
    """Settings owned by one OpenPPX Agent."""

    display_name: DisplayName
    workspace: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    instruction: Annotated[str, StringConstraints(max_length=16_384)] = ""
    owner_principal_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    privilege_level: PrivilegeLevel = "low"
    permission_overrides: PermissionOverrides = Field(default_factory=PermissionOverrides)
    permissions: AgentPermissionSpec = Field(default_factory=AgentPermissionSpec)
    model_policy: AgentModelPolicy = Field(default_factory=AgentModelPolicy)

    @field_validator("workspace", "owner_principal_id")
    @classmethod
    def identity_text_must_be_meaningful(cls, value: str) -> str:
        """Reject blank or control-bearing identity and workspace fields."""
        return _visible_text(value)

    @field_validator("instruction")
    @classmethod
    def instruction_must_not_contain_controls(cls, value: str) -> str:
        """Allow an empty instruction while rejecting unsafe control characters."""
        if any((ord(character) < 32 and character not in {"\n", "\t"}) or ord(character) == 127 for character in value):
            raise ValueError("instruction must not contain control characters")
        return value.strip()

    @model_validator(mode="after")
    def permission_overrides_must_only_narrow(self) -> "AgentSpec":
        """Reject legacy elevation and ambiguous legacy/new permission sources."""
        base = _PERMISSION_PROFILES[self.privilege_level]
        provided = self.permission_overrides.model_dump(exclude_none=True)
        execution_fields = {
            "workspace_scope",
            "filesystem_access",
            "shell_exec",
            "network_access",
            "tool_access",
        }
        if execution_fields.intersection(provided) and not self.permissions.is_empty():
            raise ValueError("permissionOverrides and permissions cannot both contain values")
        for field_name, requested in provided.items():
            allowed = base[field_name]
            if isinstance(requested, bool):
                if requested and not allowed:
                    raise ValueError("permissionOverrides may only narrow the selected privilege profile")
                continue
            order = _PERMISSION_ORDER[field_name]
            if order.index(requested) > order.index(str(allowed)):
                raise ValueError("permissionOverrides may only narrow the selected privilege profile")
        return self


class AgentConfig(StrictConfigModel):
    """Versioned configuration resource for one OpenPPX Agent."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["AgentConfig"]
    metadata: ResourceMetadata
    spec: AgentSpec
