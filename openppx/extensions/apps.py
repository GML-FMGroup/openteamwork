"""Product App definition, connection, authorization, and Runtime projection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from filelock import FileLock, Timeout
from pydantic import ValidationError

from openppx.config import (
    ConfigRevisionConflict,
    ConfigWriteError,
    SecretRef,
    SecretStore,
    config_revision,
    read_json_object,
)
from openppx.config.atomic import atomic_write_resource

from .app_models import (
    AppConnection,
    AppCredentialValue,
    AppDefinition,
    AppLiteralValue,
    AppMcpImplementation,
    AppNativeImplementation,
    AppRemoteMcpTemplate,
    AppStdioMcpTemplate,
    AppToolSpec,
)
from .app_adapters import NativeAppAdapterRegistry, NativeAppContext
from .errors import ExtensionError
from .indexes import (
    ResourceIdentityIndex,
    ResourceIdentityReservation,
)
from .mcp import McpSnapshot, McpSnapshotEntry, merge_mcp_snapshots
from .mcp_models import (
    McpLiteralValue,
    McpOwnerRef,
    McpRemoteTransport,
    McpSecretValue,
    McpServer,
    McpServerSpec,
    McpStdioTransport,
    McpToolPolicy,
)
from .prefixes import ToolPrefixIndex, ToolPrefixReservation


_RESOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True, slots=True)
class VersionedAppDefinition:
    """Validated App definition plus its optimistic revision."""

    record: AppDefinition
    revision: str

    @property
    def status(self) -> str:
        """Return the stable definition lifecycle status."""
        return "installed"


@dataclass(frozen=True, slots=True)
class VersionedAppConnection:
    """Validated user connection plus its optimistic revision."""

    record: AppConnection
    revision: str

    @property
    def status(self) -> str:
        """Return whether this connection is active for any Agent."""
        return "connected" if self.record.spec.enabled_agent_ids else "disconnected"


@dataclass(frozen=True, slots=True)
class AppReadiness:
    """Non-sensitive readiness for one App connection."""

    ready: bool
    auth_state: Literal["not_required", "ready", "missing", "backend_unavailable"]
    executable_state: Literal["ready", "missing", "not_required"]
    adapter_state: Literal["ready", "missing", "not_required"]
    tool_state: Literal["ready", "empty"]
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class AppSnapshotEntry:
    """One App definition and connection pair pinned to a Runtime."""

    definition: AppDefinition
    definition_revision: str
    connection: AppConnection
    connection_revision: str


@dataclass(frozen=True, slots=True)
class AppSnapshot:
    """Agent-specific immutable App projection and shared MCP execution input."""

    revision: str
    entries: tuple[AppSnapshotEntry, ...]
    mcp: McpSnapshot

    @property
    def connection_ids(self) -> tuple[str, ...]:
        """Return deterministic connection identities."""
        return tuple(entry.connection.metadata.name for entry in self.entries)

    @classmethod
    def empty(cls) -> "AppSnapshot":
        """Return a stable empty App snapshot."""
        canonical = b"[]"
        return cls(
            revision=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
            entries=(),
            mcp=McpSnapshot.empty(),
        )


class AppManager:
    """Own App definitions, user connections, auth references, and projections."""

    def __init__(
        self,
        node_root: Path,
        secret_store: SecretStore,
        *,
        executable_resolver: Callable[[str], str | None] = shutil.which,
        prefix_index: ToolPrefixIndex | None = None,
        identity_index: ResourceIdentityIndex | None = None,
        adapter_registry: NativeAppAdapterRegistry | None = None,
        lock_timeout: float = 5.0,
    ) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.root = self.node_root / "extensions" / "apps"
        self.definitions_dir = self.root / "definitions"
        self.connections_dir = self.root / "connections"
        self.secret_store = secret_store
        self.executable_resolver = executable_resolver
        self.prefix_index = prefix_index
        self.identity_index = identity_index
        self.adapter_registry = adapter_registry or NativeAppAdapterRegistry()
        self.lock_timeout = lock_timeout
        if prefix_index is not None:
            prefix_index.register("apps", self._prefix_reservations)
        if identity_index is not None:
            identity_index.register("direct-app-definitions", self._identity_reservations)

    def install_definition(
        self,
        record: AppDefinition,
        *,
        expected_revision: str | None,
    ) -> VersionedAppDefinition:
        """Install one definition under a create-only revision precondition."""
        if expected_revision is not None:
            raise ExtensionError("revision_conflict", "New App definitions require an empty revision precondition.")
        self._require_identity(record.metadata.name)
        if self.identity_index is not None:
            self.identity_index.require_available(
                "app",
                record.metadata.name,
                owner_key=f"app-definition:{record.metadata.name}",
            )
        with self._mutation_lock():
            self._write_definition(record, expected_revision=None)
        return self.get_definition(record.metadata.name)

    def update_definition(
        self,
        candidate: AppDefinition,
        *,
        expected_revision: str,
    ) -> VersionedAppDefinition:
        """Update product metadata only when every existing connection remains valid."""
        self._require_identity(candidate.metadata.name)
        with self._mutation_lock():
            current = self.get_definition(candidate.metadata.name)
            self._require_revision(current.revision, expected_revision, resource="App definition")
            for connection in self.list_connections(app_id=candidate.metadata.name):
                try:
                    self._validate_connection(connection.record, candidate)
                except ExtensionError as exc:
                    raise ExtensionError(
                        "extension_in_use",
                        "Updated App definition is incompatible with an existing connection.",
                        details={"connectionId": connection.record.metadata.name},
                    ) from exc
                if connection.record.spec.enabled_agent_ids:
                    readiness = self._readiness_for(connection.record, candidate)
                    if not readiness.ready:
                        raise ExtensionError(
                            "dependency_missing",
                            "Updated App definition is not ready for active connections.",
                            details={"connectionId": connection.record.metadata.name},
                        )
            self._write_definition(candidate, expected_revision=expected_revision)
        return self.get_definition(candidate.metadata.name)

    def get_definition(self, app_id: str) -> VersionedAppDefinition:
        """Read one App definition by stable product identity."""
        self._require_identity(app_id)
        local = self._read_local_definition(app_id)
        if local is None:
            raise ExtensionError("extension_not_found", f"App definition '{app_id}' was not found.")
        return local

    def _read_local_definition(self, app_id: str) -> VersionedAppDefinition | None:
        path = self._definition_path(app_id)
        if not path.exists():
            return None
        try:
            raw = read_json_object(path, source=f"app-definition:{app_id}")
            record = AppDefinition.model_validate(raw)
        except (ValidationError, ValueError) as exc:
            raise ExtensionError("invalid_registry", "Persisted App definition is invalid.") from exc
        self._require_identity(record.metadata.name)
        return VersionedAppDefinition(record=record, revision=config_revision(record))

    def list_definitions(self) -> tuple[VersionedAppDefinition, ...]:
        """Return every App definition in deterministic order."""
        values: list[VersionedAppDefinition] = []
        if self.definitions_dir.exists():
            values.extend(
                item
                for path in sorted(self.definitions_dir.glob("*.json"), key=lambda item: item.name)
                if (item := self._read_local_definition(path.stem)) is not None
            )
        return tuple(sorted(values, key=lambda item: item.record.metadata.name))

    def remove_definition(self, app_id: str, *, expected_revision: str) -> None:
        """Remove one unreferenced, directly managed App definition."""
        with self._mutation_lock():
            current = self.get_definition(app_id)
            self._require_revision(current.revision, expected_revision, resource="App definition")
            references = self.list_connections(app_id=app_id)
            if references:
                raise ExtensionError(
                    "extension_in_use",
                    "App definition must have no connections before removal.",
                    details={"connectionIds": [item.record.metadata.name for item in references]},
                )
            self._remove_path(
                self._definition_path(app_id),
                expected_revision=expected_revision,
                current_revision=lambda: self._current_definition_revision(app_id),
                resource="App definition",
            )

    def create_connection(
        self,
        record: AppConnection,
        *,
        expected_revision: str | None,
    ) -> VersionedAppConnection:
        """Create one disconnected user connection for an installed App."""
        if expected_revision is not None:
            raise ExtensionError("revision_conflict", "New App connections require an empty revision precondition.")
        self._require_identity(record.metadata.name)
        if record.spec.enabled_agent_ids:
            raise ExtensionError("invalid_operation", "New App connections must be enabled through the lifecycle API.")
        with self._mutation_lock():
            definition = self.get_definition(record.spec.app_id)
            self._validate_connection(record, definition.record)
            self._write_connection(record, expected_revision=None)
        return self.get_connection(record.metadata.name)

    def update_connection(
        self,
        candidate: AppConnection,
        *,
        expected_revision: str,
    ) -> VersionedAppConnection:
        """Update user-visible connection policy while preserving Agent enablement."""
        self._require_identity(candidate.metadata.name)
        with self._mutation_lock():
            current = self.get_connection(candidate.metadata.name)
            self._require_revision(current.revision, expected_revision, resource="App connection")
            if candidate.spec.app_id != current.record.spec.app_id:
                raise ExtensionError("invalid_operation", "An App connection cannot be rebound to another App.")
            definition = self.get_definition(candidate.spec.app_id).record
            record = candidate.model_copy(
                update={
                    "spec": candidate.spec.model_copy(
                        update={
                            "credential_refs": dict(current.record.spec.credential_refs),
                            "enabled_agent_ids": list(current.record.spec.enabled_agent_ids),
                        }
                    )
                }
            )
            self._validate_connection(record, definition)
            if record.spec.enabled_agent_ids:
                readiness = self._readiness_for(record, definition)
                if not readiness.ready:
                    raise ExtensionError("dependency_missing", "Updated App connection is not ready.")
                for agent_id in record.spec.enabled_agent_ids:
                    self._require_prefix_available(record, agent_id)
            self._write_connection(record, expected_revision=expected_revision)
        return self.get_connection(candidate.metadata.name)

    def reauthorize(
        self,
        connection_id: str,
        credential_refs: dict[str, SecretRef],
        *,
        expected_revision: str,
    ) -> VersionedAppConnection:
        """Replace protected credential references without ever reading Secret values."""
        with self._mutation_lock():
            current = self.get_connection(connection_id)
            self._require_revision(current.revision, expected_revision, resource="App connection")
            definition = self.get_definition(current.record.spec.app_id).record
            updated = current.record.model_copy(
                update={
                    "spec": current.record.spec.model_copy(
                        update={"credential_refs": dict(credential_refs)}
                    )
                }
            )
            self._validate_connection(updated, definition)
            if updated.spec.enabled_agent_ids and not self._readiness_for(updated, definition).ready:
                raise ExtensionError("dependency_missing", "Updated App authorization is not ready.")
            self._write_connection(updated, expected_revision=expected_revision)
        return self.get_connection(connection_id)

    def get_connection(self, connection_id: str) -> VersionedAppConnection:
        """Read one user App connection by stable identity."""
        path = self._connection_path(connection_id)
        if not path.exists():
            raise ExtensionError("extension_not_found", f"App connection '{connection_id}' was not found.")
        try:
            raw = read_json_object(path, source=f"app-connection:{connection_id}")
            record = AppConnection.model_validate(raw)
        except (ValidationError, ValueError) as exc:
            raise ExtensionError("invalid_registry", "Persisted App connection is invalid.") from exc
        self._require_identity(record.metadata.name)
        return VersionedAppConnection(record=record, revision=config_revision(record))

    def list_connections(self, *, app_id: str | None = None) -> tuple[VersionedAppConnection, ...]:
        """Return App connections, optionally narrowed to one definition."""
        if not self.connections_dir.exists():
            return ()
        values = tuple(
            self.get_connection(path.stem)
            for path in sorted(self.connections_dir.glob("*.json"), key=lambda item: item.name)
        )
        if app_id is None:
            return values
        self._require_identity(app_id)
        return tuple(item for item in values if item.record.spec.app_id == app_id)

    def readiness(self, connection_id: str) -> AppReadiness:
        """Return current non-sensitive readiness for one connection."""
        connection = self.get_connection(connection_id).record
        definition = self.get_definition(connection.spec.app_id).record
        return self._readiness_for(connection, definition)

    def mcp_snapshot_for_probe(self, connection_id: str) -> McpSnapshot:
        """Project one connection for a live probe without enabling it for an Agent."""
        connection = self.get_connection(connection_id)
        definition = self.get_definition(connection.record.spec.app_id)
        if not isinstance(definition.record.spec.implementation, AppMcpImplementation):
            raise ExtensionError("invalid_operation", "Native App connections do not use an MCP probe.")
        projected = self._project_mcp(
            connection.record.model_copy(deep=True),
            definition.record.model_copy(deep=True),
            "connection-probe",
        )
        return _mcp_snapshot(
            (
                McpSnapshotEntry(
                    record=projected,
                    revision=_combined_revision(definition.revision, connection.revision),
                ),
            )
        )

    def execution_kind(self, connection_id: str) -> Literal["mcp", "native"]:
        """Return the declared execution boundary for one App connection."""
        connection = self.get_connection(connection_id).record
        implementation = self.get_definition(connection.spec.app_id).record.spec.implementation
        return implementation.type

    def native_tools_for_probe(self, connection_id: str) -> tuple[Any, ...]:
        """Build native tools for diagnostics without changing Agent enablement."""
        connection = self.get_connection(connection_id).record.model_copy(deep=True)
        definition = self.get_definition(connection.spec.app_id).record.model_copy(deep=True)
        if not isinstance(definition.spec.implementation, AppNativeImplementation):
            raise ExtensionError("invalid_operation", "MCP-backed App connections require an MCP probe.")
        return self._build_native_tools(connection, definition)

    def build_native_tools(self, snapshot: AppSnapshot) -> tuple[Any, ...]:
        """Build tools for native App entries captured in one immutable snapshot."""
        tools: list[Any] = []
        for entry in snapshot.entries:
            if isinstance(entry.definition.spec.implementation, AppNativeImplementation):
                tools.extend(self._build_native_tools(entry.connection, entry.definition))
        return tuple(tools)

    def enable_connection(
        self,
        connection_id: str,
        agent_id: str,
        *,
        expected_revision: str,
        confirmed: bool = False,
    ) -> VersionedAppConnection:
        """Enable one ready App connection for an Agent under its tool policy."""
        self._require_identity(agent_id)
        with self._mutation_lock():
            current = self.get_connection(connection_id)
            self._require_revision(current.revision, expected_revision, resource="App connection")
            if agent_id in current.record.spec.enabled_agent_ids:
                return current
            definition = self.get_definition(current.record.spec.app_id).record
            readiness = self._readiness_for(current.record, definition)
            if not readiness.ready:
                raise ExtensionError(
                    "dependency_missing",
                    "App connection dependencies are not ready.",
                    details={"issues": list(readiness.issues)},
                )
            selected = self._selected_tools(current.record, definition)
            if any(tool.risk == "high" for tool in selected) and not confirmed:
                raise ExtensionError("confirmation_required", "High-risk App tools require confirmation.")
            self._require_prefix_available(current.record, agent_id)
            enabled = sorted((*current.record.spec.enabled_agent_ids, agent_id))
            updated = current.record.model_copy(
                update={"spec": current.record.spec.model_copy(update={"enabled_agent_ids": enabled})}
            )
            self._write_connection(updated, expected_revision=expected_revision)
        return self.get_connection(connection_id)

    def disable_connection(
        self,
        connection_id: str,
        agent_id: str,
        *,
        expected_revision: str,
    ) -> VersionedAppConnection:
        """Disable one App connection for an Agent."""
        self._require_identity(agent_id)
        with self._mutation_lock():
            current = self.get_connection(connection_id)
            self._require_revision(current.revision, expected_revision, resource="App connection")
            enabled = [item for item in current.record.spec.enabled_agent_ids if item != agent_id]
            updated = current.record.model_copy(
                update={"spec": current.record.spec.model_copy(update={"enabled_agent_ids": enabled})}
            )
            self._write_connection(updated, expected_revision=expected_revision)
        return self.get_connection(connection_id)

    def remove_connection(self, connection_id: str, *, expected_revision: str) -> None:
        """Remove one inactive App connection without touching referenced Secrets."""
        with self._mutation_lock():
            current = self.get_connection(connection_id)
            self._require_revision(current.revision, expected_revision, resource="App connection")
            if current.record.spec.enabled_agent_ids:
                raise ExtensionError(
                    "extension_in_use",
                    "App connection must be disabled for every Agent before removal.",
                    details={"agentIds": list(current.record.spec.enabled_agent_ids)},
                )
            self._remove_path(
                self._connection_path(connection_id),
                expected_revision=expected_revision,
                current_revision=lambda: self._current_connection_revision(connection_id),
                resource="App connection",
            )

    def snapshot_for_agent(self, agent_id: str) -> AppSnapshot:
        """Capture App resources and their managed MCP projections for one Runtime."""
        self._require_identity(agent_id)
        entries: list[AppSnapshotEntry] = []
        mcp_entries: list[McpSnapshotEntry] = []
        for connection in self.list_connections():
            if agent_id not in connection.record.spec.enabled_agent_ids:
                continue
            definition = self.get_definition(connection.record.spec.app_id)
            frozen_definition = definition.record.model_copy(deep=True)
            frozen_connection = connection.record.model_copy(deep=True)
            entries.append(
                AppSnapshotEntry(
                    definition=frozen_definition,
                    definition_revision=definition.revision,
                    connection=frozen_connection,
                    connection_revision=connection.revision,
                )
            )
            if isinstance(frozen_definition.spec.implementation, AppMcpImplementation):
                projected = self._project_mcp(frozen_connection, frozen_definition, agent_id)
                projection_revision = _combined_revision(definition.revision, connection.revision)
                mcp_entries.append(McpSnapshotEntry(record=projected, revision=projection_revision))
        frozen_entries = tuple(entries)
        mcp = merge_mcp_snapshots(
            McpSnapshot.empty(),
            _mcp_snapshot(tuple(mcp_entries)),
        )
        canonical = json.dumps(
            [
                (
                    entry.definition.metadata.name,
                    entry.definition_revision,
                    entry.connection.metadata.name,
                    entry.connection_revision,
                )
                for entry in frozen_entries
            ],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return AppSnapshot(
            revision=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
            entries=frozen_entries,
            mcp=mcp,
        )

    def _readiness_for(self, connection: AppConnection, definition: AppDefinition) -> AppReadiness:
        auth_state: Literal["not_required", "ready", "missing", "backend_unavailable"]
        auth_state = "not_required" if definition.spec.auth.type == "none" else "ready"
        issues: list[str] = []
        for credential in definition.spec.auth.credentials:
            ref = connection.spec.credential_refs.get(credential.name)
            if ref is None:
                if credential.required:
                    auth_state = "missing"
                    issues.append("credential_missing")
                continue
            state = self.secret_store.status(ref).state
            if state == "backend_unavailable":
                auth_state = "backend_unavailable"
                issues.append("secret_backend_unavailable")
            elif state == "missing" and auth_state != "backend_unavailable":
                auth_state = "missing"
                issues.append("secret_missing")
        executable_state: Literal["ready", "missing", "not_required"] = "not_required"
        adapter_state: Literal["ready", "missing", "not_required"] = "not_required"
        implementation = definition.spec.implementation
        if isinstance(implementation, AppMcpImplementation) and isinstance(
            implementation.transport,
            AppStdioMcpTemplate,
        ):
            executable_state = (
                "ready" if self.executable_resolver(implementation.transport.command) is not None else "missing"
            )
            if executable_state == "missing":
                issues.append("executable_missing")
        tool_state: Literal["ready", "empty"] = (
            "ready" if self._selected_tools(connection, definition) else "empty"
        )
        if tool_state == "empty":
            issues.append("tool_selection_empty")
        if isinstance(implementation, AppNativeImplementation):
            adapter = self.adapter_registry.get(implementation.adapter)
            if adapter is None:
                adapter_state = "missing"
                issues.append("adapter_missing")
            else:
                adapter_readiness = adapter.readiness(
                    self._native_context(connection, definition)
                )
                adapter_state = "ready" if adapter_readiness.ready else "missing"
                issues.extend(adapter_readiness.issues)
        return AppReadiness(
            ready=auth_state in {"not_required", "ready"}
            and executable_state != "missing"
            and adapter_state != "missing"
            and tool_state == "ready",
            auth_state=auth_state,
            executable_state=executable_state,
            adapter_state=adapter_state,
            tool_state=tool_state,
            issues=tuple(sorted(set(issues))),
        )

    def _validate_connection(self, connection: AppConnection, definition: AppDefinition) -> None:
        if connection.spec.app_id != definition.metadata.name:
            raise ExtensionError("invalid_identity", "App connection references the wrong definition.")
        declared_credentials = {item.name for item in definition.spec.auth.credentials}
        if not set(connection.spec.credential_refs).issubset(declared_credentials):
            raise ExtensionError("invalid_auth", "App connection contains an unknown credential slot.")
        declared_tools = {item.name for item in definition.spec.tools}
        if connection.spec.enabled_tools is not None and not set(connection.spec.enabled_tools).issubset(declared_tools):
            raise ExtensionError("invalid_policy", "App connection selects an unknown tool.")

    @staticmethod
    def _selected_tools(
        connection: AppConnection,
        definition: AppDefinition,
    ) -> tuple[AppToolSpec, ...]:
        selected = (
            set(connection.spec.enabled_tools)
            if connection.spec.enabled_tools is not None
            else {tool.name for tool in definition.spec.tools if tool.enabled_by_default}
        )
        return tuple(tool for tool in definition.spec.tools if tool.name in selected)

    def _project_mcp(
        self,
        connection: AppConnection,
        definition: AppDefinition,
        agent_id: str,
    ) -> McpServer:
        selected = self._selected_tools(connection, definition)
        bindings = connection.spec.credential_refs

        def convert(value: AppLiteralValue | AppCredentialValue) -> McpLiteralValue | McpSecretValue:
            if isinstance(value, AppLiteralValue):
                return McpLiteralValue(kind="literal", value=value.value)
            ref = bindings.get(value.credential_slot)
            if ref is None:
                raise ExtensionError("dependency_missing", "App credential binding is missing.")
            return McpSecretValue(
                kind="secret",
                secret_ref=ref,
                prefix=value.prefix,
                suffix=value.suffix,
            )

        implementation = definition.spec.implementation
        if not isinstance(implementation, AppMcpImplementation):
            raise ExtensionError("invalid_operation", "Native App definitions cannot be projected as MCP.")
        template = implementation.transport
        if isinstance(template, AppStdioMcpTemplate):
            transport = McpStdioTransport(
                type="stdio",
                command=template.command,
                args=list(template.args),
                cwd=template.cwd,
                environment={key: convert(value) for key, value in template.environment.items()},
            )
        else:
            assert isinstance(template, AppRemoteMcpTemplate)
            transport = McpRemoteTransport(
                type=template.type,
                url=template.url,
                headers={key: convert(value) for key, value in template.headers.items()},
            )
        prefix = self._tool_prefix(connection)
        risk = max((tool.risk for tool in selected), key=lambda item: _RISK_ORDER[item])
        resource_name = _managed_mcp_id(definition.metadata.name, connection.metadata.name)
        policy = definition.spec.policy
        return McpServer(
            api_version="openppx.io/v1alpha1",
            kind="McpServer",
            metadata={"name": resource_name},
            spec=McpServerSpec(
                display_name=connection.spec.display_name,
                description=definition.spec.description,
                transport=transport,
                policy=McpToolPolicy(
                    tool_filter=[tool.name for tool in selected],
                    tool_name_prefix=prefix,
                    require_confirmation=policy.require_confirmation
                    or connection.spec.require_confirmation
                    or any(tool.risk == "high" for tool in selected),
                    progress_events=policy.progress_events,
                    long_task_proxy=policy.long_task_proxy,
                    inline_budget_ms=policy.inline_budget_ms,
                    job_protocol=policy.job_protocol,
                ),
                risk=risk,
                enabled_agent_ids=[agent_id],
                managed_by=McpOwnerRef(kind="app", name=definition.metadata.name),
            ),
        )

    def _build_native_tools(
        self,
        connection: AppConnection,
        definition: AppDefinition,
    ) -> tuple[Any, ...]:
        implementation = definition.spec.implementation
        if not isinstance(implementation, AppNativeImplementation):
            return ()
        adapter = self.adapter_registry.require(implementation.adapter)
        return tuple(adapter.build_tools(self._native_context(connection, definition)))

    def _native_context(
        self,
        connection: AppConnection,
        definition: AppDefinition,
    ) -> NativeAppContext:
        return NativeAppContext(
            definition=definition,
            connection=connection,
            tools=self._selected_tools(connection, definition),
            secret_store=self.secret_store,
        )

    def _require_prefix_available(self, connection: AppConnection, agent_id: str) -> None:
        if self.prefix_index is None:
            for item in self.list_connections():
                if item.record.metadata.name == connection.metadata.name:
                    continue
                if agent_id in item.record.spec.enabled_agent_ids and self._tool_prefix(item.record) == self._tool_prefix(connection):
                    raise ExtensionError(
                        "extension_conflict",
                        "App tool-name prefix conflicts with another enabled connection.",
                    )
            return
        self.prefix_index.require_available(
            self._tool_prefix(connection),
            agent_id,
            owner_key=f"app:{connection.metadata.name}",
        )

    def _prefix_reservations(self, agent_id: str) -> tuple[ToolPrefixReservation, ...]:
        """Project active App prefixes into the shared conflict index."""
        return tuple(
            ToolPrefixReservation(
                prefix=self._tool_prefix(item.record),
                owner_key=f"app:{item.record.metadata.name}",
            )
            for item in self.list_connections()
            if agent_id in item.record.spec.enabled_agent_ids
        )

    def _identity_reservations(self) -> tuple[ResourceIdentityReservation, ...]:
        """Project directly installed App definition identities."""
        if not self.definitions_dir.exists():
            return ()
        return tuple(
            ResourceIdentityReservation(
                kind="app",
                name=path.stem,
                owner_key=f"app-definition:{path.stem}",
            )
            for path in sorted(self.definitions_dir.glob("*.json"), key=lambda item: item.name)
        )

    @staticmethod
    def _tool_prefix(connection: AppConnection) -> str:
        raw = (
            f"app_{connection.spec.app_id.replace('-', '_')}"
            f"_{connection.metadata.name.replace('-', '_')}"
        )
        if len(raw) <= 128:
            return raw
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
        return f"{raw[:117].rstrip('_')}_{digest}"

    def _write_definition(self, record: AppDefinition, *, expected_revision: str | None) -> None:
        self._write(
            self._definition_path(record.metadata.name),
            record,
            source=f"app-definition:{record.metadata.name}",
            expected_revision=expected_revision,
            current_revision=lambda: self._current_definition_revision(record.metadata.name),
            message="App definition could not be written.",
        )

    def _write_connection(self, record: AppConnection, *, expected_revision: str | None) -> None:
        self._write(
            self._connection_path(record.metadata.name),
            record,
            source=f"app-connection:{record.metadata.name}",
            expected_revision=expected_revision,
            current_revision=lambda: self._current_connection_revision(record.metadata.name),
            message="App connection could not be written.",
        )

    def _write(
        self,
        path: Path,
        record: AppDefinition | AppConnection,
        *,
        source: str,
        expected_revision: str | None,
        current_revision: Callable[[], str | None],
        message: str,
    ) -> None:
        try:
            atomic_write_resource(
                path,
                record,
                source=source,
                expected_revision=expected_revision,
                current_revision=current_revision,
                lock_timeout=self.lock_timeout,
            )
        except ConfigRevisionConflict as exc:
            raise ExtensionError(
                "revision_conflict",
                "App revision does not match current state.",
                details={"expectedRevision": exc.expected_revision, "actualRevision": exc.actual_revision},
            ) from exc
        except ConfigWriteError as exc:
            raise ExtensionError(exc.kind, message) from exc

    def _remove_path(
        self,
        path: Path,
        *,
        expected_revision: str,
        current_revision: Callable[[], str | None],
        resource: str,
    ) -> None:
        lock = FileLock(path.with_name(f"{path.name}.lock"), timeout=self.lock_timeout, mode=0o600)
        try:
            with lock:
                actual = current_revision()
                self._require_revision(actual or "", expected_revision, resource=resource)
                path.unlink()
                _fsync_directory(path.parent)
        except Timeout as exc:
            raise ExtensionError("registry_busy", "App registry is busy; retry with a fresh revision.") from exc
        except OSError as exc:
            raise ExtensionError("write_failed", f"{resource} could not be removed.") from exc

    def _current_definition_revision(self, app_id: str) -> str | None:
        try:
            return self.get_definition(app_id).revision
        except ExtensionError as exc:
            if exc.code == "extension_not_found":
                return None
            raise

    def _current_connection_revision(self, connection_id: str) -> str | None:
        try:
            return self.get_connection(connection_id).revision
        except ExtensionError as exc:
            if exc.code == "extension_not_found":
                return None
            raise

    def _definition_path(self, app_id: str) -> Path:
        return self._resource_path(self.definitions_dir, app_id)

    def _connection_path(self, connection_id: str) -> Path:
        return self._resource_path(self.connections_dir, connection_id)

    def _resource_path(self, directory: Path, resource_id: str) -> Path:
        self._require_identity(resource_id)
        path = (directory / f"{resource_id}.json").resolve(strict=False)
        if not path.is_relative_to(self.root.resolve(strict=False)):
            raise ExtensionError("unsafe_path", "App resource path is outside the Node root.")
        return path

    @staticmethod
    def _require_identity(value: str) -> None:
        if _RESOURCE_NAME_PATTERN.fullmatch(value) is None:
            raise ExtensionError("invalid_identity", "App, connection, or Agent identity is invalid.")

    @staticmethod
    def _require_revision(actual: str, expected: str, *, resource: str) -> None:
        if actual != expected:
            raise ExtensionError(
                "revision_conflict",
                f"{resource} revision does not match current state.",
                details={"expectedRevision": expected, "actualRevision": actual},
            )

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(self.root / "registry.lock", timeout=self.lock_timeout, mode=0o600)
        try:
            with lock:
                yield
        except Timeout as exc:
            raise ExtensionError("registry_busy", "App registry is busy; retry with a fresh revision.") from exc


def _managed_mcp_id(app_id: str, connection_id: str) -> str:
    raw = f"app-{app_id}-{connection_id}"
    if len(raw) <= 63:
        return raw
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
    return f"{raw[:52].rstrip('-')}-{digest}"


def _combined_revision(definition_revision: str, connection_revision: str) -> str:
    canonical = json.dumps(
        [definition_revision, connection_revision],
        separators=(",", ":"),
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


def _mcp_snapshot(entries: tuple[McpSnapshotEntry, ...]) -> McpSnapshot:
    canonical = json.dumps(
        [(entry.record.metadata.name, entry.revision) for entry in entries],
        separators=(",", ":"),
    ).encode("utf-8")
    return McpSnapshot(
        revision=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        entries=entries,
    )


def _fsync_directory(directory: Path) -> None:
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        pass
    finally:
        os.close(descriptor)


__all__ = [
    "AppManager",
    "AppReadiness",
    "AppSnapshot",
    "AppSnapshotEntry",
    "VersionedAppConnection",
    "VersionedAppDefinition",
]
