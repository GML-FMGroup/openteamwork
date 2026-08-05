"""Portable Plugin staging, lifecycle, and standard component projection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

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
from openppx.config.models import ResourceMetadata

from .errors import ExtensionError
from .indexes import ResourceIdentityIndex, ResourceIdentityReservation
from .mcp import McpSnapshot, McpSnapshotEntry, merge_mcp_snapshots
from .mcp_models import (
    McpEnvironmentValue,
    McpLiteralValue,
    McpOwnerRef,
    McpRemoteTransport,
    McpServer,
    McpServerSpec,
    McpStdioTransport,
    McpToolPolicy,
)
from .models import ExtensionSourceRef, SkillManifest
from .plugin_models import (
    PluginManifest,
    PluginRecord,
    PluginRecordSpec,
    PluginRegisteredApp,
    PluginResourceRef,
    PluginResources,
)
from .plugin_hooks import (
    ParsedPluginHooks,
    PluginHookSnapshot,
    PluginHookSnapshotEntry,
    PluginHookStatus,
    PluginHookTrustStore,
    SUPPORTED_HOOK_EVENTS,
    parse_plugin_hooks,
)
from .prefixes import ToolPrefixIndex, ToolPrefixReservation
from .skills import SkillSnapshot, SkillSnapshotEntry, parse_skill_manifest
from .sources import (
    BuiltinSourceAdapter,
    CatalogSourceAdapter,
    GitSourceAdapter,
    LocalArchiveSourceAdapter,
    LocalDirectorySourceAdapter,
    NpmSourceAdapter,
    SourceAdapter,
    SourceLimits,
    StagedExtension,
    StagingStore,
)


_MANIFEST_PATH = ".agent-plugin/plugin.json"
_RESOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ENVIRONMENT_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
_MCP_CONFIG_KEYS = {
    "args",
    "bearer_token_env_var",
    "command",
    "cwd",
    "disabled_tools",
    "enabled",
    "enabled_tools",
    "env",
    "env_http_headers",
    "env_vars",
    "http_headers",
    "required",
    "startup_timeout_sec",
    "tool_timeout_sec",
    "transport",
    "url",
}


@dataclass(frozen=True, slots=True)
class ParsedPluginSkill:
    """One standard Skill projected into an internal Plugin namespace."""

    ref: PluginResourceRef
    manifest: SkillManifest


@dataclass(frozen=True, slots=True)
class ParsedPluginMcp:
    """One standard bundled MCP server projected into an internal namespace."""

    ref: PluginResourceRef
    server: McpServer
    environment_keys: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class PluginBundle:
    """Fully parsed standard Plugin components without executable host code."""

    skills: tuple[ParsedPluginSkill, ...]
    mcp_servers: tuple[ParsedPluginMcp, ...]
    registered_apps: tuple[PluginRegisteredApp, ...]
    hook_paths: tuple[str, ...]
    inline_hook_count: int
    hooks: ParsedPluginHooks
    effective_risk: str

    @property
    def resources(self) -> PluginResources:
        """Return the stable installed inventory derived from package files."""
        return PluginResources(
            skills=[item.ref for item in self.skills],
            mcp_servers=[item.ref for item in self.mcp_servers],
            apps=list(self.registered_apps),
            hook_paths=list(self.hook_paths),
            inline_hook_count=self.inline_hook_count,
        )


@dataclass(frozen=True, slots=True)
class StagedPlugin:
    """Pinned source content plus its validated manifest and components."""

    extension: StagedExtension
    manifest: PluginManifest
    bundle: PluginBundle


@dataclass(frozen=True, slots=True)
class PluginPreview:
    """Client-safe Plugin preview produced before installation."""

    plugin_id: str
    display_name: str
    description: str
    version: str
    digest: str
    source_type: str
    trust: str
    risk: str
    resource_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class PluginReadiness:
    """Non-sensitive enablement readiness for one Plugin."""

    ready: bool
    issues: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VersionedPlugin:
    """Validated installed Plugin record and immutable content location."""

    record: PluginRecord
    revision: str
    content_root: Path

    @property
    def status(self) -> str:
        """Return the stable installed enablement state."""
        return "enabled" if self.record.spec.enabled_agent_ids else "disabled"


@dataclass(frozen=True, slots=True)
class PluginSnapshotEntry:
    """One installed Plugin pinned to a Runtime snapshot."""

    record: PluginRecord
    revision: str
    content_root: Path


@dataclass(frozen=True, slots=True)
class PluginSnapshot:
    """Agent-specific Plugin, Skill, and MCP projections."""

    revision: str
    entries: tuple[PluginSnapshotEntry, ...]
    skills: SkillSnapshot
    mcp: McpSnapshot
    hooks: PluginHookSnapshot

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        """Return deterministic enabled Plugin identities."""
        return tuple(entry.record.metadata.name for entry in self.entries)

    @classmethod
    def empty(cls) -> "PluginSnapshot":
        """Return a stable empty Plugin snapshot."""
        digest = hashlib.sha256(b"[]").hexdigest()
        return cls(
            revision=f"sha256:{digest}",
            entries=(),
            skills=SkillSnapshot.empty(),
            mcp=McpSnapshot.empty(),
            hooks=PluginHookSnapshot.empty(),
        )


RegisteredAppResolver = Callable[[PluginRegisteredApp], bool]


class PluginManager:
    """Own portable Plugin staging, persistence, enablement, and projection."""

    def __init__(
        self,
        node_root: Path,
        secret_store: SecretStore,
        *,
        builtin_plugins: Mapping[str, Path] | None = None,
        catalog_adapters: Mapping[str, CatalogSourceAdapter] | None = None,
        source_limits: SourceLimits | None = None,
        executable_resolver: Callable[[str], str | None] = shutil.which,
        available_environment_keys: frozenset[str] = frozenset(),
        identity_index: ResourceIdentityIndex | None = None,
        reference_index: object | None = None,
        prefix_index: ToolPrefixIndex | None = None,
        lock_timeout: float = 5.0,
    ) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.root = self.node_root / "extensions" / "plugins"
        self.records_dir = self.root / "records"
        self.content_dir = self.root / "content"
        self.staging = StagingStore(
            self.node_root,
            limits=source_limits,
            required_root_file=_MANIFEST_PATH,
        )
        self.secret_store = secret_store
        self.executable_resolver = executable_resolver
        self.available_environment_keys = available_environment_keys
        self.identity_index = identity_index
        self.reference_index = reference_index
        self.prefix_index = prefix_index
        self.lock_timeout = lock_timeout
        self.hook_trust = PluginHookTrustStore(self.node_root, lock_timeout=lock_timeout)
        self._registered_app_resolvers: dict[str, RegisteredAppResolver] = {}
        builtin_roots = {
            identifier: path.expanduser().resolve(strict=False)
            for identifier, path in (builtin_plugins or {}).items()
        }
        self._adapters: dict[str, SourceAdapter] = {
            "builtin": BuiltinSourceAdapter(builtin_roots),
            "local_directory": LocalDirectorySourceAdapter(),
            "local_archive": LocalArchiveSourceAdapter(),
            "git": GitSourceAdapter(),
            "npm": NpmSourceAdapter(),
        }
        self._catalog_adapters = dict(catalog_adapters or {})
        if identity_index is not None:
            identity_index.register("plugins", self._identity_reservations)
        if prefix_index is not None:
            prefix_index.register("plugins", self._prefix_reservations)

    def register_app_resolver(self, provider_id: str, resolver: RegisteredAppResolver) -> None:
        """Register one host resolver for standard `.app.json` technical IDs."""
        if provider_id in self._registered_app_resolvers:
            raise ValueError(f"Plugin App resolver '{provider_id}' is already registered.")
        self._registered_app_resolvers[provider_id] = resolver

    def stage(self, reference: ExtensionSourceRef) -> StagedPlugin:
        """Stage and fully validate one portable Plugin without installing it."""
        adapter = (
            self._catalog_adapters.get(reference.provider or "")
            if reference.type == "catalog"
            else self._adapters.get(reference.type)
        )
        if adapter is None:
            raise ExtensionError("invalid_source", "No Source Adapter is registered for this reference.")
        staged = adapter.stage(reference, self.staging)
        try:
            manifest = parse_plugin_manifest(staged.content_root / _MANIFEST_PATH)
            bundle = self._load_bundle(staged.content_root, manifest)
            return StagedPlugin(staged, manifest, bundle)
        except Exception:
            staged.cleanup()
            raise

    @staticmethod
    def preview(staged: StagedPlugin) -> PluginPreview:
        """Return a bounded preview suitable for CLI/Desktop confirmation."""
        bundle = staged.bundle
        return PluginPreview(
            plugin_id=staged.manifest.name,
            display_name=_display_name(staged.manifest),
            description=staged.manifest.description,
            version=staged.manifest.version,
            digest=staged.extension.digest,
            source_type=staged.extension.source.type,
            trust=_trust_for_source(staged.extension.source.type),
            risk=bundle.effective_risk,
            resource_counts={
                "apps": len(bundle.registered_apps),
                "hooks": len(bundle.hook_paths) + bundle.inline_hook_count,
                "mcpServers": len(bundle.mcp_servers),
                "skills": len(bundle.skills),
            },
        )

    def install(
        self,
        staged: StagedPlugin,
        *,
        expected_revision: str | None,
        confirmed: bool = False,
    ) -> VersionedPlugin:
        """Publish one validated Plugin record over immutable staged content."""
        plugin_id = staged.manifest.name
        _validate_resource_name(plugin_id)
        previous = self._read_optional(plugin_id)
        try:
            if previous is None:
                if expected_revision is not None:
                    raise ExtensionError("revision_conflict", "New Plugins require an empty revision precondition.")
                enabled_agents: list[str] = []
            else:
                self._require_revision(previous, expected_revision)
                enabled_agents = list(previous.record.spec.enabled_agent_ids)
            self._require_identities_available(plugin_id, staged.bundle)
            if enabled_agents:
                readiness = self._readiness_for(staged.bundle)
                blocking_issues = tuple(
                    issue for issue in readiness.issues if not issue.startswith("plugin_hooks_")
                )
                if blocking_issues:
                    raise ExtensionError(
                        "dependency_missing",
                        "Updated Plugin dependencies are not ready.",
                        details={"issues": list(blocking_issues)},
                    )
                if staged.bundle.effective_risk == "high" and not confirmed:
                    raise ExtensionError(
                        "confirmation_required",
                        "Updating an active high-risk Plugin requires confirmation.",
                    )
                for agent_id in enabled_agents:
                    self._require_prefixes_available(plugin_id, staged.bundle, agent_id)
            target = self._content_path(plugin_id, staged.extension.digest)
            self._activate_content(staged.extension.content_root, target)
            preview = self.preview(staged)
            record = PluginRecord(
                api_version="openppx.io/v1alpha1",
                kind="Plugin",
                metadata=ResourceMetadata(name=plugin_id),
                spec=PluginRecordSpec(
                    display_name=preview.display_name,
                    description=preview.description,
                    version=preview.version,
                    developer=_developer_name(staged.manifest),
                    digest=preview.digest,
                    source=staged.extension.source,
                    trust=preview.trust,
                    risk=preview.risk,
                    resources=staged.bundle.resources,
                    enabled_agent_ids=enabled_agents,
                ),
            )
            self._write_record(record, expected_revision=expected_revision)
            return self.get(plugin_id)
        finally:
            staged.extension.cleanup()

    def update(
        self,
        staged: StagedPlugin,
        *,
        expected_revision: str,
        confirmed: bool = False,
    ) -> VersionedPlugin:
        """Update one Plugin while preserving Agent enablement."""
        return self.install(staged, expected_revision=expected_revision, confirmed=confirmed)

    def get(self, plugin_id: str) -> VersionedPlugin:
        """Read one installed Plugin by stable identity."""
        result = self._read_optional(plugin_id)
        if result is None:
            raise ExtensionError("extension_not_found", f"Plugin '{plugin_id}' was not found.")
        return result

    def list(self) -> tuple[VersionedPlugin, ...]:
        """Return every installed Plugin in deterministic order."""
        if not self.records_dir.exists():
            return ()
        return tuple(
            self._read_record(path)
            for path in sorted(self.records_dir.glob("*.json"), key=lambda item: item.name)
        )

    def readiness(self, plugin_id: str) -> PluginReadiness:
        """Return current component readiness without executing content."""
        current = self.get(plugin_id)
        _manifest, bundle = self._load_installed_bundle(current)
        return self._readiness_for(
            bundle,
            plugin_id=plugin_id,
            plugin_digest=current.record.spec.digest,
        )

    def hook_status(self, plugin_id: str) -> PluginHookStatus:
        """Return exact-definition Hook trust and runtime support diagnostics."""
        current = self.get(plugin_id)
        _manifest, bundle = self._load_installed_bundle(current)
        hooks = bundle.hooks
        trusted = bool(hooks.handler_count) and self.hook_trust.is_trusted(
            plugin_id,
            current.record.spec.digest,
            hooks.digest,
        )
        executable = hooks.executable_count
        supported = tuple(event for event in hooks.event_names if event in SUPPORTED_HOOK_EVENTS)
        return PluginHookStatus(
            plugin_id=plugin_id,
            plugin_revision=current.revision,
            plugin_digest=current.record.spec.digest,
            hook_digest=hooks.digest,
            trusted=trusted,
            declared_events=hooks.event_names,
            supported_events=supported,
            handler_count=hooks.handler_count,
            executable_count=executable,
            unsupported_handlers=hooks.handler_count - executable,
            handlers=tuple(
                {
                    "event": event,
                    "matcher": group.matcher,
                    "type": handler.type,
                    "command": handler.command,
                    "timeout": handler.timeout,
                    "async": handler.asynchronous,
                    "supported": event in SUPPORTED_HOOK_EVENTS and handler.executable(),
                }
                for event, groups in hooks.events
                for group in groups
                for handler in group.hooks
            ),
        )

    def trust_hooks(self, plugin_id: str, *, expected_revision: str) -> PluginHookStatus:
        """Trust only the exact currently installed executable Hook definitions."""
        current = self.get(plugin_id)
        self._require_revision(current, expected_revision)
        _manifest, bundle = self._load_installed_bundle(current)
        if not bundle.hooks.handler_count:
            raise ExtensionError("invalid_operation", "Plugin does not declare Hooks.")
        if not bundle.hooks.executable_count:
            raise ExtensionError("invalid_operation", "Plugin has no supported synchronous command Hooks.")
        self.hook_trust.trust(plugin_id, current.record.spec.digest, bundle.hooks.digest)
        return self.hook_status(plugin_id)

    def untrust_hooks(self, plugin_id: str, *, expected_revision: str) -> PluginHookStatus:
        """Revoke local Hook execution trust without changing Plugin installation."""
        current = self.get(plugin_id)
        self._require_revision(current, expected_revision)
        self.hook_trust.untrust(plugin_id)
        return self.hook_status(plugin_id)

    def enable(
        self,
        plugin_id: str,
        agent_id: str,
        *,
        expected_revision: str,
        confirmed: bool = False,
    ) -> VersionedPlugin:
        """Enable one ready Plugin for an Agent."""
        _validate_resource_name(agent_id)
        current = self.get(plugin_id)
        self._require_revision(current, expected_revision)
        if agent_id in current.record.spec.enabled_agent_ids:
            return current
        _manifest, bundle = self._load_installed_bundle(current)
        readiness = self._readiness_for(
            bundle,
            plugin_id=plugin_id,
            plugin_digest=current.record.spec.digest,
        )
        if not readiness.ready:
            raise ExtensionError(
                "dependency_missing",
                "Plugin dependencies are not ready.",
                details={"issues": list(readiness.issues)},
            )
        if current.record.spec.risk == "high" and not confirmed:
            raise ExtensionError("confirmation_required", "High-risk Plugin enablement requires confirmation.")
        self._require_prefixes_available(plugin_id, bundle, agent_id)
        enabled = sorted((*current.record.spec.enabled_agent_ids, agent_id))
        return self._replace_enablement(current, enabled, expected_revision=expected_revision)

    def disable(self, plugin_id: str, agent_id: str, *, expected_revision: str) -> VersionedPlugin:
        """Disable one Plugin projection for an Agent."""
        _validate_resource_name(agent_id)
        current = self.get(plugin_id)
        enabled = [item for item in current.record.spec.enabled_agent_ids if item != agent_id]
        return self._replace_enablement(current, enabled, expected_revision=expected_revision)

    def remove(self, plugin_id: str, *, expected_revision: str) -> None:
        """Remove one disabled Plugin while retaining immutable snapshot content."""
        current = self.get(plugin_id)
        self._require_revision(current, expected_revision)
        if current.record.spec.enabled_agent_ids:
            raise ExtensionError(
                "extension_in_use",
                "Plugin must be disabled for every Agent before removal.",
                details={"agentIds": list(current.record.spec.enabled_agent_ids)},
            )
        path = self._record_path(plugin_id)
        lock = FileLock(path.with_name(f"{path.name}.lock"), timeout=self.lock_timeout, mode=0o600)
        try:
            with lock:
                fresh = self.get(plugin_id)
                self._require_revision(fresh, expected_revision)
                path.unlink()
                _fsync_directory(path.parent)
                self.hook_trust.untrust(plugin_id)
        except Timeout as exc:
            raise ExtensionError("registry_busy", "Plugin registry is busy; retry with a fresh revision.") from exc
        except OSError as exc:
            raise ExtensionError("write_failed", "Plugin record could not be removed.") from exc

    def is_enabled(self, plugin_id: str, agent_id: str) -> bool:
        """Return whether one installed Plugin currently projects to an Agent."""
        try:
            return agent_id in self.get(plugin_id).record.spec.enabled_agent_ids
        except ExtensionError as exc:
            if exc.code == "extension_not_found":
                return False
            raise

    def snapshot_for_agent(self, agent_id: str) -> PluginSnapshot:
        """Capture enabled standard Plugin resources for a newly assembled Runtime."""
        _validate_resource_name(agent_id)
        entries: list[PluginSnapshotEntry] = []
        skills: list[SkillSnapshotEntry] = []
        mcp_entries: list[McpSnapshotEntry] = []
        hook_entries: list[PluginHookSnapshotEntry] = []
        for plugin in self.list():
            if agent_id not in plugin.record.spec.enabled_agent_ids:
                continue
            _manifest, bundle = self._load_installed_bundle(plugin)
            entries.append(
                PluginSnapshotEntry(
                    record=plugin.record.model_copy(deep=True),
                    revision=plugin.revision,
                    content_root=plugin.content_root,
                )
            )
            for item in bundle.skills:
                skills.append(
                    SkillSnapshotEntry(
                        name=item.ref.name,
                        description=item.manifest.description,
                        source=f"plugin:{plugin.record.metadata.name}",
                        digest=plugin.record.spec.digest,
                        content_root=self._resource_path(plugin.content_root, item.ref.path),
                    )
                )
            for item in bundle.mcp_servers:
                server = item.server.model_copy(
                    deep=True,
                    update={
                        "spec": item.server.spec.model_copy(
                            deep=True,
                            update={
                                "enabled_agent_ids": [agent_id],
                                "managed_by": McpOwnerRef(kind="plugin", name=plugin.record.metadata.name),
                            },
                        )
                    },
                )
                mcp_entries.append(McpSnapshotEntry(server, config_revision(server)))
            if bundle.hooks.handler_count and self.hook_trust.is_trusted(
                plugin.record.metadata.name,
                plugin.record.spec.digest,
                bundle.hooks.digest,
            ):
                hook_entries.append(
                    PluginHookSnapshotEntry(
                        plugin_id=plugin.record.metadata.name,
                        plugin_digest=plugin.record.spec.digest,
                        content_root=plugin.content_root,
                        data_root=self.root / "data" / plugin.record.metadata.name,
                        hooks=bundle.hooks,
                    )
                )
        frozen_entries = tuple(entries)
        canonical = json.dumps(
            [(entry.record.metadata.name, entry.revision) for entry in frozen_entries],
            separators=(",", ":"),
        ).encode("utf-8")
        return PluginSnapshot(
            revision=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
            entries=frozen_entries,
            skills=_skill_snapshot(tuple(skills)),
            mcp=_mcp_snapshot(tuple(mcp_entries)),
            hooks=_hook_snapshot(tuple(hook_entries)),
        )

    def _load_installed_bundle(self, plugin: VersionedPlugin) -> tuple[PluginManifest, PluginBundle]:
        manifest = parse_plugin_manifest(plugin.content_root / _MANIFEST_PATH)
        if manifest.name != plugin.record.metadata.name:
            raise ExtensionError("invalid_registry", "Installed Plugin manifest identity is inconsistent.")
        record_spec = plugin.record.spec
        if (
            _display_name(manifest) != record_spec.display_name
            or manifest.description != record_spec.description
            or manifest.version != record_spec.version
            or _developer_name(manifest) != record_spec.developer
        ):
            raise ExtensionError("invalid_registry", "Installed Plugin metadata is inconsistent.")
        bundle = self._load_bundle(plugin.content_root, manifest)
        if bundle.resources != record_spec.resources:
            raise ExtensionError("invalid_registry", "Installed Plugin component inventory is inconsistent.")
        if bundle.effective_risk != record_spec.risk:
            raise ExtensionError("invalid_registry", "Installed Plugin risk projection is inconsistent.")
        return manifest, bundle

    def _load_bundle(self, content_root: Path, manifest: PluginManifest) -> PluginBundle:
        skills = self._load_skills(content_root, manifest)
        servers = self._load_mcp_servers(content_root, manifest)
        registered_apps = self._load_registered_apps(content_root, manifest)
        hook_paths, inline_hook_count, hooks = self._load_hooks(content_root, manifest)
        self._validate_assets(content_root, manifest)
        risks = [
            "low",
            *(item.manifest.risk for item in skills),
            *(item.server.spec.risk for item in servers),
        ]
        if registered_apps:
            risks.append("medium")
        if hook_paths or inline_hook_count:
            risks.append("high")
        return PluginBundle(
            tuple(skills),
            tuple(servers),
            tuple(registered_apps),
            tuple(hook_paths),
            inline_hook_count,
            hooks,
            max(risks, key=lambda item: _RISK_ORDER[item]),
        )

    def _load_skills(self, content_root: Path, manifest: PluginManifest) -> list[ParsedPluginSkill]:
        if manifest.skills is None:
            return []
        skills_root = self._resource_path(content_root, manifest.skills)
        if not skills_root.is_dir():
            raise ExtensionError("invalid_manifest", "Plugin skills must point to a directory.")
        roots = [skills_root] if (skills_root / "SKILL.md").is_file() else [
            path for path in sorted(skills_root.iterdir(), key=lambda item: item.name) if path.is_dir()
        ]
        values: list[ParsedPluginSkill] = []
        source_names: set[str] = set()
        for root in roots:
            if not (root / "SKILL.md").is_file():
                raise ExtensionError("invalid_manifest", "Every Plugin skill directory must contain SKILL.md.")
            skill = parse_skill_manifest(root / "SKILL.md")
            if skill.name in source_names:
                raise ExtensionError("invalid_manifest", "Plugin Skill names must be unique.")
            source_names.add(skill.name)
            internal_name = _component_id(manifest.name, skill.name)
            values.append(
                ParsedPluginSkill(
                    PluginResourceRef(name=internal_name, path=self._relative_path(content_root, root)),
                    skill,
                )
            )
        return values

    def _load_mcp_servers(self, content_root: Path, manifest: PluginManifest) -> list[ParsedPluginMcp]:
        if manifest.mcp_servers is None:
            return []
        path = self._resource_path(content_root, manifest.mcp_servers)
        raw = self._read_json(path, label="MCP configuration")
        if not isinstance(raw, dict):
            raise ExtensionError("invalid_manifest", "Plugin MCP configuration must be an object.")
        wrapped = raw.get("mcp_servers", raw.get("mcpServers"))
        server_map = wrapped if wrapped is not None else raw
        if not isinstance(server_map, dict):
            raise ExtensionError("invalid_manifest", "Plugin MCP server map must be an object.")
        values: list[ParsedPluginMcp] = []
        for source_name in sorted(server_map):
            config = server_map[source_name]
            if _RESOURCE_NAME_PATTERN.fullmatch(source_name) is None or not isinstance(config, dict):
                raise ExtensionError("invalid_manifest", "Plugin MCP server entry is invalid.")
            unsupported = set(config) - _MCP_CONFIG_KEYS
            if unsupported:
                raise ExtensionError(
                    "invalid_manifest",
                    "Plugin MCP server contains unsupported standard fields.",
                    details={"fields": sorted(unsupported)},
                )
            internal_name = _component_id(manifest.name, source_name)
            server, environment_keys = self._project_mcp_server(
                content_root,
                manifest,
                source_name,
                internal_name,
                config,
            )
            values.append(
                ParsedPluginMcp(
                    PluginResourceRef(name=internal_name, path=manifest.mcp_servers),
                    server,
                    environment_keys,
                )
            )
        return values

    def _project_mcp_server(
        self,
        content_root: Path,
        manifest: PluginManifest,
        source_name: str,
        internal_name: str,
        config: dict[str, Any],
    ) -> tuple[McpServer, tuple[str, ...]]:
        command = config.get("command")
        url = config.get("url")
        if isinstance(command, str) == isinstance(url, str):
            raise ExtensionError("invalid_manifest", "Plugin MCP server must define exactly one of command or url.")
        environment_keys: set[str] = set()
        if isinstance(command, str):
            resolved_command = self._resolve_plugin_command(content_root, command)
            args = self._string_list(config.get("args", []), label="MCP args")
            cwd = self._resolve_plugin_cwd(content_root, config.get("cwd"))
            environment = {
                name: McpLiteralValue(kind="literal", value=value)
                for name, value in self._string_map(config.get("env", {}), label="MCP env").items()
            }
            env_vars = self._string_list(config.get("env_vars", []), label="MCP env_vars")
            for name in env_vars:
                self._require_environment_name(name)
                environment_keys.add(name)
                environment[name] = McpEnvironmentValue(kind="environment", name=name)
            transport = McpStdioTransport(
                type="stdio",
                command=resolved_command,
                args=args,
                cwd=cwd,
                environment=environment,
            )
        else:
            if not isinstance(url, str):
                raise ExtensionError("invalid_manifest", "Plugin MCP URL must be text.")
            headers = {
                name: McpLiteralValue(kind="literal", value=value)
                for name, value in self._string_map(
                    config.get("http_headers", {}),
                    label="MCP http_headers",
                ).items()
            }
            for header, environment_name in self._string_map(
                config.get("env_http_headers", {}),
                label="MCP env_http_headers",
            ).items():
                self._require_environment_name(environment_name)
                environment_keys.add(environment_name)
                headers[header] = McpEnvironmentValue(kind="environment", name=environment_name)
            bearer = config.get("bearer_token_env_var")
            if bearer is not None:
                if not isinstance(bearer, str):
                    raise ExtensionError("invalid_manifest", "MCP bearer_token_env_var must be text.")
                self._require_environment_name(bearer)
                environment_keys.add(bearer)
                headers["Authorization"] = McpEnvironmentValue(
                    kind="environment",
                    name=bearer,
                    prefix="Bearer ",
                )
            transport_name = config.get("transport", "streamable_http")
            if transport_name in {"http", "streamable-http"}:
                transport_name = "streamable_http"
            if transport_name not in {"streamable_http", "sse"}:
                raise ExtensionError("invalid_manifest", "Plugin MCP remote transport is unsupported.")
            transport = McpRemoteTransport(
                type=transport_name,
                url=url,
                headers=headers,
                query={},
                auth="none",
            )
        enabled_tools = self._string_list(config.get("enabled_tools", []), label="MCP enabled_tools")
        disabled_tools = self._string_list(config.get("disabled_tools", []), label="MCP disabled_tools")
        if enabled_tools and disabled_tools:
            raise ExtensionError(
                "invalid_manifest",
                "Plugin MCP enabled_tools and disabled_tools cannot both be configured.",
            )
        server = McpServer(
            api_version="openppx.io/v1alpha1",
            kind="McpServer",
            metadata=ResourceMetadata(name=internal_name),
            spec=McpServerSpec(
                display_name=_title(source_name),
                description=f"Bundled MCP server from {_display_name(manifest)}.",
                transport=transport,
                policy=McpToolPolicy(
                    tool_filter=enabled_tools,
                    disabled_tools=disabled_tools,
                    tool_name_prefix=_tool_prefix(manifest.name, source_name),
                ),
                risk="medium",
                enabled_agent_ids=[],
                managed_by=McpOwnerRef(kind="plugin", name=manifest.name),
            ),
        )
        return server, tuple(sorted(environment_keys))

    def _load_registered_apps(
        self,
        content_root: Path,
        manifest: PluginManifest,
    ) -> list[PluginRegisteredApp]:
        if manifest.apps is None:
            return []
        raw = self._read_json(self._resource_path(content_root, manifest.apps), label="App mapping")
        app_map = raw.get("apps") if isinstance(raw, dict) else None
        if not isinstance(app_map, dict):
            raise ExtensionError("invalid_manifest", "Plugin .app.json must contain an apps object.")
        values: list[PluginRegisteredApp] = []
        for name in sorted(app_map):
            item = app_map[name]
            if _RESOURCE_NAME_PATTERN.fullmatch(name) is None or not isinstance(item, dict):
                raise ExtensionError("invalid_manifest", "Plugin registered App entry is invalid.")
            try:
                values.append(PluginRegisteredApp.model_validate({"name": name, **item}))
            except (ValidationError, ValueError) as exc:
                raise ExtensionError("invalid_manifest", "Plugin registered App entry is invalid.") from exc
        return values

    def _load_hooks(
        self,
        content_root: Path,
        manifest: PluginManifest,
    ) -> tuple[list[str], int, ParsedPluginHooks]:
        declared = manifest.hooks
        default = "./hooks/hooks.json"
        if declared is None and self._resource_path(content_root, default).is_file():
            declared = default
        if declared is None:
            return [], 0, ParsedPluginHooks.empty()
        values = declared if isinstance(declared, list) else [declared]
        paths: list[str] = []
        inline_count = 0
        documents: list[dict[str, Any]] = []
        for value in values:
            if isinstance(value, str):
                path = self._resource_path(content_root, value)
                raw = self._read_json(path, label="hook definition")
                if not isinstance(raw, dict):
                    raise ExtensionError("invalid_manifest", "Plugin hook definition must be an object.")
                paths.append(value)
                documents.append(raw)
            elif isinstance(value, dict):
                inline_count += 1
                documents.append(value)
            else:
                raise ExtensionError("invalid_manifest", "Plugin hooks contain an unsupported value.")
        return paths, inline_count, parse_plugin_hooks(documents)

    def _validate_assets(self, content_root: Path, manifest: PluginManifest) -> None:
        interface = manifest.interface
        if interface is None:
            return
        paths = [
            item
            for item in (interface.composer_icon, interface.logo, interface.logo_dark, *interface.screenshots)
            if item is not None
        ]
        for relative in paths:
            if not self._resource_path(content_root, relative).is_file():
                raise ExtensionError("invalid_manifest", "Plugin interface asset is unavailable.")

    def _readiness_for(
        self,
        bundle: PluginBundle,
        *,
        plugin_id: str | None = None,
        plugin_digest: str | None = None,
    ) -> PluginReadiness:
        issues: list[str] = []
        for item in bundle.skills:
            for executable in item.manifest.dependencies.executables:
                if self.executable_resolver(executable) is None:
                    issues.append("skill_executable_missing")
            for key in item.manifest.dependencies.environment:
                if key not in self.available_environment_keys and key not in os.environ:
                    issues.append("skill_environment_missing")
        for item in bundle.mcp_servers:
            transport = item.server.spec.transport
            if isinstance(transport, McpStdioTransport) and self.executable_resolver(transport.command) is None:
                issues.append("mcp_executable_missing")
            for key in item.environment_keys:
                if key not in self.available_environment_keys and key not in os.environ:
                    issues.append("mcp_environment_missing")
        for app in bundle.registered_apps:
            if app.required and not any(
                resolver(app) for resolver in self._registered_app_resolvers.values()
            ):
                issues.append("registered_app_unavailable")
        if bundle.hooks.handler_count:
            if not bundle.hooks.executable_count:
                issues.append("plugin_hooks_no_supported_handlers")
            elif (
                plugin_id is None
                or plugin_digest is None
                or not self.hook_trust.is_trusted(plugin_id, plugin_digest, bundle.hooks.digest)
            ):
                issues.append("plugin_hooks_untrusted")
        return PluginReadiness(ready=not issues, issues=tuple(sorted(set(issues))))

    def _require_identities_available(self, plugin_id: str, bundle: PluginBundle) -> None:
        if self.identity_index is None:
            return
        owner_key = f"plugin:{plugin_id}"
        for kind, values in (("skill", bundle.skills), ("mcp", bundle.mcp_servers)):
            for item in values:
                self.identity_index.require_available(kind, item.ref.name, owner_key=owner_key)

    def _require_prefixes_available(self, plugin_id: str, bundle: PluginBundle, agent_id: str) -> None:
        if self.prefix_index is None:
            return
        for item in bundle.mcp_servers:
            self.prefix_index.require_available(
                item.server.spec.policy.resolved_prefix(item.server.metadata.name),
                agent_id,
                owner_key=f"plugin:{plugin_id}",
            )

    def _identity_reservations(self) -> tuple[ResourceIdentityReservation, ...]:
        """Project every installed Plugin-owned runtime identity."""
        reservations: list[ResourceIdentityReservation] = []
        for plugin in self.list():
            owner = f"plugin:{plugin.record.metadata.name}"
            for kind, values in (
                ("skill", plugin.record.spec.resources.skills),
                ("mcp", plugin.record.spec.resources.mcp_servers),
            ):
                reservations.extend(ResourceIdentityReservation(kind, item.name, owner) for item in values)
        return tuple(reservations)

    def _prefix_reservations(self, agent_id: str) -> tuple[ToolPrefixReservation, ...]:
        """Project MCP prefixes from Plugins enabled for one Agent."""
        reservations: list[ToolPrefixReservation] = []
        for plugin in self.list():
            if agent_id not in plugin.record.spec.enabled_agent_ids:
                continue
            _manifest, bundle = self._load_installed_bundle(plugin)
            reservations.extend(
                ToolPrefixReservation(
                    item.server.spec.policy.resolved_prefix(item.server.metadata.name),
                    f"plugin:{plugin.record.metadata.name}",
                )
                for item in bundle.mcp_servers
            )
        return tuple(reservations)

    def _replace_enablement(
        self,
        current: VersionedPlugin,
        enabled_agent_ids: list[str],
        *,
        expected_revision: str,
    ) -> VersionedPlugin:
        self._require_revision(current, expected_revision)
        updated = current.record.model_copy(
            update={
                "spec": current.record.spec.model_copy(
                    update={"enabled_agent_ids": enabled_agent_ids}
                )
            }
        )
        self._write_record(updated, expected_revision=expected_revision)
        return self.get(current.record.metadata.name)

    def _write_record(self, record: PluginRecord, *, expected_revision: str | None) -> None:
        path = self._record_path(record.metadata.name)
        try:
            atomic_write_resource(
                path,
                record,
                source=f"plugin:{record.metadata.name}",
                expected_revision=expected_revision,
                current_revision=lambda: (
                    current.revision
                    if (current := self._read_optional(record.metadata.name)) is not None
                    else None
                ),
                lock_timeout=self.lock_timeout,
            )
        except ConfigRevisionConflict as exc:
            raise ExtensionError(
                "revision_conflict",
                "Plugin revision does not match current state.",
                details={
                    "expectedRevision": exc.expected_revision,
                    "actualRevision": exc.actual_revision,
                },
            ) from exc
        except ConfigWriteError as exc:
            raise ExtensionError(exc.kind, "Plugin record could not be written.") from exc

    def _read_optional(self, plugin_id: str) -> VersionedPlugin | None:
        path = self._record_path(plugin_id)
        if not path.exists():
            return None
        return self._read_record(path)

    def _read_record(self, path: Path) -> VersionedPlugin:
        try:
            raw = read_json_object(path, source=f"plugin:{path.stem}")
            record = PluginRecord.model_validate(raw)
        except (ValidationError, ValueError) as exc:
            raise ExtensionError("invalid_registry", "Installed Plugin record is invalid.") from exc
        if record.metadata.name != path.stem:
            raise ExtensionError("invalid_registry", "Installed Plugin identity does not match its record path.")
        content_root = self._content_path(record.metadata.name, record.spec.digest)
        if not content_root.joinpath(*PurePosixPath(_MANIFEST_PATH).parts).is_file():
            raise ExtensionError("extension_unavailable", "Installed Plugin content is unavailable.")
        return VersionedPlugin(record, config_revision(record), content_root)

    def _record_path(self, plugin_id: str) -> Path:
        _validate_resource_name(plugin_id)
        path = (self.records_dir / f"{plugin_id}.json").resolve(strict=False)
        if not path.is_relative_to(self.root.resolve(strict=False)):
            raise ExtensionError("unsafe_path", "Plugin record path is outside the Node root.")
        return path

    def _content_path(self, plugin_id: str, digest: str) -> Path:
        _validate_resource_name(plugin_id)
        digest_id = digest.removeprefix("sha256:")
        if re.fullmatch(r"[0-9a-f]{64}", digest_id) is None:
            raise ExtensionError("invalid_registry", "Plugin content digest is invalid.")
        path = (self.content_dir / plugin_id / digest_id).resolve(strict=False)
        if not path.is_relative_to(self.root.resolve(strict=False)):
            raise ExtensionError("unsafe_path", "Plugin content path is outside the Node root.")
        return path

    @staticmethod
    def _resource_path(content_root: Path, relative: str) -> Path:
        normalized = relative.removeprefix("./")
        path = content_root.joinpath(*PurePosixPath(normalized).parts).resolve(strict=False)
        if not path.is_relative_to(content_root.resolve(strict=True)):
            raise ExtensionError("unsafe_path", "Plugin resource path is outside installed content.")
        return path

    @staticmethod
    def _relative_path(content_root: Path, path: Path) -> str:
        relative = path.resolve(strict=True).relative_to(content_root.resolve(strict=True))
        return f"./{relative.as_posix()}"

    @staticmethod
    def _read_json(path: Path, *, label: str) -> Any:
        if not path.is_file():
            raise ExtensionError("invalid_manifest", f"Plugin {label} path is not a file.")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ExtensionError("invalid_manifest", f"Plugin {label} is invalid.") from exc

    @staticmethod
    def _string_list(value: object, *, label: str) -> list[str]:
        if not isinstance(value, list) or len(value) > 256 or any(not isinstance(item, str) for item in value):
            raise ExtensionError("invalid_manifest", f"Plugin {label} must be a bounded string array.")
        return list(value)

    @staticmethod
    def _string_map(value: object, *, label: str) -> dict[str, str]:
        if (
            not isinstance(value, dict)
            or len(value) > 128
            or any(not isinstance(key, str) or not isinstance(item, str) for key, item in value.items())
        ):
            raise ExtensionError("invalid_manifest", f"Plugin {label} must be a bounded string map.")
        return dict(value)

    @staticmethod
    def _require_environment_name(value: str) -> None:
        if _ENVIRONMENT_NAME_PATTERN.fullmatch(value) is None:
            raise ExtensionError("invalid_manifest", "Plugin MCP environment name is invalid.")

    def _resolve_plugin_command(self, content_root: Path, command: str) -> str:
        if command.startswith("./"):
            path = self._resource_path(content_root, command)
            if not path.is_file():
                raise ExtensionError("invalid_manifest", "Plugin MCP executable is unavailable.")
            return str(path)
        absolute = Path(command)
        if absolute.is_absolute():
            if not absolute.is_file():
                raise ExtensionError("invalid_manifest", "Plugin MCP executable is unavailable.")
            return str(absolute)
        if "/" in command or "\\" in command:
            raise ExtensionError("invalid_manifest", "Plugin MCP command must be an executable name or './' path.")
        return command

    def _resolve_plugin_cwd(self, content_root: Path, value: object) -> str | None:
        if value is None:
            return str(content_root)
        if not isinstance(value, str):
            raise ExtensionError("invalid_manifest", "Plugin MCP cwd must be text.")
        if value == ".":
            return str(content_root)
        relative = value if value.startswith("./") else f"./{value}"
        path = self._resource_path(content_root, relative)
        if not path.is_dir():
            raise ExtensionError("invalid_manifest", "Plugin MCP cwd is not a directory.")
        return str(path)

    @staticmethod
    def _require_revision(current: VersionedPlugin, expected_revision: str | None) -> None:
        if current.revision != expected_revision:
            raise ExtensionError(
                "revision_conflict",
                "Plugin revision does not match current state.",
                details={
                    "expectedRevision": expected_revision,
                    "actualRevision": current.revision,
                },
            )

    @staticmethod
    def _activate_content(source: Path, target: Path) -> None:
        if target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
        try:
            shutil.copytree(source, temporary, symlinks=False)
            os.replace(temporary, target)
            _fsync_directory(target.parent)
        except OSError as exc:
            raise ExtensionError("write_failed", "Plugin content could not be activated.") from exc
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def parse_plugin_manifest(path: Path) -> PluginManifest:
    """Parse one strict `.agent-plugin` manifest using Codex field names."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return PluginManifest.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ExtensionError("invalid_manifest", "Plugin manifest does not match the Codex schema.") from exc


