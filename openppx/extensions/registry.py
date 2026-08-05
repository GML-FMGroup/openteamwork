"""Read-only unified inventory across the four Product Extension domains."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal

from .app_models import AppDefinition
from .apps import AppManager, VersionedAppConnection
from .errors import ExtensionError
from .mcp import McpManager
from .mcp_models import McpStdioTransport
from .plugins import PluginManager
from .skills import SkillManager


ExtensionKind = Literal["plugin", "app", "mcp", "skill"]
_KINDS: tuple[ExtensionKind, ...] = ("plugin", "app", "mcp", "skill")


@dataclass(frozen=True, slots=True)
class ExtensionSummary:
    """Stable, client-safe common inventory row for one Extension resource."""

    kind: ExtensionKind
    extension_id: str
    display_name: str
    description: str
    version: str
    status: str
    revision: str
    source_type: str
    trust: str
    risk: str
    enabled_agent_ids: tuple[str, ...]
    ready: bool
    issues: tuple[str, ...]
    managed_by: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Project the common row without source locators or protected references."""
        return {
            "kind": self.kind,
            "id": self.extension_id,
            "displayName": self.display_name,
            "description": self.description,
            "version": self.version,
            "status": self.status,
            "revision": self.revision,
            "source": {"type": self.source_type, "trust": self.trust},
            "risk": self.risk,
            "enabledAgentIds": list(self.enabled_agent_ids),
            "readiness": {"ready": self.ready, "issues": list(self.issues)},
            "managedBy": self.managed_by,
        }


@dataclass(frozen=True, slots=True)
class ExtensionDetail:
    """One common row plus bounded domain-specific, non-sensitive details."""

    summary: ExtensionSummary
    details: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """Return one contract-ready detail payload."""
        return {**self.summary.to_payload(), "details": self.details}


