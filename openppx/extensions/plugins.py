"""Declarative Product Plugin staging, lifecycle, and immutable projection."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Callable, Mapping

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

from .app_models import AppDefinition, AppOwnerRef
from .apps import VersionedAppDefinition
from .errors import ExtensionError
from .indexes import (
    ExtensionReferenceIndex,
    ResourceIdentityIndex,
    ResourceIdentityReservation,
)
from .mcp import McpSnapshot, McpSnapshotEntry, merge_mcp_snapshots
from .mcp_models import McpOwnerRef, McpSecretValue, McpServer, McpStdioTransport
from .models import ExtensionSourceRef, SkillManifest
from .plugin_models import (
    PluginAgentTemplate,
    PluginConfigSchema,
    PluginManifest,
    PluginRecord,
    PluginRecordSpec,
    PluginResourceRef,
)
from .prefixes import ToolPrefixIndex, ToolPrefixReservation
from .skills import SkillSnapshot, SkillSnapshotEntry, parse_skill_manifest
from .sources import (
    BuiltinSourceAdapter,
    CatalogSourceAdapter,
    GitSourceAdapter,
    LocalArchiveSourceAdapter,
    LocalDirectorySourceAdapter,
    SourceAdapter,
    SourceLimits,
    StagedExtension,
    StagingStore,
)


_MANIFEST_PATH = ".openppx-plugin/plugin.json"
_RESOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_RISK_ORDER = {"low": 0, "medium": 1, "high": 2}


@dataclass(frozen=True, slots=True)
class ParsedPluginSkill:
    """Validated Skill declaration inside staged or installed Plugin content."""

    ref: PluginResourceRef
    manifest: SkillManifest


@dataclass(frozen=True, slots=True)
class ParsedPluginApp:
    """Validated App definition declaration inside Plugin content."""

    ref: PluginResourceRef
    definition: AppDefinition


@dataclass(frozen=True, slots=True)
class ParsedPluginMcp:
    """Validated direct MCP declaration inside Plugin content."""

    ref: PluginResourceRef
    server: McpServer


@dataclass(frozen=True, slots=True)
class PluginBundle:
    """Fully parsed, non-executable Plugin resource bundle."""

    skills: tuple[ParsedPluginSkill, ...]
    apps: tuple[ParsedPluginApp, ...]
    mcp_servers: tuple[ParsedPluginMcp, ...]
    effective_risk: str


@dataclass(frozen=True, slots=True)
class StagedPlugin:
    """Pinned source content plus its validated Plugin manifest and bundle."""

    extension: StagedExtension
    manifest: PluginManifest
    bundle: PluginBundle


@dataclass(frozen=True, slots=True)
class PluginPreview:
    """Client-safe Plugin preview produced before any installed state changes."""

    plugin_id: str
    display_name: str
    description: str
    version: str
    digest: str
    source_type: str
    trust: str
    risk: str
    runtime_capabilities: tuple[str, ...]
    resource_counts: dict[str, int]


@dataclass(frozen=True, slots=True)
class PluginReadiness:
    """Non-sensitive enablement readiness for one Product Plugin."""

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
    """Agent-specific Product Plugin, Skill, MCP, and App projections."""

    revision: str
    entries: tuple[PluginSnapshotEntry, ...]
    skills: SkillSnapshot
    mcp: McpSnapshot
    app_definitions: tuple[VersionedAppDefinition, ...]

    @property
    def plugin_ids(self) -> tuple[str, ...]:
        """Return deterministic enabled Plugin identities."""
        return tuple(entry.record.metadata.name for entry in self.entries)

    @classmethod
    def empty(cls) -> "PluginSnapshot":
        """Return a stable empty Product Plugin snapshot."""
        digest = hashlib.sha256(b"[]").hexdigest()
        return cls(
            revision=f"sha256:{digest}",
            entries=(),
            skills=SkillSnapshot.empty(),
            mcp=McpSnapshot.empty(),
            app_definitions=(),
        )


AppDefinitionValidator = Callable[[str, tuple[AppDefinition, ...]], None]


class PluginManager:
    """Own Product Plugin staging, persistence, enablement, and projections."""

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
        allowed_runtime_capabilities: frozenset[str] = frozenset(),
        identity_index: ResourceIdentityIndex | None = None,
        reference_index: ExtensionReferenceIndex | None = None,
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
        self.allowed_runtime_capabilities = allowed_runtime_capabilities
        self.identity_index = identity_index
        self.reference_index = reference_index
        self.prefix_index = prefix_index
        self.lock_timeout = lock_timeout
        builtin_roots = {
            identifier: path.expanduser().resolve(strict=False)
            for identifier, path in (builtin_plugins or {}).items()
        }
        self._adapters: dict[str, SourceAdapter] = {
            "builtin": BuiltinSourceAdapter(builtin_roots),
            "local_directory": LocalDirectorySourceAdapter(),
            "local_archive": LocalArchiveSourceAdapter(),
            "git": GitSourceAdapter(),
        }
        self._catalog_adapters = dict(catalog_adapters or {})
        self._app_definition_validators: dict[str, AppDefinitionValidator] = {}
        if identity_index is not None:
            identity_index.register("plugins", self._identity_reservations)
        if prefix_index is not None:
            prefix_index.register("plugins", self._prefix_reservations)

    def register_app_definition_validator(
        self,
        provider_id: str,
        validator: AppDefinitionValidator,
    ) -> None:
        """Register cross-domain connection compatibility validation."""
        if provider_id in self._app_definition_validators:
            raise ValueError(f"Plugin App validator '{provider_id}' is already registered.")
        self._app_definition_validators[provider_id] = validator

    def stage(self, reference: ExtensionSourceRef) -> StagedPlugin:
        """Stage and fully validate one declarative Plugin without installing it."""
        if reference.type == "catalog":
            adapter = self._catalog_adapters.get(reference.provider or "")
        else:
            adapter = self._adapters.get(reference.type)
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
        resources = staged.manifest.spec.resources
        return PluginPreview(
            plugin_id=staged.manifest.metadata.name,
            display_name=staged.manifest.spec.display_name,
            description=staged.manifest.spec.description,
            version=staged.manifest.spec.version,
            digest=staged.extension.digest,
            source_type=staged.extension.source.type,
            trust=_trust_for_source(staged.extension.source.type),
            risk=staged.bundle.effective_risk,
            runtime_capabilities=tuple(staged.manifest.spec.runtime_capabilities),
            resource_counts={
                "appDefinitions": len(resources.app_definitions),
                "agentTemplates": len(resources.agent_templates),
                "configSchemas": len(resources.config_schemas),
                "documentation": len(resources.documentation),
                "mcpServers": len(resources.mcp_servers),
                "skills": len(resources.skills),
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
        plugin_id = staged.manifest.metadata.name
        _validate_resource_name(plugin_id)
        previous = self._read_optional(plugin_id)
        try:
            if previous is None:
                if expected_revision is not None:
                    raise ExtensionError("revision_conflict", "New Plugins require an empty revision precondition.")
                enabled_agents: list[str] = []
            else:
                if expected_revision != previous.revision:
                    raise ExtensionError(
                        "revision_conflict",
                        "Plugin revision does not match current state.",
                        details={
                            "expectedRevision": expected_revision,
                            "actualRevision": previous.revision,
                        },
                    )
                enabled_agents = list(previous.record.spec.enabled_agent_ids)
            self._require_identities_available(plugin_id, staged.manifest)
            definitions = tuple(
                self._project_app_definition_from_source(
                    staged.extension.source,
                    staged.manifest.spec.version,
                    plugin_id,
                    item.definition,
                )
                for item in staged.bundle.apps
            )
            for provider_id in sorted(self._app_definition_validators):
                self._app_definition_validators[provider_id](plugin_id, definitions)
            if previous is not None and self.reference_index is not None:
                old_apps = {item.name for item in previous.record.spec.resources.app_definitions}
                new_apps = {item.name for item in staged.manifest.spec.resources.app_definitions}
                if old_apps != new_apps and self.reference_index.references(f"plugin:{plugin_id}"):
                    raise ExtensionError(
                        "extension_in_use",
                        "Plugin App identities cannot change while connections reference the Plugin.",
                    )
            if enabled_agents:
                readiness = self._readiness_for(staged.manifest, staged.bundle)
                if not readiness.ready:
                    raise ExtensionError(
                        "dependency_missing",
                        "Updated Plugin dependencies are not ready.",
                        details={"issues": list(readiness.issues)},
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
                    developer=staged.manifest.spec.developer,
                    digest=preview.digest,
                    source=staged.extension.source,
                    trust=preview.trust,
                    risk=preview.risk,
                    runtime_capabilities=list(preview.runtime_capabilities),
                    resources=staged.manifest.spec.resources,
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
        return self.install(
            staged,
            expected_revision=expected_revision,
            confirmed=confirmed,
        )

    def get(self, plugin_id: str) -> VersionedPlugin:
        """Read one installed Product Plugin by stable identity."""
        result = self._read_optional(plugin_id)
        if result is None:
            raise ExtensionError("extension_not_found", f"Plugin '{plugin_id}' was not found.")
        return result

    def list(self) -> tuple[VersionedPlugin, ...]:
        """Return every installed Product Plugin in deterministic order."""
        if not self.records_dir.exists():
            return ()
        return tuple(
            self._read_record(path)
            for path in sorted(self.records_dir.glob("*.json"), key=lambda item: item.name)
        )

    def readiness(self, plugin_id: str) -> PluginReadiness:
        """Return current dependency/capability readiness without executing content."""
        current = self.get(plugin_id)
        manifest, bundle = self._load_installed_bundle(current)
        return self._readiness_for(manifest, bundle)

    def enable(
        self,
        plugin_id: str,
        agent_id: str,
        *,
        expected_revision: str,
        confirmed: bool = False,
    ) -> VersionedPlugin:
        """Enable one ready declarative Plugin for an Agent."""
        _validate_resource_name(agent_id)
        current = self.get(plugin_id)
        self._require_revision(current, expected_revision)
        if agent_id in current.record.spec.enabled_agent_ids:
            return current
        manifest, bundle = self._load_installed_bundle(current)
        readiness = self._readiness_for(manifest, bundle)
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

    def disable(
        self,
        plugin_id: str,
        agent_id: str,
        *,
        expected_revision: str,
    ) -> VersionedPlugin:
        """Disable one Plugin projection for an Agent."""
        _validate_resource_name(agent_id)
        current = self.get(plugin_id)
        enabled = [item for item in current.record.spec.enabled_agent_ids if item != agent_id]
        return self._replace_enablement(current, enabled, expected_revision=expected_revision)

    def remove(self, plugin_id: str, *, expected_revision: str) -> None:
        """Remove one disabled, unreferenced Plugin while retaining snapshot content."""
        current = self.get(plugin_id)
        self._require_revision(current, expected_revision)
        if current.record.spec.enabled_agent_ids:
            raise ExtensionError(
                "extension_in_use",
                "Plugin must be disabled for every Agent before removal.",
                details={"agentIds": list(current.record.spec.enabled_agent_ids)},
            )
        if self.reference_index is not None:
            self.reference_index.require_unreferenced(f"plugin:{plugin_id}")
        path = self._record_path(plugin_id)
        lock = FileLock(path.with_name(f"{path.name}.lock"), timeout=self.lock_timeout, mode=0o600)
        try:
            with lock:
                fresh = self.get(plugin_id)
                self._require_revision(fresh, expected_revision)
                path.unlink()
                _fsync_directory(path.parent)
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

    def app_definitions(self) -> tuple[VersionedAppDefinition, ...]:
        """Project all installed Plugin-owned App definitions for connection management."""
        definitions: list[VersionedAppDefinition] = []
        for plugin in self.list():
            _manifest, bundle = self._load_installed_bundle(plugin)
            for item in bundle.apps:
                record = self._project_app_definition(plugin, item.definition)
                definitions.append(
                    VersionedAppDefinition(record=record, revision=config_revision(record))
                )
        return tuple(sorted(definitions, key=lambda item: item.record.metadata.name))

    def snapshot_for_agent(self, agent_id: str) -> PluginSnapshot:
        """Capture enabled Plugin resources for one newly assembled Runtime."""
        _validate_resource_name(agent_id)
        entries: list[PluginSnapshotEntry] = []
        skills: list[SkillSnapshotEntry] = []
        mcp_entries: list[McpSnapshotEntry] = []
        definitions: list[VersionedAppDefinition] = []
        for plugin in self.list():
            if agent_id not in plugin.record.spec.enabled_agent_ids:
                continue
            manifest, bundle = self._load_installed_bundle(plugin)
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
                                "managed_by": McpOwnerRef(
                                    kind="plugin",
                                    name=plugin.record.metadata.name,
                                ),
                            },
                        )
                    },
                )
                mcp_entries.append(McpSnapshotEntry(server, config_revision(server)))
            for item in bundle.apps:
                definition = self._project_app_definition(plugin, item.definition)
                definitions.append(
                    VersionedAppDefinition(definition, config_revision(definition))
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
            app_definitions=tuple(definitions),
        )

    def _load_installed_bundle(
        self,
        plugin: VersionedPlugin,
    ) -> tuple[PluginManifest, PluginBundle]:
        manifest = parse_plugin_manifest(plugin.content_root / _MANIFEST_PATH)
        if manifest.metadata.name != plugin.record.metadata.name:
            raise ExtensionError("invalid_registry", "Installed Plugin manifest identity is inconsistent.")
        record_spec = plugin.record.spec
        manifest_spec = manifest.spec
        if (
            manifest_spec.display_name != record_spec.display_name
            or manifest_spec.description != record_spec.description
            or manifest_spec.version != record_spec.version
            or manifest_spec.developer != record_spec.developer
            or manifest_spec.runtime_capabilities != record_spec.runtime_capabilities
        ):
            raise ExtensionError("invalid_registry", "Installed Plugin metadata is inconsistent.")
        if manifest_spec.resources != record_spec.resources:
            raise ExtensionError("invalid_registry", "Installed Plugin resource inventory is inconsistent.")
        bundle = self._load_bundle(plugin.content_root, manifest)
        if bundle.effective_risk != record_spec.risk:
            raise ExtensionError("invalid_registry", "Installed Plugin risk projection is inconsistent.")
        return manifest, bundle

    def _load_bundle(self, content_root: Path, manifest: PluginManifest) -> PluginBundle:
        plugin_id = manifest.metadata.name
        resources = manifest.spec.resources
        skills: list[ParsedPluginSkill] = []
        apps: list[ParsedPluginApp] = []
        servers: list[ParsedPluginMcp] = []
        risks = [manifest.spec.risk]
        for ref in resources.skills:
            self._require_namespaced(plugin_id, ref.name)
            root = self._resource_path(content_root, ref.path)
            skill = parse_skill_manifest(root / "SKILL.md")
            if skill.name != ref.name:
                raise ExtensionError("invalid_manifest", "Plugin Skill identity does not match its declaration.")
            skills.append(ParsedPluginSkill(ref, skill))
            risks.append(skill.risk)
        for ref in resources.app_definitions:
            self._require_namespaced(plugin_id, ref.name)
            definition = self._read_model(
                self._resource_path(content_root, ref.path),
                AppDefinition,
                label="App definition",
            )
            if definition.metadata.name != ref.name:
                raise ExtensionError("invalid_manifest", "Plugin App identity does not match its declaration.")
            owner = definition.spec.managed_by
            if owner is not None and (owner.kind != "plugin" or owner.name != plugin_id):
                raise ExtensionError("invalid_manifest", "Plugin App declares a different owner.")
            apps.append(ParsedPluginApp(ref, definition))
            risks.extend(tool.risk for tool in definition.spec.tools)
        prefixes: set[str] = set()
        for ref in resources.mcp_servers:
            self._require_namespaced(plugin_id, ref.name)
            server = self._read_model(
                self._resource_path(content_root, ref.path),
                McpServer,
                label="MCP Server",
            )
            if server.metadata.name != ref.name:
                raise ExtensionError("invalid_manifest", "Plugin MCP identity does not match its declaration.")
            owner = server.spec.managed_by
            if owner is not None and (owner.kind != "plugin" or owner.name != plugin_id):
                raise ExtensionError("invalid_manifest", "Plugin MCP declares a different owner.")
            if server.spec.enabled_agent_ids:
                raise ExtensionError("invalid_manifest", "Plugin MCP templates cannot contain Agent enablement.")
            bindings = (
                server.spec.transport.environment.values()
                if isinstance(server.spec.transport, McpStdioTransport)
                else server.spec.transport.headers.values()
            )
            if any(isinstance(value, McpSecretValue) for value in bindings):
                raise ExtensionError("invalid_manifest", "Plugin MCP templates cannot contain SecretRef bindings.")
            prefix = server.spec.policy.resolved_prefix(server.metadata.name)
            if prefix in prefixes:
                raise ExtensionError("invalid_manifest", "Plugin MCP tool-name prefixes must be unique.")
            prefixes.add(prefix)
            servers.append(ParsedPluginMcp(ref, server))
            risks.append(server.spec.risk)
        for ref in resources.agent_templates:
            self._require_namespaced(plugin_id, ref.name)
            template = self._read_model(
                self._resource_path(content_root, ref.path),
                PluginAgentTemplate,
                label="Agent template",
            )
            declared = {
                *(item.name for item in resources.skills),
                *(item.name for item in resources.app_definitions),
                *(item.name for item in resources.mcp_servers),
            }
            referenced = {
                *template.spec.skills,
                *template.spec.app_definitions,
                *template.spec.mcp_servers,
            }
            if not referenced.issubset(declared):
                raise ExtensionError("invalid_manifest", "Agent template references an undeclared resource.")
        for ref in resources.config_schemas:
            self._require_namespaced(plugin_id, ref.name)
            self._read_model(
                self._resource_path(content_root, ref.path),
                PluginConfigSchema,
                label="config schema",
            )
        for ref in resources.documentation:
            self._require_namespaced(plugin_id, ref.name)
            path = self._resource_path(content_root, ref.path)
            if path.suffix.lower() != ".md" or not path.is_file():
                raise ExtensionError("invalid_manifest", "Plugin documentation must be a Markdown file.")
            try:
                path.read_text(encoding="utf-8")
            except (OSError, UnicodeError) as exc:
                raise ExtensionError("invalid_manifest", "Plugin documentation is not valid UTF-8.") from exc
        effective_risk = max(risks, key=lambda item: _RISK_ORDER[item])
        return PluginBundle(tuple(skills), tuple(apps), tuple(servers), effective_risk)

    def _readiness_for(self, manifest: PluginManifest, bundle: PluginBundle) -> PluginReadiness:
        issues: list[str] = []
        if not set(manifest.spec.runtime_capabilities).issubset(self.allowed_runtime_capabilities):
            issues.append("runtime_capability_unavailable")
        for item in bundle.skills:
            for executable in item.manifest.dependencies.executables:
                if self.executable_resolver(executable) is None:
                    issues.append("skill_executable_missing")
            for key in item.manifest.dependencies.environment:
                if key not in self.available_environment_keys:
                    issues.append("skill_environment_missing")
        for item in bundle.mcp_servers:
            transport = item.server.spec.transport
            if isinstance(transport, McpStdioTransport) and self.executable_resolver(transport.command) is None:
                issues.append("mcp_executable_missing")
        return PluginReadiness(ready=not issues, issues=tuple(sorted(set(issues))))

    def _require_identities_available(self, plugin_id: str, manifest: PluginManifest) -> None:
        if self.identity_index is None:
            return
        owner_key = f"plugin:{plugin_id}"
        for kind, values in (
            ("skill", manifest.spec.resources.skills),
            ("app", manifest.spec.resources.app_definitions),
            ("mcp", manifest.spec.resources.mcp_servers),
        ):
            for item in values:
                self.identity_index.require_available(kind, item.name, owner_key=owner_key)

    def _require_prefixes_available(
        self,
        plugin_id: str,
        bundle: PluginBundle,
        agent_id: str,
    ) -> None:
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
                ("app", plugin.record.spec.resources.app_definitions),
                ("mcp", plugin.record.spec.resources.mcp_servers),
            ):
                reservations.extend(
                    ResourceIdentityReservation(kind, item.name, owner) for item in values
                )
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

    def _project_app_definition(
        self,
        plugin: VersionedPlugin,
        definition: AppDefinition,
    ) -> AppDefinition:
        return self._project_app_definition_from_source(
            plugin.record.spec.source,
            plugin.record.spec.version,
            plugin.record.metadata.name,
            definition,
        )

    @staticmethod
    def _project_app_definition_from_source(
        source,
        version: str,
        plugin_id: str,
        definition: AppDefinition,
    ) -> AppDefinition:
        projected_source = source.model_copy(update={"version": version})
        return definition.model_copy(
            deep=True,
            update={
                "spec": definition.spec.model_copy(
                    deep=True,
                    update={
                        "source": projected_source,
                        "managed_by": AppOwnerRef(kind="plugin", name=plugin_id),
                    },
                )
            },
        )

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
        path = content_root.joinpath(*PurePosixPath(relative).parts).resolve(strict=False)
        if not path.is_relative_to(content_root.resolve(strict=True)):
            raise ExtensionError("unsafe_path", "Plugin resource path is outside installed content.")
        return path

    @staticmethod
    def _read_model(path: Path, model_type, *, label: str):
        if not path.is_file():
            raise ExtensionError("invalid_manifest", f"Plugin {label} path is not a file.")
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            return model_type.model_validate(raw)
        except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
            raise ExtensionError("invalid_manifest", f"Plugin {label} is invalid.") from exc

    @staticmethod
    def _require_namespaced(plugin_id: str, resource_id: str) -> None:
        if not resource_id.startswith(f"{plugin_id}--"):
            raise ExtensionError(
                "invalid_manifest",
                "Plugin resources must use the Plugin identity namespace.",
            )

    @staticmethod
    def _require_revision(current: VersionedPlugin, expected_revision: str) -> None:
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
    """Parse one strict Plugin root manifest without accepting compatibility fields."""
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return PluginManifest.model_validate(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValidationError, ValueError) as exc:
        raise ExtensionError("invalid_manifest", "Plugin manifest does not match the v1 schema.") from exc


def _trust_for_source(source_type: str) -> str:
    if source_type == "builtin":
        return "builtin"
    if source_type in {"local_directory", "local_archive"}:
        return "local"
    return "third_party"


def _validate_resource_name(value: str) -> None:
    if _RESOURCE_NAME_PATTERN.fullmatch(value) is None:
        raise ExtensionError("invalid_identity", "Plugin or Agent identity is invalid.")


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
