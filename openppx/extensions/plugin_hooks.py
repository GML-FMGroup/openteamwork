"""Codex-compatible Plugin Hook parsing, trust, snapshots, and execution.

Plugin Hooks are executable host commands. OpenPPX therefore separates package
installation from exact-definition trust: a changed package digest or Hook
definition automatically returns to an untrusted state.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, StrictInt, ValidationError, field_validator, model_validator

from openppx.config import ConfigWriteError, config_revision, read_json_object
from openppx.config.atomic import atomic_write_resource
from openppx.config.models import ResourceMetadata, ResourceName, StrictConfigModel

from .errors import ExtensionError


HookEvent = Literal[
    "PreToolUse",
    "PermissionRequest",
    "PostToolUse",
    "PreCompact",
    "PostCompact",
    "UserPromptSubmit",
    "SubagentStop",
    "Stop",
    "SessionStart",
    "SubagentStart",
    "SessionEnd",
]

SUPPORTED_HOOK_EVENTS: tuple[str, ...] = (
    "PreToolUse",
    "PostToolUse",
    "UserPromptSubmit",
    "SubagentStop",
    "Stop",
    "SessionStart",
    "SubagentStart",
)
_KNOWN_EVENTS = frozenset(
    {
        *SUPPORTED_HOOK_EVENTS,
        "PermissionRequest",
        "PreCompact",
        "PostCompact",
        "SessionEnd",
    }
)
_BLOCKING_EVENTS = frozenset({"PreToolUse", "UserPromptSubmit", "SessionStart", "SubagentStart"})


class PluginHookHandler(StrictConfigModel):
    """One command, prompt, or agent Hook handler from the Codex schema."""

    type: Literal["command", "prompt", "agent"]
    command: str | None = None
    prompt: str | None = None
    timeout: StrictInt = Field(default=10, ge=1, le=60)
    asynchronous: StrictBool = Field(default=False, alias="async")
    status_message: str | None = None
    once: StrictBool = False

    @field_validator("command", "prompt", "status_message")
    @classmethod
    def text_is_bounded(cls, value: str | None) -> str | None:
        """Reject blank/control-bearing executable or prompt text."""
        if value is None:
            return None
        if not value.strip() or len(value) > 16_384 or "\x00" in value:
            raise ValueError("Hook text is invalid")
        return value

    def executable(self) -> bool:
        """Return whether OpenPPX can execute this handler synchronously."""
        return self.type == "command" and bool(self.command) and not self.asynchronous

    @model_validator(mode="after")
    def handler_shape_is_consistent(self) -> "PluginHookHandler":
        """Require the payload associated with the declared handler type."""
        if self.type == "command" and self.command is None:
            raise ValueError("Command Hook requires command")
        if self.type in {"prompt", "agent"} and self.prompt is None:
            raise ValueError("Prompt or agent Hook requires prompt")
        return self


class PluginHookGroup(StrictConfigModel):
    """Matcher group containing one or more Hook handlers."""

    matcher: str = ""
    hooks: list[PluginHookHandler] = Field(min_length=1, max_length=64)

    @field_validator("matcher")
    @classmethod
    def matcher_is_safe_regex(cls, value: str) -> str:
        """Validate bounded matcher syntax while accepting Codex's `*` wildcard."""
        if len(value) > 512 or "\x00" in value:
            raise ValueError("Hook matcher is invalid")
        if value not in {"", "*"}:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError("Hook matcher is not a valid regular expression") from exc
        return value

    def matches(self, value: str) -> bool:
        """Return whether this group applies to one event match value."""
        return self.matcher in {"", "*"} or re.search(self.matcher, value) is not None


