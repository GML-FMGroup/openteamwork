"""Typed, runtime-neutral models for OpenPPX static execution permissions."""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Annotated, Literal, TypeAlias, TypeVar
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StringConstraints,
    field_validator,
    model_validator,
)
from pydantic.alias_generators import to_camel


PermissionEffect: TypeAlias = Literal["allow", "deny"]
PermissionObject: TypeAlias = Literal[
    "workspace",
    "external_path",
    "command",
    "process",
    "network",
    "tool",
]
PermissionAction: TypeAlias = Literal[
    "list",
    "read",
    "search",
    "create",
    "write",
    "edit",
    "rename",
    "delete",
    "execute",
    "spawn",
    "view",
    "wait",
    "input",
    "stop",
    "cleanup",
    "resolve",
    "connect",
    "upload",
    "private_access",
    "listen",
    "invoke",
]
PermissionPreset: TypeAlias = Literal["low", "medium", "high", "root"]
PermissionRolloutMode: TypeAlias = Literal["observe", "enforce"]


_ALLOWED_ACTIONS: dict[str, tuple[str, ...]] = {
    "workspace": ("list", "read", "search", "create", "write", "edit", "rename", "delete", "execute"),
    "external_path": ("list", "read", "search", "create", "write", "edit", "rename", "delete", "execute"),
    "command": ("execute",),
    "process": ("spawn", "view", "wait", "input", "stop", "cleanup"),
    "network": ("resolve", "connect", "read", "write", "upload", "private_access", "listen"),
    "tool": ("invoke",),
}

_SELECTOR_KINDS: dict[str, frozenset[str]] = {
    "workspace": frozenset({"all", "workspace_path"}),
    "external_path": frozenset({"all", "external_path", "agent_workspace"}),
    "command": frozenset({"all", "command"}),
    "process": frozenset({"all", "process"}),
    "network": frozenset({"all", "network"}),
    "tool": frozenset({"all", "tool"}),
}

_CONSTRAINT_KINDS: dict[str, frozenset[str]] = {
    "workspace": frozenset({"none", "path"}),
    "external_path": frozenset({"none", "path"}),
    "command": frozenset({"none", "command"}),
    "process": frozenset({"none", "process"}),
    "network": frozenset({"none", "network"}),
    "tool": frozenset({"none"}),
}

_RULE_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9._:-]{0,126}[a-z0-9])?$")
_LIST_TO_TUPLE = BeforeValidator(lambda value: tuple(value) if isinstance(value, list) else value)
CollectionItemT = TypeVar("CollectionItemT")


def allowed_actions_for(object_kind: str) -> tuple[str, ...]:
    """Return the complete action vocabulary for one permission object."""

    try:
        return _ALLOWED_ACTIONS[object_kind]
    except KeyError as exc:
        raise ValueError(f"unsupported permission object: {object_kind}") from exc


def _visible(value: str, *, field_name: str) -> str:
    """Reject blank or control-bearing permission text."""

    if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError(f"{field_name} must contain visible characters")
    return value.strip()


def _unique(values: Sequence[CollectionItemT], *, field_name: str) -> tuple[CollectionItemT, ...]:
    """Reject duplicate selector values before compilation."""

    if len(values) != len(set(values)):
        raise ValueError(f"{field_name} entries must be unique")
    return tuple(values)


def _absolute_paths(values: Sequence[str], *, field_name: str) -> tuple[str, ...]:
    """Require canonical-looking absolute roots before runtime path resolution."""

    normalized: list[str] = []
    for value in values:
        item = _visible(value, field_name=field_name)
        path = Path(item)
        if not path.is_absolute() or ".." in path.parts:
            raise ValueError(f"{field_name} entries must be absolute and cannot contain '..'")
        normalized.append(item)
    return _unique(normalized, field_name=field_name)


class PermissionConfigModel(BaseModel):
    """Strict camel-cased model shared by permission config contracts."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        loc_by_alias=True,
        populate_by_name=True,
        strict=True,
        frozen=True,
    )


class FrozenPermissionModel(PermissionConfigModel):
    """Immutable top-level value used after permission compilation."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        extra="forbid",
        loc_by_alias=True,
        populate_by_name=True,
        strict=True,
        frozen=True,
    )