def _display_name(manifest: PluginManifest) -> str:
    interface = manifest.interface
    return interface.display_name if interface and interface.display_name else _title(manifest.name)


def _developer_name(manifest: PluginManifest) -> str:
    if manifest.author is not None:
        return manifest.author.name
    if manifest.interface is not None and manifest.interface.developer_name:
        return manifest.interface.developer_name
    return "Unknown developer"


def _title(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", "-").split("-") if part)


def _component_id(plugin_id: str, component_name: str) -> str:
    """Build a deterministic internal identity without modifying the package."""
    _validate_resource_name(plugin_id)
    _validate_resource_name(component_name)
    candidate = f"{plugin_id}--{component_name}"
    if len(candidate) <= 63:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:10]
    prefix = candidate[: 63 - len(digest) - 1].rstrip("-")
    return f"{prefix}-{digest}"


def _tool_prefix(plugin_id: str, component_name: str) -> str:
    """Build one bounded ADK-safe MCP prefix from standard component names."""
    candidate = f"{plugin_id.replace('-', '_')}_{component_name.replace('-', '_')}"
    if len(candidate) <= 128:
        return candidate
    digest = hashlib.sha256(candidate.encode("utf-8")).hexdigest()[:10]
    return f"{candidate[:117].rstrip('_')}_{digest}"


