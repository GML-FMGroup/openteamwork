"""Node-owned direct MCP resource lifecycle and immutable snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, Literal

from filelock import FileLock, Timeout
from pydantic import ValidationError

from openppx.config import (
    ConfigRevisionConflict,
    ConfigWriteError,
    SecretStore,
    config_revision,
    read_json_object,
)
from openppx.config.atomic import atomic_write_resource

from .errors import ExtensionError
from .mcp_models import McpRemoteTransport, McpSecretValue, McpServer, McpStdioTransport


_RESOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True, slots=True)
class VersionedMcp:
    """Validated MCP resource plus its optimistic revision."""

    record: McpServer
    revision: str

    @property
    def status(self) -> str:
        """Return the stable lifecycle status exposed to clients."""
        return "enabled" if self.record.spec.enabled_agent_ids else "disabled"


@dataclass(frozen=True, slots=True)
class McpReadiness:
    """Non-sensitive authentication and executable readiness projection."""

    ready: bool
    auth_state: Literal["ready", "missing", "backend_unavailable"]
    executable_state: Literal["ready", "missing", "not_required"]
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class McpSnapshotEntry:
    """One direct MCP definition pinned to a Runtime snapshot."""

    record: McpServer
    revision: str


@dataclass(frozen=True, slots=True)
class McpSnapshot:
    """Agent-specific immutable set of enabled direct MCP resources."""

    revision: str
    entries: tuple[McpSnapshotEntry, ...]

    @property
    def names(self) -> tuple[str, ...]:
        """Return deterministic MCP resource identifiers."""
        return tuple(entry.record.metadata.name for entry in self.entries)

    @classmethod
    def empty(cls) -> "McpSnapshot":
        """Return the stable empty MCP snapshot."""
        return cls(revision="sha256:" + hashlib.sha256(b"[]").hexdigest(), entries=())


class McpManager:
    """Own strict direct MCP persistence, readiness, enablement, and snapshots."""

    def __init__(
        self,
        node_root: Path,
        secret_store: SecretStore,
        *,
        executable_resolver: Callable[[str], str | None] = shutil.which,
        lock_timeout: float = 5.0,
    ) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.root = self.node_root / "extensions" / "mcp"
        self.records_dir = self.root / "records"
        self.secret_store = secret_store
        self.executable_resolver = executable_resolver
        self.lock_timeout = lock_timeout

    def create(self, record: McpServer, *, expected_revision: str | None) -> VersionedMcp:
        """Create one disabled MCP resource under a create-only precondition."""
        if expected_revision is not None:
            raise ExtensionError("revision_conflict", "New MCP resources require an empty revision precondition.")
        self._require_record_identity(record)
        if record.spec.enabled_agent_ids:
            raise ExtensionError("invalid_operation", "New MCP resources must be enabled through the lifecycle API.")
        with self._mutation_lock():
            self._write(record, expected_revision=None)
        return self.get(record.metadata.name)

    def update(self, candidate: McpServer, *, expected_revision: str) -> VersionedMcp:
        """Update one MCP definition while preserving Agent enablement."""
        self._require_record_identity(candidate)
        with self._mutation_lock():
            current = self.get(candidate.metadata.name)
            self._require_revision(current, expected_revision)
            record = candidate.model_copy(
                update={
                    "spec": candidate.spec.model_copy(
                        update={"enabled_agent_ids": list(current.record.spec.enabled_agent_ids)}
                    )
                }
            )
            if record.spec.enabled_agent_ids:
                readiness = self._readiness_for(record)
                if not readiness.ready:
                    raise ExtensionError("dependency_missing", "Updated MCP resource is not ready.")
                for agent_id in record.spec.enabled_agent_ids:
                    self._require_prefix_available(record, agent_id, ignore_id=record.metadata.name)
            self._write(record, expected_revision=expected_revision)
        return self.get(candidate.metadata.name)

    def get(self, server_id: str) -> VersionedMcp:
        """Read one direct MCP resource by stable identity."""
        path = self._record_path(server_id)
        if not path.exists():
            raise ExtensionError("extension_not_found", f"MCP Server '{server_id}' was not found.")
        try:
            raw = read_json_object(path, source=f"mcp:{server_id}")
            record = McpServer.model_validate(raw)
        except (ValidationError, ValueError) as exc:
            raise ExtensionError("invalid_registry", "Persisted MCP resource is invalid.") from exc
        self._require_record_identity(record)
        return VersionedMcp(record=record, revision=config_revision(record))

    def list(self) -> tuple[VersionedMcp, ...]:
        """Return every direct MCP resource in deterministic order."""
        if not self.records_dir.exists():
            return ()
        return tuple(self.get(path.stem) for path in sorted(self.records_dir.glob("*.json"), key=lambda item: item.name))

    def readiness(self, server_id: str) -> McpReadiness:
        """Return current Secret and executable readiness without resolving values."""
        return self._readiness_for(self.get(server_id).record)

    def enable(
        self,
        server_id: str,
        agent_id: str,
        *,
        expected_revision: str,
        confirmed: bool = False,
    ) -> VersionedMcp:
        """Enable one ready MCP resource for an Agent under policy checks."""
        _validate_resource_name(agent_id)
        with self._mutation_lock():
            current = self.get(server_id)
            self._require_revision(current, expected_revision)
            if agent_id in current.record.spec.enabled_agent_ids:
                return current
            readiness = self._readiness_for(current.record)
            if not readiness.ready:
                raise ExtensionError(
                    "dependency_missing",
                    "MCP resource dependencies are not ready.",
                    details={"issues": list(readiness.issues)},
                )
            if current.record.spec.risk == "high" and not confirmed:
                raise ExtensionError("confirmation_required", "High-risk MCP enablement requires confirmation.")
            self._require_prefix_available(current.record, agent_id, ignore_id=server_id)
            enabled = sorted((*current.record.spec.enabled_agent_ids, agent_id))
            updated = current.record.model_copy(
                update={"spec": current.record.spec.model_copy(update={"enabled_agent_ids": enabled})}
            )
            self._write(updated, expected_revision=expected_revision)
        return self.get(server_id)

    def disable(self, server_id: str, agent_id: str, *, expected_revision: str) -> VersionedMcp:
        """Disable one MCP resource for an Agent."""
        _validate_resource_name(agent_id)
        with self._mutation_lock():
            current = self.get(server_id)
            self._require_revision(current, expected_revision)
            enabled = [item for item in current.record.spec.enabled_agent_ids if item != agent_id]
            updated = current.record.model_copy(
                update={"spec": current.record.spec.model_copy(update={"enabled_agent_ids": enabled})}
            )
            self._write(updated, expected_revision=expected_revision)
        return self.get(server_id)

    def remove(self, server_id: str, *, expected_revision: str) -> None:
        """Remove one disabled direct MCP resource under its owner boundary."""
        with self._mutation_lock():
            current = self.get(server_id)
            self._require_revision(current, expected_revision)
            if current.record.spec.managed_by is not None:
                raise ExtensionError("invalid_operation", "Managed MCP resources must be removed by their owner.")
            if current.record.spec.enabled_agent_ids:
                raise ExtensionError(
                    "extension_in_use",
                    "MCP resource must be disabled for every Agent before removal.",
                    details={"agentIds": list(current.record.spec.enabled_agent_ids)},
                )
            path = self._record_path(server_id)
            lock = FileLock(path.with_name(f"{path.name}.lock"), timeout=self.lock_timeout, mode=0o600)
            try:
                with lock:
                    fresh = self.get(server_id)
                    self._require_revision(fresh, expected_revision)
                    path.unlink()
                    _fsync_directory(path.parent)
            except Timeout as exc:
                raise ExtensionError("registry_busy", "MCP registry is busy; retry with a fresh revision.") from exc
            except OSError as exc:
                raise ExtensionError("write_failed", "MCP resource could not be removed.") from exc

    def snapshot_for_agent(self, agent_id: str) -> McpSnapshot:
        """Capture enabled direct MCP definitions for one newly assembled Runtime."""
        _validate_resource_name(agent_id)
        entries = tuple(
            McpSnapshotEntry(item.record.model_copy(deep=True), item.revision)
            for item in self.list()
            if agent_id in item.record.spec.enabled_agent_ids
        )
        return _snapshot_from_entries(entries)

    def snapshot_all(self) -> McpSnapshot:
        """Capture every configured MCP resource for operator diagnostics."""
        entries = tuple(
            McpSnapshotEntry(item.record.model_copy(deep=True), item.revision)
            for item in self.list()
        )
        return _snapshot_from_entries(entries)

    def _readiness_for(self, record: McpServer) -> McpReadiness:
        auth_state: Literal["ready", "missing", "backend_unavailable"] = "ready"
        issues: list[str] = []
        transport = record.spec.transport
        bindings = transport.environment.values() if isinstance(transport, McpStdioTransport) else transport.headers.values()
        for binding in bindings:
            if not isinstance(binding, McpSecretValue):
                continue
            state = self.secret_store.status(binding.secret_ref).state
            if state == "backend_unavailable":
                auth_state = "backend_unavailable"
                issues.append("secret_backend_unavailable")
            elif state == "missing" and auth_state != "backend_unavailable":
                auth_state = "missing"
                issues.append("secret_missing")
        executable_state: Literal["ready", "missing", "not_required"] = "not_required"
        if isinstance(transport, McpStdioTransport):
            executable_state = "ready" if self.executable_resolver(transport.command) is not None else "missing"
            if executable_state == "missing":
                issues.append("executable_missing")
        return McpReadiness(
            ready=auth_state == "ready" and executable_state != "missing",
            auth_state=auth_state,
            executable_state=executable_state,
            issues=tuple(sorted(set(issues))),
        )

    def _require_prefix_available(self, record: McpServer, agent_id: str, *, ignore_id: str) -> None:
        prefix = record.spec.policy.resolved_prefix(record.metadata.name)
        for item in self.list():
            if item.record.metadata.name == ignore_id or agent_id not in item.record.spec.enabled_agent_ids:
                continue
            other = item.record.spec.policy.resolved_prefix(item.record.metadata.name)
            if other == prefix:
                raise ExtensionError(
                    "extension_conflict",
                    "MCP tool-name prefix conflicts with another resource enabled for this Agent.",
                )

    def _write(self, record: McpServer, *, expected_revision: str | None) -> None:
        path = self._record_path(record.metadata.name)
        try:
            atomic_write_resource(
                path,
                record,
                source=f"mcp:{record.metadata.name}",
                expected_revision=expected_revision,
                current_revision=lambda: self._current_revision(record.metadata.name),
                lock_timeout=self.lock_timeout,
            )
        except ConfigRevisionConflict as exc:
            raise ExtensionError(
                "revision_conflict",
                "MCP revision does not match current state.",
                details={"expectedRevision": exc.expected_revision, "actualRevision": exc.actual_revision},
            ) from exc
        except ConfigWriteError as exc:
            raise ExtensionError(exc.kind, "MCP resource could not be written.") from exc

    def _current_revision(self, server_id: str) -> str | None:
        try:
            return self.get(server_id).revision
        except ExtensionError as exc:
            if exc.code == "extension_not_found":
                return None
            raise

    def _record_path(self, server_id: str) -> Path:
        _validate_resource_name(server_id)
        path = (self.records_dir / f"{server_id}.json").resolve(strict=False)
        if not path.is_relative_to(self.root.resolve(strict=False)):
            raise ExtensionError("unsafe_path", "MCP resource path is outside the Node root.")
        return path

    @staticmethod
    def _require_record_identity(record: McpServer) -> None:
        _validate_resource_name(record.metadata.name)

    @staticmethod
    def _require_revision(current: VersionedMcp, expected_revision: str) -> None:
        if current.revision != expected_revision:
            raise ExtensionError(
                "revision_conflict",
                "MCP revision does not match current state.",
                details={"expectedRevision": expected_revision, "actualRevision": current.revision},
            )

    @contextmanager
    def _mutation_lock(self) -> Iterator[None]:
        self.root.mkdir(parents=True, exist_ok=True)
        lock = FileLock(self.root / "registry.lock", timeout=self.lock_timeout, mode=0o600)
        try:
            with lock:
                yield
        except Timeout as exc:
            raise ExtensionError("registry_busy", "MCP registry is busy; retry with a fresh revision.") from exc


def _validate_resource_name(value: str) -> None:
    if _RESOURCE_NAME_PATTERN.fullmatch(value) is None:
        raise ExtensionError("invalid_identity", "MCP or Agent identity is invalid.")


def _snapshot_from_entries(entries: tuple[McpSnapshotEntry, ...]) -> McpSnapshot:
    """Build one deterministic immutable MCP snapshot from pinned entries."""
    canonical = json.dumps(
        [(entry.record.metadata.name, entry.revision) for entry in entries],
        ensure_ascii=False,
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
    "McpManager",
    "McpReadiness",
    "McpSnapshot",
    "McpSnapshotEntry",
    "VersionedMcp",
]