class ExtensionRegistry:
    """Aggregate Extension queries while leaving lifecycle ownership in managers."""

    def __init__(
        self,
        *,
        skills: SkillManager,
        mcp: McpManager,
        apps: AppManager,
        plugins: PluginManager,
    ) -> None:
        self.skills = skills
        self.mcp = mcp
        self.apps = apps
        self.plugins = plugins

    def list(
        self,
        *,
        kind: ExtensionKind | None = None,
        agent_id: str | None = None,
    ) -> tuple[ExtensionSummary, ...]:
        """Return deterministic Extension rows, optionally filtered by kind/Agent."""
        kinds = (self._kind(kind),) if kind is not None else _KINDS
        values: list[ExtensionSummary] = []
        for selected in kinds:
            values.extend(self._list_kind(selected))
        if agent_id is not None:
            values = [item for item in values if agent_id in item.enabled_agent_ids]
        return tuple(sorted(values, key=lambda item: (_KINDS.index(item.kind), item.extension_id)))

    def get(self, kind: ExtensionKind, extension_id: str) -> ExtensionDetail:
        """Return one Extension with bounded kind-specific information."""
        selected = self._kind(kind)
        if selected == "skill":
            item = self.skills.get(extension_id)
            readiness = self.skills.readiness(extension_id)
            summary = self._skill_summary(item)
            return ExtensionDetail(
                summary,
                {
                    "builtin": item.builtin,
                    "capabilities": list(item.record.spec.capabilities),
                    "dependencies": {
                        "executables": list(item.record.spec.dependencies.executables),
                        "environment": list(item.record.spec.dependencies.environment),
                    },
                    "missingExecutables": list(readiness.missing_executables),
                    "missingEnvironment": list(readiness.missing_environment),
                },
            )
        if selected == "mcp":
            item = self.mcp.get(extension_id)
            readiness = self.mcp.readiness(extension_id)
            transport = item.record.spec.transport
            summary = self._mcp_summary(item)
            return ExtensionDetail(
                summary,
                {
                    "transport": transport.type,
                    "toolNamePrefix": item.record.spec.policy.resolved_prefix(extension_id),
                    "toolFilter": list(item.record.spec.policy.tool_filter),
                    "requiresConfirmation": item.record.spec.policy.require_confirmation,
                    "authState": readiness.auth_state,
                    "executableState": readiness.executable_state,
                    "endpointKind": "local" if isinstance(transport, McpStdioTransport) else "remote",
                    "resource": item.record.model_dump(mode="json", by_alias=True),
                },
            )
        if selected == "plugin":
            item = self.plugins.get(extension_id)
            readiness = self.plugins.readiness(extension_id)
            hooks = self.plugins.hook_status(extension_id)
            resources = item.record.spec.resources
            return ExtensionDetail(
                self._plugin_summary(item),
                {
                    "developer": item.record.spec.developer,
                    "digest": item.record.spec.digest,
                    "resourceCounts": {
                        "skills": len(resources.skills),
                        "apps": len(resources.apps),
                        "mcpServers": len(resources.mcp_servers),
                        "hooks": len(resources.hook_paths) + resources.inline_hook_count,
                    },
                    "readinessIssues": list(readiness.issues),
                    "hooks": hooks.to_payload(),
                },
            )
        definition = self.apps.get_definition(extension_id)
        connections = self.apps.list_connections(app_id=extension_id)
        return ExtensionDetail(
            self._app_summary(definition.record, definition.revision, connections),
            {
                "definitionRevision": definition.revision,
                "category": definition.record.spec.category,
                "developer": definition.record.spec.developer,
                "authType": definition.record.spec.auth.type,
                "toolCount": len(definition.record.spec.tools),
                "credentials": [
                    credential.model_dump(mode="json", by_alias=True)
                    for credential in definition.record.spec.auth.credentials
                ],
                "tools": [
                    tool.model_dump(mode="json", by_alias=True)
                    for tool in definition.record.spec.tools
                ],
                "connections": [self._connection_payload(item) for item in connections],
            },
        )

    def readiness(self, kind: ExtensionKind, extension_id: str) -> dict[str, Any]:
        """Return a common readiness payload without transport-specific detail leaks."""
        detail = self.get(kind, extension_id)
        return {
            "kind": detail.summary.kind,
            "id": detail.summary.extension_id,
            "ready": detail.summary.ready,
            "issues": list(detail.summary.issues),
            "status": detail.summary.status,
            "revision": detail.summary.revision,
        }

    def _list_kind(self, kind: ExtensionKind) -> list[ExtensionSummary]:
        if kind == "skill":
            return [self._skill_summary(item) for item in self.skills.list()]
        if kind == "mcp":
            return [self._mcp_summary(item) for item in self.mcp.list()]
        if kind == "plugin":
            return [self._plugin_summary(item) for item in self.plugins.list()]
        return [
            self._app_summary(
                item.record,
                item.revision,
                self.apps.list_connections(app_id=item.record.metadata.name),
            )
            for item in self.apps.list_definitions()
        ]

    def _skill_summary(self, item) -> ExtensionSummary:
        readiness = self.skills.readiness(item.record.metadata.name)
        return ExtensionSummary(
            kind="skill",
            extension_id=item.record.metadata.name,
            display_name=item.record.metadata.name,
            description=item.record.spec.description,
            version=item.record.spec.version,
            status=item.status,
            revision=item.revision,
            source_type=item.record.spec.source.type,
            trust=_trust(item.record.spec.source.type),
            risk=item.record.spec.risk,
            enabled_agent_ids=tuple(item.record.spec.enabled_agent_ids),
            ready=readiness.ready,
            issues=readiness.issues,
        )

    def _mcp_summary(self, item) -> ExtensionSummary:
        readiness = self.mcp.readiness(item.record.metadata.name)
        managed = item.record.spec.managed_by
        return ExtensionSummary(
            kind="mcp",
            extension_id=item.record.metadata.name,
            display_name=item.record.spec.display_name,
            description=item.record.spec.description,
            version="unversioned",
            status=item.status,
            revision=item.revision,
            source_type="direct" if managed is None else managed.kind,
            trust="local",
            risk=item.record.spec.risk,
            enabled_agent_ids=tuple(item.record.spec.enabled_agent_ids),
            ready=readiness.ready,
            issues=readiness.issues,
            managed_by=None if managed is None else f"{managed.kind}:{managed.name}",
        )

    def _plugin_summary(self, item) -> ExtensionSummary:
        readiness = self.plugins.readiness(item.record.metadata.name)
        return ExtensionSummary(
            kind="plugin",
            extension_id=item.record.metadata.name,
            display_name=item.record.spec.display_name,
            description=item.record.spec.description,
            version=item.record.spec.version,
            status=item.status,
            revision=item.revision,
            source_type=item.record.spec.source.type,
            trust=item.record.spec.trust,
            risk=item.record.spec.risk,
            enabled_agent_ids=tuple(item.record.spec.enabled_agent_ids),
            ready=readiness.ready,
            issues=readiness.issues,
        )

    def _app_summary(
        self,
        definition: AppDefinition,
        definition_revision: str,
        connections: tuple[VersionedAppConnection, ...],
    ) -> ExtensionSummary:
        enabled = tuple(
            sorted(
                {
                    agent_id
                    for connection in connections
                    for agent_id in connection.record.spec.enabled_agent_ids
                }
            )
        )
        readiness = [self.apps.readiness(item.record.metadata.name) for item in connections]
        ready = any(item.ready for item in readiness) if readiness else definition.spec.auth.type == "none"
        issues = tuple(sorted({issue for item in readiness for issue in item.issues}))
        if not connections and definition.spec.auth.type != "none":
            issues = ("connection_required",)
        status = "enabled" if enabled else "connected" if connections else "installed"
        revision = _combined_revision(
            definition_revision,
            *(item.revision for item in connections),
        )
        return ExtensionSummary(
            kind="app",
            extension_id=definition.metadata.name,
            display_name=definition.spec.display_name,
            description=definition.spec.description,
            version=definition.spec.version,
            status=status,
            revision=revision,
            source_type=definition.spec.source.type,
            trust=_trust(definition.spec.source.type),
            risk=_max_risk(tool.risk for tool in definition.spec.tools),
            enabled_agent_ids=enabled,
            ready=ready,
            issues=issues,
            managed_by=None,
        )

    def _connection_payload(self, connection: VersionedAppConnection) -> dict[str, Any]:
        readiness = self.apps.readiness(connection.record.metadata.name)
        return {
            "id": connection.record.metadata.name,
            "appId": connection.record.spec.app_id,
            "displayName": connection.record.spec.display_name,
            "status": connection.status,
            "revision": connection.revision,
            "authState": readiness.auth_state,
            "ready": readiness.ready,
            "issues": list(readiness.issues),
            "credentialRefs": {
                name: reference.model_dump(mode="json", by_alias=True)
                for name, reference in connection.record.spec.credential_refs.items()
            },
            "enabledAgentIds": list(connection.record.spec.enabled_agent_ids),
            "enabledTools": connection.record.spec.enabled_tools,
            "requiresConfirmation": connection.record.spec.require_confirmation,
        }

    @staticmethod
    def _kind(value: str) -> ExtensionKind:
        if value not in _KINDS:
            raise ExtensionError("invalid_extension_kind", "Extension kind is not supported.")
        return value  # type: ignore[return-value]


def _trust(source_type: str) -> str:
    if source_type == "builtin":
        return "builtin"
    if source_type in {"local_directory", "local_archive", "direct"}:
        return "local"
    return "third_party"


def _max_risk(values) -> str:
    order = {"low": 0, "medium": 1, "high": 2}
    return max(values, key=lambda item: order[item], default="low")


def _combined_revision(*revisions: str) -> str:
    canonical = json.dumps(revisions, separators=(",", ":")).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


__all__ = ["ExtensionDetail", "ExtensionKind", "ExtensionRegistry", "ExtensionSummary"]