def _trust_for_source(source_type: str) -> str:
    if source_type == "builtin":
        return "builtin"
    if source_type in {"local_directory", "local_archive"}:
        return "local"
    return "third_party"


def _validate_resource_name(value: str) -> None:
    if _RESOURCE_NAME_PATTERN.fullmatch(value) is None:
        raise ExtensionError("invalid_identity", "Plugin, component, or Agent identity is invalid.")


def _skill_snapshot(skills: tuple[SkillSnapshotEntry, ...]) -> SkillSnapshot:
    ordered = tuple(sorted(skills, key=lambda item: item.name))
    names = [item.name for item in ordered]
    if len(names) != len(set(names)):
        raise ExtensionError("extension_conflict", "Plugin Skill projection contains duplicate identities.")
    canonical = json.dumps(
        [(item.name, item.digest) for item in ordered],
        separators=(",", ":"),
    ).encode("utf-8")
    return SkillSnapshot(f"sha256:{hashlib.sha256(canonical).hexdigest()}", ordered)


def _mcp_snapshot(entries: tuple[McpSnapshotEntry, ...]) -> McpSnapshot:
    canonical = json.dumps(
        [(entry.record.metadata.name, entry.revision) for entry in entries],
        separators=(",", ":"),
    ).encode("utf-8")
    snapshot = McpSnapshot(f"sha256:{hashlib.sha256(canonical).hexdigest()}", entries)
    return merge_mcp_snapshots(McpSnapshot.empty(), snapshot)


def _hook_snapshot(entries: tuple[PluginHookSnapshotEntry, ...]) -> PluginHookSnapshot:
    ordered = tuple(sorted(entries, key=lambda item: item.plugin_id))
    canonical = json.dumps(
        [(entry.plugin_id, entry.plugin_digest, entry.hooks.digest) for entry in ordered],
        separators=(",", ":"),
    ).encode("utf-8")
    return PluginHookSnapshot(f"sha256:{hashlib.sha256(canonical).hexdigest()}", ordered)


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
    "PluginManager",
    "PluginPreview",
    "PluginReadiness",
    "PluginSnapshot",
    "PluginSnapshotEntry",
    "StagedPlugin",
    "VersionedPlugin",
    "parse_plugin_manifest",
]