@dataclass(frozen=True, slots=True)
class ParsedPluginHooks:
    """Canonical Hook definitions discovered from one Plugin package."""

    events: tuple[tuple[str, tuple[PluginHookGroup, ...]], ...]
    digest: str

    @classmethod
    def empty(cls) -> "ParsedPluginHooks":
        """Return an empty, stable Hook definition."""
        return cls((), f"sha256:{hashlib.sha256(b'{}').hexdigest()}")

    @property
    def handler_count(self) -> int:
        """Return the number of declared handlers."""
        return sum(len(group.hooks) for _event, groups in self.events for group in groups)

    @property
    def executable_count(self) -> int:
        """Return the number of synchronous command handlers."""
        return sum(
            handler.executable()
            for _event, groups in self.events
            for group in groups
            for handler in group.hooks
        )

    @property
    def event_names(self) -> tuple[str, ...]:
        """Return deterministic declared event names."""
        return tuple(event for event, _groups in self.events)

    def groups_for(self, event: str) -> tuple[PluginHookGroup, ...]:
        """Return matcher groups for one event."""
        return next((groups for name, groups in self.events if name == event), ())


class PluginHookTrustSpec(StrictConfigModel):
    """Exact installed package and Hook hashes trusted by one local user."""

    plugin_digest: str
    hook_digest: str
    trusted_at: str

    @field_validator("plugin_digest", "hook_digest")
    @classmethod
    def digest_is_sha256(cls, value: str) -> str:
        """Require a full SHA-256 identity."""
        if re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("Hook trust digest is invalid")
        return value