class MatchAllSelector(PermissionConfigModel):
    """Match every resource already contained by the selected object boundary."""

    kind: Literal["all"] = "all"


class WorkspacePathSelector(PermissionConfigModel):
    """Match paths relative to the authoritative Agent Workspace."""

    kind: Literal["workspace_path"] = "workspace_path"
    patterns: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=1024)], ...],
        _LIST_TO_TUPLE,
    ] = Field(min_length=1)

    @field_validator("patterns")
    @classmethod
    def patterns_must_be_safe_relative_values(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Keep Workspace selectors relative and free of traversal components."""

        normalized: list[str] = []
        for pattern in value:
            item = _visible(pattern, field_name="workspace pattern").replace("\\", "/")
            if item.startswith("/") or any(part == ".." for part in item.split("/")):
                raise ValueError("workspace patterns must be relative and cannot contain '..'")
            normalized.append(item)
        return _unique(normalized, field_name="workspace patterns")


class ExternalPathSelector(PermissionConfigModel):
    """Match explicit host paths resolved later by the Path enforcement adapter."""

    kind: Literal["external_path"] = "external_path"
    paths: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=4096)], ...],
        _LIST_TO_TUPLE,
    ] = Field(min_length=1)

    @field_validator("paths")
    @classmethod
    def paths_must_be_visible_and_unique(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject ambiguous empty/control-bearing roots without host-specific resolution."""

        return _absolute_paths(value, field_name="external paths")


class AgentWorkspaceSelector(PermissionConfigModel):
    """Match Workspaces owned by other Agents using trusted Config facts."""

    kind: Literal["agent_workspace"] = "agent_workspace"
    privilege_levels: Annotated[tuple[PermissionPreset, ...], _LIST_TO_TUPLE] = ()
    agent_ids: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=128)], ...],
        _LIST_TO_TUPLE,
    ] = ()

    @model_validator(mode="after")
    def selector_must_have_one_dimension(self) -> "AgentWorkspaceSelector":
        """Require an Agent ID or permission preset selector."""

        if not self.privilege_levels and not self.agent_ids:
            raise ValueError("agentWorkspace selector requires privilegeLevels or agentIds")
        _unique(self.privilege_levels, field_name="privilege levels")
        _unique(self.agent_ids, field_name="agent IDs")
        return self


class CommandSelector(PermissionConfigModel):
    """Match a structured command identity rather than a shell string."""

    kind: Literal["command"] = "command"
    executables: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=1024)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    subcommands: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=256)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    execution_profiles: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=128)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    shell: StrictBool | None = None

    @model_validator(mode="after")
    def selector_must_have_one_dimension(self) -> "CommandSelector":
        """Reject an accidental unbounded Command selector; use kind=all explicitly."""

        if not self.executables and not self.subcommands and not self.execution_profiles and self.shell is None:
            raise ValueError("command selector requires at least one structured field")
        _unique(self.executables, field_name="executables")
        _unique(self.subcommands, field_name="subcommands")
        _unique(self.execution_profiles, field_name="execution profiles")
        return self


class ProcessSelector(PermissionConfigModel):
    """Match trustworthy process provenance recorded by the Node Runner."""

    kind: Literal["process"] = "process"
    created_by: Annotated[
        tuple[Literal["allowed_command", "current_task", "current_agent"], ...],
        _LIST_TO_TUPLE,
    ] = ()
    protected: StrictBool | None = None
    system_process: StrictBool | None = None

    @model_validator(mode="after")
    def selector_must_have_one_dimension(self) -> "ProcessSelector":
        """Reject an unbounded Process selector and duplicate provenance values."""

        if not self.created_by and self.protected is None and self.system_process is None:
            raise ValueError("process selector requires provenance or protection facts")
        _unique(self.created_by, field_name="createdBy")
        return self