class PluginHookTrustRecord(StrictConfigModel):
    """Private Node-owned Hook trust resource."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["PluginHookTrust"]
    metadata: ResourceMetadata
    spec: PluginHookTrustSpec


@dataclass(frozen=True, slots=True)
class PluginHookStatus:
    """Client-safe Hook trust and support status."""

    plugin_id: str
    plugin_revision: str
    plugin_digest: str
    hook_digest: str
    trusted: bool
    declared_events: tuple[str, ...]
    supported_events: tuple[str, ...]
    handler_count: int
    executable_count: int
    unsupported_handlers: int
    handlers: tuple[dict[str, Any], ...]

    def to_payload(self) -> dict[str, Any]:
        """Return a non-sensitive Desktop/CLI payload."""
        return {
            "pluginId": self.plugin_id,
            "pluginRevision": self.plugin_revision,
            "pluginDigest": self.plugin_digest,
            "hookDigest": self.hook_digest,
            "trusted": self.trusted,
            "declaredEvents": list(self.declared_events),
            "supportedEvents": list(self.supported_events),
            "handlerCount": self.handler_count,
            "executableCount": self.executable_count,
            "unsupportedHandlers": self.unsupported_handlers,
            "handlers": [dict(item) for item in self.handlers],
        }


class PluginHookTrustStore:
    """Persist exact-hash Hook trust below the explicit Node root."""

    def __init__(self, node_root: Path, *, lock_timeout: float = 5.0) -> None:
        self.root = node_root.expanduser().resolve(strict=False) / "extensions" / "plugins" / "hook-trust"
        self.lock_timeout = lock_timeout

    def is_trusted(self, plugin_id: str, plugin_digest: str, hook_digest: str) -> bool:
        """Return true only for the exact currently installed definitions."""
        record = self._read_optional(plugin_id)
        return bool(
            record
            and record.spec.plugin_digest == plugin_digest
            and record.spec.hook_digest == hook_digest
        )

    def trust(self, plugin_id: ResourceName, plugin_digest: str, hook_digest: str) -> PluginHookTrustRecord:
        """Create or replace one explicit trust decision."""
        current = self._read_optional(plugin_id)
        record = PluginHookTrustRecord(
            api_version="openppx.io/v1alpha1",
            kind="PluginHookTrust",
            metadata=ResourceMetadata(name=plugin_id),
            spec=PluginHookTrustSpec(
                plugin_digest=plugin_digest,
                hook_digest=hook_digest,
                trusted_at=datetime.now(UTC).isoformat(),
            ),
        )
        try:
            atomic_write_resource(
                self._path(plugin_id),
                record,
                source=f"plugin-hook-trust:{plugin_id}",
                expected_revision=None if current is None else config_revision(current),
                current_revision=lambda: (
                    config_revision(value) if (value := self._read_optional(plugin_id)) is not None else None
                ),
                lock_timeout=self.lock_timeout,
            )
        except ConfigWriteError as exc:
            raise ExtensionError(exc.kind, "Plugin Hook trust could not be stored.") from exc
        return record

    def untrust(self, plugin_id: str) -> None:
        """Remove one trust decision; missing records are already untrusted."""
        path = self._path(plugin_id)
        try:
            path.unlink(missing_ok=True)
        except OSError as exc:
            raise ExtensionError("write_failed", "Plugin Hook trust could not be removed.") from exc

    def _read_optional(self, plugin_id: str) -> PluginHookTrustRecord | None:
        path = self._path(plugin_id)
        if not path.exists():
            return None
        try:
            return PluginHookTrustRecord.model_validate(
                read_json_object(path, source=f"plugin-hook-trust:{plugin_id}")
            )
        except (ValidationError, ValueError) as exc:
            raise ExtensionError("invalid_registry", "Plugin Hook trust record is invalid.") from exc

    def _path(self, plugin_id: str) -> Path:
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", plugin_id) is None:
            raise ExtensionError("invalid_identity", "Plugin identity is invalid.")
        return self.root / f"{plugin_id}.json"


@dataclass(frozen=True, slots=True)
class PluginHookSnapshotEntry:
    """One trusted Plugin Hook definition pinned to an Agent runtime."""

    plugin_id: str
    plugin_digest: str
    content_root: Path
    data_root: Path
    hooks: ParsedPluginHooks


@dataclass(frozen=True, slots=True)
class PluginHookSnapshot:
    """Trusted Hook packages projected into one immutable runtime."""

    revision: str
    entries: tuple[PluginHookSnapshotEntry, ...]

    @classmethod
    def empty(cls) -> "PluginHookSnapshot":
        """Return a stable empty snapshot."""
        return cls(f"sha256:{hashlib.sha256(b'[]').hexdigest()}", ())


@dataclass(frozen=True, slots=True)
class PluginHookExecution:
    """One bounded command result without exposing stdout or stderr."""

    plugin_id: str
    event: str
    exit_code: int
    duration_ms: int
    timed_out: bool
    output_truncated: bool


class PluginHookRejected(RuntimeError):
    """Raised when a trusted blocking Hook rejects an operation."""


class PluginHookExecutor:
    """Execute trusted synchronous command Hooks with time/output bounds."""

    def __init__(self, snapshot: PluginHookSnapshot, *, max_output_bytes: int = 64 * 1024) -> None:
        self.snapshot = snapshot
        self.max_output_bytes = max_output_bytes
        self._once: set[tuple[str, str, str]] = set()

    async def emit(
        self,
        event: str,
        payload: dict[str, Any],
        *,
        match_value: str = "",
        cwd: Path,
    ) -> tuple[PluginHookExecution, ...]:
        """Execute all matching trusted handlers for one supported event."""
        if event not in SUPPORTED_HOOK_EVENTS:
            return ()
        results: list[PluginHookExecution] = []
        for entry in self.snapshot.entries:
            for group_index, group in enumerate(entry.hooks.groups_for(event)):
                if not group.matches(match_value):
                    continue
                for handler_index, handler in enumerate(group.hooks):
                    if not handler.executable():
                        continue
                    once_key = (entry.plugin_id, event, f"{group_index}:{handler_index}")
                    if handler.once and once_key in self._once:
                        continue
                    if handler.once:
                        self._once.add(once_key)
                    result = await self._run_command(entry, event, handler, payload, cwd=cwd)
                    results.append(result)
                    if result.exit_code != 0 and event in _BLOCKING_EVENTS:
                        raise PluginHookRejected(
                            f"Plugin '{entry.plugin_id}' rejected {event} (exit {result.exit_code})."
                        )
        return tuple(results)

    async def _run_command(
        self,
        entry: PluginHookSnapshotEntry,
        event: str,
        handler: PluginHookHandler,
        payload: dict[str, Any],
        *,
        cwd: Path,
    ) -> PluginHookExecution:
        command = handler.command or ""
        entry.data_root.mkdir(parents=True, exist_ok=True)
        environment = dict(os.environ)
        environment.update(
            {
                "PLUGIN_ROOT": str(entry.content_root),
                "PLUGIN_DATA": str(entry.data_root),
                "CLAUDE_PLUGIN_ROOT": str(entry.content_root),
                "CLAUDE_PLUGIN_DATA": str(entry.data_root),
            }
        )
        wire = json.dumps(
            {"hook_event_name": event, "plugin_id": entry.plugin_id, **payload},
            ensure_ascii=False,
            default=_json_default,
        ).encode("utf-8")
        if len(wire) > 256 * 1024:
            wire = wire[: 256 * 1024]
        started = time.monotonic()
        process = await asyncio.create_subprocess_shell(
            command,
            executable="/bin/sh" if Path("/bin/sh").exists() else None,
            cwd=str(cwd),
            env=environment,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=os.name != "nt",
        )
        timed_out = False
        try:
            stdout, stderr = await asyncio.wait_for(process.communicate(wire), timeout=handler.timeout)
        except TimeoutError:
            timed_out = True
            if os.name != "nt":
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            else:  # pragma: no cover - Windows product build
                process.kill()
            stdout, stderr = await process.communicate()
        output_truncated = len(stdout) + len(stderr) > self.max_output_bytes
        exit_code = 124 if timed_out else int(process.returncode or 0)
        return PluginHookExecution(
            plugin_id=entry.plugin_id,
            event=event,
            exit_code=exit_code,
            duration_ms=max(0, int((time.monotonic() - started) * 1000)),
            timed_out=timed_out,
            output_truncated=output_truncated,
        )


def parse_plugin_hooks(documents: list[dict[str, Any]]) -> ParsedPluginHooks:
    """Parse and canonically merge standard Hook documents."""
    merged: dict[str, list[PluginHookGroup]] = {}
    for document in documents:
        raw_events = document.get("hooks", document)
        if not isinstance(raw_events, dict):
            raise ExtensionError("invalid_manifest", "Plugin Hook document must contain a hooks object.")
        for event, raw_groups in raw_events.items():
            if event not in _KNOWN_EVENTS or not isinstance(raw_groups, list) or len(raw_groups) > 128:
                raise ExtensionError("invalid_manifest", "Plugin Hook event or matcher groups are invalid.")
            try:
                groups = [PluginHookGroup.model_validate(item) for item in raw_groups]
            except (ValidationError, ValueError) as exc:
                raise ExtensionError("invalid_manifest", "Plugin Hook matcher group is invalid.") from exc
            merged.setdefault(event, []).extend(groups)
    events = tuple((event, tuple(merged[event])) for event in sorted(merged))
    canonical = json.dumps(
        {
            event: [group.model_dump(mode="json", by_alias=True) for group in groups]
            for event, groups in events
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return ParsedPluginHooks(events, f"sha256:{hashlib.sha256(canonical).hexdigest()}")


def _json_default(value: object) -> object:
    """Project common runtime objects into bounded Hook JSON."""
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", by_alias=True)
    return str(value)


__all__ = [
    "ParsedPluginHooks",
    "PluginHookExecutor",
    "PluginHookGroup",
    "PluginHookHandler",
    "PluginHookRejected",
    "PluginHookSnapshot",
    "PluginHookSnapshotEntry",
    "PluginHookStatus",
    "PluginHookTrustStore",
    "SUPPORTED_HOOK_EVENTS",
    "parse_plugin_hooks",
]