class NetworkSelector(PermissionConfigModel):
    """Match normalized network targets without relying on substring rules."""

    kind: Literal["network"] = "network"
    origins: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=2048)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    domains: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=253)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    cidrs: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=64)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    schemes: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=32)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    ports: Annotated[tuple[StrictInt, ...], _LIST_TO_TUPLE] = ()
    visibility: Annotated[
        tuple[Literal["public", "private", "control_plane"], ...],
        _LIST_TO_TUPLE,
    ] = ()

    @model_validator(mode="after")
    def validate_network_dimensions(self) -> "NetworkSelector":
        """Validate bounded network dimensions while deferring DNS resolution to runtime."""

        if not any((self.origins, self.domains, self.cidrs, self.schemes, self.ports, self.visibility)):
            raise ValueError("network selector requires at least one target dimension")
        for cidr in self.cidrs:
            try:
                ipaddress.ip_network(cidr, strict=False)
            except ValueError as exc:
                raise ValueError(f"invalid network CIDR: {cidr}") from exc
        if any(port < 1 or port > 65535 for port in self.ports):
            raise ValueError("network ports must be between 1 and 65535")
        _unique(self.origins, field_name="origins")
        _unique(self.domains, field_name="domains")
        _unique(self.cidrs, field_name="CIDRs")
        _unique(self.schemes, field_name="schemes")
        _unique([str(port) for port in self.ports], field_name="ports")
        _unique(list(self.visibility), field_name="visibility")
        return self


class ToolSelector(PermissionConfigModel):
    """Match stable Tool IDs and registered operations."""

    kind: Literal["tool"] = "tool"
    tool_ids: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=256)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    operations: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=256)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    sources: Annotated[
        tuple[Literal["builtin", "extension", "mcp", "native_app", "skill"], ...],
        _LIST_TO_TUPLE,
    ] = ()

    @model_validator(mode="after")
    def selector_must_have_one_dimension(self) -> "ToolSelector":
        """Reject an accidental match-all Tool rule."""

        if not self.tool_ids and not self.operations and not self.sources:
            raise ValueError("tool selector requires toolIds, operations, or sources")
        _unique(self.tool_ids, field_name="tool IDs")
        _unique(self.operations, field_name="operations")
        _unique(self.sources, field_name="tool sources")
        return self


PermissionSelector: TypeAlias = Annotated[
    MatchAllSelector
    | WorkspacePathSelector
    | ExternalPathSelector
    | AgentWorkspaceSelector
    | CommandSelector
    | ProcessSelector
    | NetworkSelector
    | ToolSelector,
    Field(discriminator="kind"),
]


class NoConstraints(PermissionConfigModel):
    """Declare that a rule has no additional runtime constraints."""

    kind: Literal["none"] = "none"


class PathConstraints(PermissionConfigModel):
    """Bound a path operation independently from selector matching."""

    kind: Literal["path"] = "path"
    max_bytes: StrictInt | None = Field(default=None, ge=1)
    max_entries: StrictInt | None = Field(default=None, ge=1)
    max_depth: StrictInt | None = Field(default=None, ge=0)


class CommandConstraints(PermissionConfigModel):
    """Require one Command execution profile and bounded process behavior."""

    kind: Literal["command"] = "command"
    execution_profile: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    allow_shell: StrictBool = False
    allow_background: StrictBool = False
    allow_pty: StrictBool = False
    timeout_seconds: StrictInt | None = Field(default=None, ge=1)
    max_output_bytes: StrictInt | None = Field(default=None, ge=1)


class ProcessConstraints(PermissionConfigModel):
    """Restrict process management to a trusted task or Agent boundary."""

    kind: Literal["process"] = "process"
    current_task_only: StrictBool = False
    current_agent_only: StrictBool = False


class NetworkConstraints(PermissionConfigModel):
    """Bound one network rule to a managed protocol and transfer shape."""

    kind: Literal["network"] = "network"
    managed_web_only: StrictBool = False
    read_only: StrictBool = False


PermissionConstraints: TypeAlias = Annotated[
    NoConstraints | PathConstraints | CommandConstraints | ProcessConstraints | NetworkConstraints,
    Field(discriminator="kind"),
]


class PermissionRule(PermissionConfigModel):
    """One configurable allow/deny rule expanded to single actions at compile time."""

    rule_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    effect: PermissionEffect
    object: PermissionObject
    actions: Annotated[tuple[PermissionAction, ...], _LIST_TO_TUPLE] = Field(min_length=1)
    selector: PermissionSelector = Field(default_factory=MatchAllSelector)
    constraints: PermissionConstraints = Field(default_factory=NoConstraints)
    description: Annotated[str, StringConstraints(max_length=512)] = ""

    @field_validator("rule_id")
    @classmethod
    def rule_id_must_be_stable(cls, value: str) -> str:
        """Require a stable machine-safe identifier for audit records."""

        if _RULE_ID_PATTERN.fullmatch(value) is None:
            raise ValueError("ruleId must use lowercase letters, digits, '.', '_', ':', or '-'")
        return value

    @field_validator("description")
    @classmethod
    def description_must_not_contain_controls(cls, value: str) -> str:
        """Keep optional operator-facing descriptions safe for logs and previews."""

        if not value:
            return value
        return _visible(value, field_name="description")

    @model_validator(mode="after")
    def object_action_selector_and_constraints_must_align(self) -> "PermissionRule":
        """Reject cross-object rules that an enforcement adapter could misinterpret."""

        allowed_actions = set(allowed_actions_for(self.object))
        invalid_actions = sorted(set(self.actions) - allowed_actions)
        if invalid_actions:
            raise ValueError(f"actions {invalid_actions} are invalid for object '{self.object}'")
        _unique(list(self.actions), field_name="actions")
        if self.selector.kind not in _SELECTOR_KINDS[self.object]:
            raise ValueError(f"selector kind '{self.selector.kind}' is invalid for object '{self.object}'")
        if self.constraints.kind not in _CONSTRAINT_KINDS[self.object]:
            raise ValueError(f"constraint kind '{self.constraints.kind}' is invalid for object '{self.object}'")
        if self.effect == "deny" and self.constraints.kind != "none":
            raise ValueError("deny rules cannot declare execution constraints")
        return self


class TemplatePermissionRule(PermissionRule):
    """Preset rule that may be locked against Agent-level widening."""

    locked: StrictBool = False


PermissionDefaults: TypeAlias = dict[PermissionObject, dict[PermissionAction, PermissionEffect]]


def _validate_defaults(value: PermissionDefaults) -> PermissionDefaults:
    """Validate that each configured default belongs to its permission object."""

    for object_kind, actions in value.items():
        allowed = set(allowed_actions_for(object_kind))
        invalid = sorted(set(actions) - allowed)
        if invalid:
            raise ValueError(f"default actions {invalid} are invalid for object '{object_kind}'")
    return value


class AgentPermissionSpec(PermissionConfigModel):
    """Fine-grained static permission overlay owned by one Agent resource."""

    object_defaults: dict[PermissionObject, PermissionEffect] = Field(default_factory=dict)
    defaults: PermissionDefaults = Field(default_factory=dict)
    rules: Annotated[tuple[PermissionRule, ...], _LIST_TO_TUPLE] = ()
    rollout_mode: PermissionRolloutMode | None = Field(
        default=None,
        description="Optional Agent-wide override for every permission-object rollout mode.",
    )
    rollout_modes: dict[PermissionObject, PermissionRolloutMode] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_overlay(self) -> "AgentPermissionSpec":
        """Reject invalid defaults and duplicate local rule IDs."""

        _validate_defaults(self.defaults)
        rule_ids = [rule.rule_id for rule in self.rules]
        _unique(rule_ids, field_name="permission rule IDs")
        return self

    def is_empty(self) -> bool:
        """Return whether the overlay leaves the selected preset unchanged."""

        return (
            not self.object_defaults
            and not self.defaults
            and not self.rules
            and self.rollout_mode is None
            and not self.rollout_modes
        )


class CodeEgressProxySpec(PermissionConfigModel):
    """Node-owned proxy-only Docker network configuration for arbitrary code."""

    url: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    docker_network: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")]
    policy_directory: Annotated[str, StringConstraints(min_length=1, max_length=4096)]

    @field_validator("url")
    @classmethod
    def proxy_url_must_be_plain_http_origin(cls, value: str) -> str:
        """Require an in-network HTTP proxy endpoint without embedded credentials."""

        parsed = urlsplit(value.strip())
        if parsed.scheme != "http" or not parsed.hostname:
            raise ValueError("code egress proxy url must be an http URL with a hostname")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("code egress proxy credentials cannot be stored in NodeConfig")
        return value.strip().rstrip("/")

    @field_validator("policy_directory")
    @classmethod
    def policy_directory_must_be_absolute(cls, value: str) -> str:
        """Keep the Node-owned proxy policy exchange outside Agent path control."""

        return _absolute_paths([value], field_name="code egress proxy policy directory")[0]


class NodePermissionSpec(PermissionConfigModel):
    """Node-owned hard ceilings and preset inputs for static execution permissions."""

    safe_external_read_roots: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=4096)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    high_protected_write_roots: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=4096)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    hard_rules: Annotated[tuple[PermissionRule, ...], _LIST_TO_TUPLE] = ()
    rollout_modes: dict[PermissionObject, PermissionRolloutMode] = Field(default_factory=dict)
    code_egress_proxy: CodeEgressProxySpec | None = None

    @field_validator("safe_external_read_roots", "high_protected_write_roots")
    @classmethod
    def roots_must_be_absolute(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject relative Node roots before they can influence a Sandbox plan."""

        return _absolute_paths(value, field_name="Node permission roots")

    @model_validator(mode="after")
    def validate_node_owned_rules(self) -> "NodePermissionSpec":
        """Require hard rules to deny and keep configured roots deterministic."""

        if any(rule.effect != "deny" for rule in self.hard_rules):
            raise ValueError("Node permission hardRules must all use effect=deny")
        _unique([rule.rule_id for rule in self.hard_rules], field_name="Node hard rule IDs")
        _unique(self.safe_external_read_roots, field_name="safe external read roots")
        _unique(self.high_protected_write_roots, field_name="high protected write roots")
        return self


class PermissionTemplate(PermissionConfigModel):
    """One versioned declarative low/medium/high/root permission preset."""

    schema_version: Literal["openppx.permissions/v1alpha1"]
    template_id: PermissionPreset
    object_defaults: dict[PermissionObject, PermissionEffect]
    defaults: PermissionDefaults = Field(default_factory=dict)
    rules: Annotated[tuple[TemplatePermissionRule, ...], _LIST_TO_TUPLE] = ()
    pending_gates: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=128)], ...],
        _LIST_TO_TUPLE,
    ] = ()

    @model_validator(mode="after")
    def validate_template(self) -> "PermissionTemplate":
        """Require every permission object to have a base default and stable rules."""

        missing = sorted(set(_ALLOWED_ACTIONS) - set(self.object_defaults))
        if missing:
            raise ValueError(f"permission template is missing object defaults: {missing}")
        _validate_defaults(self.defaults)
        _unique([rule.rule_id for rule in self.rules], field_name="template rule IDs")
        _unique(self.pending_gates, field_name="pending gates")
        return self


class PermissionTemplateCatalog(PermissionConfigModel):
    """Packaged catalog of all built-in static permission presets."""

    schema_version: Literal["openppx.permissions/v1alpha1"]
    templates: Annotated[tuple[PermissionTemplate, ...], _LIST_TO_TUPLE] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def catalog_must_contain_each_preset_once(self) -> "PermissionTemplateCatalog":
        """Require an exact low/medium/high/root template set."""

        template_ids = [template.template_id for template in self.templates]
        _unique(template_ids, field_name="template IDs")
        expected = {"low", "medium", "high", "root"}
        if set(template_ids) != expected:
            raise ValueError("permission template catalog must contain low, medium, high, and root")
        return self


class PermissionSource(FrozenPermissionModel):
    """One versioned source contributing to a resolved permission snapshot."""

    source_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    source_kind: Literal["node", "preset", "agent"]
    revision: Annotated[str, StringConstraints(min_length=1, max_length=128)]


class AgentWorkspaceBoundary(FrozenPermissionModel):
    """Trusted Config fact used to classify another Agent's Workspace."""

    agent_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    privilege_level: PermissionPreset
    workspace: Annotated[str, StringConstraints(min_length=1, max_length=4096)]


class ResolvedPermissionDefault(FrozenPermissionModel):
    """One explicit per-object, per-action default after template expansion."""

    object: PermissionObject
    action: PermissionAction
    effect: PermissionEffect
    origin: PermissionSource


class ResolvedPermissionRule(FrozenPermissionModel):
    """One single-action rule with trusted origin and lock state."""

    rule_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    source_rule_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    effect: PermissionEffect
    object: PermissionObject
    action: PermissionAction
    selector: PermissionSelector
    constraints: PermissionConstraints
    description: Annotated[str, StringConstraints(max_length=512)] = ""
    origin: PermissionSource
    locked: StrictBool = False


class ResolvedPermissionRollout(FrozenPermissionModel):
    """Effective rollout mode for one permission object."""

    object: PermissionObject
    mode: PermissionRolloutMode


class ResolvedPermissionSnapshot(FrozenPermissionModel):
    """Content-addressed static permissions observed by the Runtime gate."""

    schema_version: Literal["openppx.permissions/v1alpha1"]
    agent_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    workspace: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    preset: PermissionPreset
    revision: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    sources: tuple[PermissionSource, ...]
    agent_workspaces: tuple[AgentWorkspaceBoundary, ...] = ()
    defaults: tuple[ResolvedPermissionDefault, ...]
    rules: tuple[ResolvedPermissionRule, ...]
    rollout_modes: tuple[ResolvedPermissionRollout, ...]
    code_egress_proxy: CodeEgressProxySpec | None = None
    blocking_gates: tuple[str, ...] = ()

    def default_for(self, object_kind: PermissionObject, action: PermissionAction) -> PermissionEffect:
        """Return the explicit default for one valid object/action pair."""

        for item in self.defaults:
            if item.object == object_kind and item.action == action:
                return item.effect
        raise KeyError(f"permission default not found for {object_kind}.{action}")

    def rollout_for(self, object_kind: PermissionObject) -> PermissionRolloutMode:
        """Return the effective rollout mode for one permission object."""

        for item in self.rollout_modes:
            if item.object == object_kind:
                return item.mode
        return "observe"

    def assert_enforce_ready(self, object_kind: PermissionObject) -> None:
        """Fail closed when an enforced object still depends on an unresolved Gate."""

        if self.rollout_for(object_kind) != "enforce":
            return
        relevant: set[str] = set()
        if object_kind in {"external_path", "command"}:
            relevant.add("high-protected-write-roots")
        if object_kind == "command":
            relevant.update({"medium-code-egress-proxy", "high-code-egress-proxy"})
        blocked = sorted(set(self.blocking_gates) & relevant)
        if blocked:
            raise PermissionError(
                f"Permission object '{object_kind}' cannot enforce until blocking Gates are resolved: "
                + ", ".join(blocked)
            )


class PermissionSubject(PermissionConfigModel):
    """Trusted identity and lifecycle facts attached to an authorization request."""

    agent_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    task_id: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    run_id: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    session_id: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None


class WorkspacePathResource(PermissionConfigModel):
    """Concrete Workspace path facts resolved by the trusted Path adapter."""

    kind: Literal["workspace_path"] = "workspace_path"
    path: Annotated[str, StringConstraints(min_length=1, max_length=4096)]

    @field_validator("path")
    @classmethod
    def path_must_be_workspace_relative(cls, value: str) -> str:
        """Require a normalized relative path supplied by the trusted Path adapter."""

        item = _visible(value, field_name="Workspace resource path").replace("\\", "/")
        if item.startswith("/") or any(part == ".." for part in item.split("/")):
            raise ValueError("Workspace resource path must be relative and cannot contain '..'")
        return item


class ExternalPathResource(PermissionConfigModel):
    """Concrete external path facts resolved by the trusted Path adapter."""

    kind: Literal["external_path"] = "external_path"
    path: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    owner_agent_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    owner_privilege_level: PermissionPreset | None = None

    @field_validator("path")
    @classmethod
    def path_must_be_absolute(cls, value: str) -> str:
        """Require the trusted Path adapter to supply an absolute external path."""

        return _absolute_paths([value], field_name="external resource path")[0]


class CommandResource(PermissionConfigModel):
    """Parsed command facts independent from a mutable shell string."""

    kind: Literal["command"] = "command"
    executable: Annotated[str, StringConstraints(min_length=1, max_length=1024)]
    argv: Annotated[
        tuple[Annotated[str, StringConstraints(max_length=8192)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    cwd: Annotated[str, StringConstraints(min_length=1, max_length=4096)]
    shell: StrictBool = False
    background: StrictBool = False
    pty: StrictBool = False
    execution_profile: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None

    @field_validator("cwd")
    @classmethod
    def cwd_must_be_absolute(cls, value: str) -> str:
        """Require Command cwd to cross the permission boundary as an absolute path."""

        return _absolute_paths([value], field_name="Command cwd")[0]


class ProcessResource(PermissionConfigModel):
    """Process provenance facts populated only by the Node Runner."""

    kind: Literal["process"] = "process"
    process_id: StrictInt | None = Field(default=None, ge=1)
    created_by_agent_id: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    created_by_task_id: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    created_by_allowed_command: StrictBool = False
    protected: StrictBool = False
    system_process: StrictBool = False


class NetworkResource(PermissionConfigModel):
    """Normalized target and DNS facts for one network authorization request."""

    kind: Literal["network"] = "network"
    scheme: Annotated[str, StringConstraints(min_length=1, max_length=32)]
    host: Annotated[str, StringConstraints(min_length=1, max_length=253)]
    port: StrictInt = Field(ge=1, le=65535)
    resolved_ips: Annotated[
        tuple[Annotated[str, StringConstraints(min_length=1, max_length=64)], ...],
        _LIST_TO_TUPLE,
    ] = ()
    visibility: Literal["public", "private", "control_plane"]
    method: Annotated[str, StringConstraints(min_length=1, max_length=32)] | None = None
    managed: StrictBool = False

    @field_validator("resolved_ips")
    @classmethod
    def resolved_ips_must_be_addresses(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject unparsed DNS output before authorization."""

        for item in value:
            try:
                ipaddress.ip_address(item)
            except ValueError as exc:
                raise ValueError(f"invalid resolved IP address: {item}") from exc
        return _unique(value, field_name="resolved IPs")


class ToolResource(PermissionConfigModel):
    """Stable registered Tool identity for one invocation."""

    kind: Literal["tool"] = "tool"
    tool_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    operation: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    source: Literal["builtin", "extension", "mcp", "native_app", "skill"]


PermissionResource: TypeAlias = Annotated[
    WorkspacePathResource | ExternalPathResource | CommandResource | ProcessResource | NetworkResource | ToolResource,
    Field(discriminator="kind"),
]


class PermissionRequest(PermissionConfigModel):
    """One normalized, trusted request passed to the future Authorization Engine."""

    request_id: Annotated[str, StringConstraints(min_length=1, max_length=256)]
    permission_revision: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    subject: PermissionSubject
    object: PermissionObject
    action: PermissionAction
    resource: PermissionResource

    @model_validator(mode="after")
    def request_object_action_and_resource_must_align(self) -> "PermissionRequest":
        """Prevent adapters from authorizing one object with facts from another."""

        if self.action not in allowed_actions_for(self.object):
            raise ValueError(f"action '{self.action}' is invalid for object '{self.object}'")
        expected_resource_kind = {
            "workspace": "workspace_path",
            "external_path": "external_path",
            "command": "command",
            "process": "process",
            "network": "network",
            "tool": "tool",
        }[self.object]
        if self.resource.kind != expected_resource_kind:
            raise ValueError(f"resource kind '{self.resource.kind}' is invalid for object '{self.object}'")
        return self


class PermissionDecision(FrozenPermissionModel):
    """Explainable static authorization result; approval is reserved for a later phase."""

    outcome: Literal["allow", "deny", "requires_approval"]
    reason_code: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    permission_revision: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    matched_rule_ids: tuple[str, ...] = ()
    obligations: tuple[str, ...] = ()


class PermissionChange(FrozenPermissionModel):
    """One redacted semantic difference between two resolved permission snapshots."""

    change_kind: Literal[
        "default_changed",
        "rule_added",
        "rule_removed",
        "rule_changed",
        "gate_added",
        "gate_removed",
    ]
    object: PermissionObject | None = None
    action: PermissionAction | None = None
    rule_id: str | None = None
    gate: str | None = None
