"""Node-owned Skill manifest, registry, lifecycle, and Runtime snapshots."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Mapping

import yaml
from filelock import FileLock, Timeout
from pydantic import ValidationError

from openppx.config import ConfigRevisionConflict, ConfigWriteError, config_revision, read_json_object
from openppx.config.atomic import atomic_write_resource
from openppx.config.models import ResourceMetadata

from .errors import ExtensionError
from .indexes import ResourceIdentityIndex, ResourceIdentityReservation
from .models import (
    ExtensionSourceIdentity,
    ExtensionSourceRef,
    SkillDependencies,
    SkillManifest,
    SkillRecord,
    SkillRecordSpec,
)
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
    content_digest,
)


_RESOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


@dataclass(frozen=True, slots=True)
class StagedSkill:
    """Source content plus its validated Skill manifest."""

    extension: StagedExtension
    manifest: SkillManifest


@dataclass(frozen=True, slots=True)
class SkillPreview:
    """Client-safe preview produced before installation."""

    skill_id: str
    description: str
    version: str
    digest: str
    risk: str
    dependencies: SkillDependencies
    source: ExtensionSourceIdentity


@dataclass(frozen=True, slots=True)
class SkillReadiness:
    """Non-sensitive dependency readiness for one installed Skill."""

    ready: bool
    issues: tuple[str, ...]
    missing_executables: tuple[str, ...]
    missing_environment: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class VersionedSkill:
    """Validated Skill record plus internal immutable content location."""

    record: SkillRecord
    revision: str
    content_root: Path
    builtin: bool = False

    @property
    def status(self) -> str:
        """Return the stable lifecycle status exposed to clients."""
        if self.builtin:
            return "builtin"
        return "enabled" if self.record.spec.enabled_agent_ids else "disabled"


@dataclass(frozen=True, slots=True)
class SkillSnapshotEntry:
    """One immutable Runtime-facing Skill reference."""

    name: str
    description: str
    source: str
    digest: str
    content_root: Path


@dataclass(frozen=True, slots=True)
class SkillSnapshot:
    """Agent-specific immutable Skill set captured for one Runtime assembly."""

    revision: str
    skills: tuple[SkillSnapshotEntry, ...]

    @property
    def names(self) -> tuple[str, ...]:
        """Return stable Skill identifiers."""
        return tuple(skill.name for skill in self.skills)

    def build_summary(self) -> str:
        """Build the startup prompt summary without discovering new files."""
        lines = ["<skills>"]
        for skill in self.skills:
            lines.extend(
                [
                    "  <skill>",
                    f"    <name>{_xml_escape(skill.name)}</name>",
                    f"    <description>{_xml_escape(skill.description)}</description>",
                    f"    <source>{_xml_escape(skill.source)}</source>",
                    "  </skill>",
                ]
            )
        lines.append("</skills>")
        return "\n".join(lines)

    def read_skill(self, name: str) -> str:
        """Read the SKILL.md pinned by this snapshot."""
        normalized = name.strip()
        for skill in self.skills:
            if skill.name == normalized:
                try:
                    return (skill.content_root / "SKILL.md").read_text(encoding="utf-8")
                except (OSError, UnicodeError) as exc:
                    raise ExtensionError("extension_unavailable", "Installed Skill content is unavailable.") from exc
        raise ExtensionError("extension_not_found", f"Skill '{normalized}' was not found.")

    @classmethod
    def empty(cls) -> "SkillSnapshot":
        """Return the stable empty Extension snapshot."""
        return cls(revision="sha256:" + hashlib.sha256(b"[]").hexdigest(), skills=())


def merge_skill_snapshots(*snapshots: SkillSnapshot) -> SkillSnapshot:
    """Merge immutable Skill projections while rejecting identity collisions."""
    skills = tuple(
        sorted(
            (skill for snapshot in snapshots for skill in snapshot.skills),
            key=lambda skill: skill.name,
        )
    )
    names = [skill.name for skill in skills]
    if len(names) != len(set(names)):
        raise ExtensionError(
            "extension_conflict",
            "Skill projections contain a duplicate identity.",
        )
    canonical = json.dumps(
        [(skill.name, skill.digest) for skill in skills],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return SkillSnapshot(
        revision=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
        skills=skills,
    )


class SkillManager:
    """Own safe Skill staging, persistence, enablement, and Runtime snapshots."""

    def __init__(
        self,
        node_root: Path,
        *,
        builtin_skills: Mapping[str, Path] | None = None,
        catalog_adapters: Mapping[str, CatalogSourceAdapter] | None = None,
        source_limits: SourceLimits | None = None,
        executable_resolver: Callable[[str], str | None] = shutil.which,
        available_environment_keys: frozenset[str] = frozenset(),
        identity_index: ResourceIdentityIndex | None = None,
        lock_timeout: float = 5.0,
    ) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.root = self.node_root / "extensions" / "skills"
        self.records_dir = self.root / "records"
        self.content_dir = self.root / "content"
        self.staging = StagingStore(self.node_root, limits=source_limits)
        self.lock_timeout = lock_timeout
        self.executable_resolver = executable_resolver
        self.available_environment_keys = available_environment_keys
        self.identity_index = identity_index
        self._builtin_roots = {
            identifier: root.expanduser().resolve(strict=False)
            for identifier, root in (builtin_skills or {}).items()
        }
        self._adapters: dict[str, SourceAdapter] = {
            "builtin": BuiltinSourceAdapter(self._builtin_roots),
            "local_directory": LocalDirectorySourceAdapter(),
            "local_archive": LocalArchiveSourceAdapter(),
            "git": GitSourceAdapter(),
        }
        self._catalog_adapters = dict(catalog_adapters or {})
        self._builtins = self._load_builtins()
        if identity_index is not None:
            identity_index.register("direct-skills", self._identity_reservations)

    def stage(self, reference: ExtensionSourceRef) -> StagedSkill:
        """Stage and validate one Skill without changing installed state."""
        if reference.type == "catalog":
            adapter = self._catalog_adapters.get(reference.provider or "")
        else:
            adapter = self._adapters.get(reference.type)
        if adapter is None:
            raise ExtensionError("invalid_source", "No Source Adapter is registered for this reference.")
        staged = adapter.stage(reference, self.staging)
        try:
            manifest = parse_skill_manifest(staged.content_root / "SKILL.md")
            return StagedSkill(staged, manifest)
        except Exception:
            staged.cleanup()
            raise

    @staticmethod
    def preview(staged: StagedSkill) -> SkillPreview:
        """Return a client-safe install preview."""
        manifest = staged.manifest
        version = manifest.version if manifest.version != "0.0.0" else staged.extension.source.version
        return SkillPreview(
            skill_id=manifest.name,
            description=manifest.description,
            version=version,
            digest=staged.extension.digest,
            risk=manifest.risk,
            dependencies=manifest.dependencies,
            source=staged.extension.source,
        )

    def install(
        self,
        staged: StagedSkill,
        *,
        expected_revision: str | None,
        extension_id: str | None = None,
    ) -> VersionedSkill:
        """Atomically activate a staged Skill under a revision precondition."""
        skill_id = extension_id or staged.manifest.name
        _validate_resource_name(skill_id)
        if staged.manifest.name != skill_id:
            staged.extension.cleanup()
            raise ExtensionError("invalid_manifest", "Skill manifest name does not match its install identity.")
        if skill_id in self._builtins:
            staged.extension.cleanup()
            raise ExtensionError("extension_conflict", "Installed Skill cannot shadow a builtin Skill.")
        if self.identity_index is not None:
            self.identity_index.require_available(
                "skill",
                skill_id,
                owner_key=f"skill:{skill_id}",
            )
        previous = self._read_optional(skill_id)
        enabled_agents = list(previous.record.spec.enabled_agent_ids) if previous else []
        preview = self.preview(staged)
        target = self._content_path(skill_id, staged.extension.digest)
        try:
            self._activate_content(staged.extension.content_root, target)
            record = SkillRecord(
                api_version="openppx.io/v1alpha1",
                kind="Skill",
                metadata=ResourceMetadata(name=skill_id),
                spec=SkillRecordSpec(
                    description=preview.description,
                    version=preview.version,
                    digest=preview.digest,
                    source=staged.extension.source,
                    risk=preview.risk,
                    dependencies=preview.dependencies,
                    capabilities=staged.manifest.capabilities,
                    enabled_agent_ids=enabled_agents,
                ),
            )
            self._write_record(record, expected_revision=expected_revision)
            return self.get(skill_id)
        finally:
            staged.extension.cleanup()

    def update(
        self,
        staged: StagedSkill,
        *,
        expected_revision: str,
        extension_id: str | None = None,
    ) -> VersionedSkill:
        """Replace one installed Skill while preserving Agent enablement."""
        return self.install(
            staged,
            expected_revision=expected_revision,
            extension_id=extension_id,
        )

    def enable(
        self,
        skill_id: str,
        agent_id: str,
        *,
        expected_revision: str,
        confirmed: bool = False,
    ) -> VersionedSkill:
        """Enable one installed Skill for an Agent after readiness checks."""
        current = self.get(skill_id)
        if current.builtin:
            return current
        _validate_resource_name(agent_id)
        if current.record.spec.risk == "high" and not confirmed:
            raise ExtensionError("confirmation_required", "High-risk Skill enablement requires confirmation.")
        readiness = self.readiness(skill_id)
        if not readiness.ready:
            raise ExtensionError(
                "dependency_missing",
                "Skill dependencies are not ready.",
                details={
                    "executables": list(readiness.missing_executables),
                    "environment": list(readiness.missing_environment),
                },
            )
        if agent_id in current.record.spec.enabled_agent_ids:
            self._require_revision(current, expected_revision)
            return current
        enabled = sorted((*current.record.spec.enabled_agent_ids, agent_id))
        return self._replace_enablement(current, enabled, expected_revision=expected_revision)

    def disable(self, skill_id: str, agent_id: str, *, expected_revision: str) -> VersionedSkill:
        """Disable one installed Skill for an Agent."""
        current = self.get(skill_id)
        if current.builtin:
            raise ExtensionError("invalid_operation", "Builtin Skills cannot be disabled in this increment.")
        enabled = [item for item in current.record.spec.enabled_agent_ids if item != agent_id]
        return self._replace_enablement(current, enabled, expected_revision=expected_revision)

    def remove(self, skill_id: str, *, expected_revision: str) -> None:
        """Remove one disabled record while retaining content for active snapshots."""
        current = self.get(skill_id)
        if current.builtin:
            raise ExtensionError("invalid_operation", "Builtin Skills cannot be removed.")
        if current.record.spec.enabled_agent_ids:
            raise ExtensionError(
                "extension_in_use",
                "Skill must be disabled for every Agent before removal.",
                details={"agentIds": list(current.record.spec.enabled_agent_ids)},
            )
        path = self._record_path(skill_id)
        lock = FileLock(path.with_name(f"{path.name}.lock"), timeout=self.lock_timeout, mode=0o600)
        try:
            with lock:
                fresh = self.get(skill_id)
                self._require_revision(fresh, expected_revision)
                path.unlink()
                _fsync_directory(path.parent)
        except Timeout as exc:
            raise ExtensionError("registry_busy", "Skill registry is busy; retry with a fresh revision.") from exc
        except OSError as exc:
            raise ExtensionError("write_failed", "Skill record could not be removed.") from exc

    def get(self, skill_id: str) -> VersionedSkill:
        """Read one builtin or installed Skill by stable identity."""
        _validate_resource_name(skill_id)
        builtin = self._builtins.get(skill_id)
        if builtin is not None:
            return builtin
        result = self._read_optional(skill_id)
        if result is None:
            raise ExtensionError("extension_not_found", f"Skill '{skill_id}' was not found.")
        return result

    def list(self) -> tuple[VersionedSkill, ...]:
        """Return deterministic builtin and installed Skill records."""
        discovered = dict(self._builtins)
        if self.records_dir.exists():
            for path in sorted(self.records_dir.glob("*.json"), key=lambda item: item.name):
                skill_id = path.stem
                if skill_id in discovered:
                    raise ExtensionError("extension_conflict", "Installed Skill conflicts with a builtin identity.")
                discovered[skill_id] = self._read_record(path)
        return tuple(discovered[identifier] for identifier in sorted(discovered))

    def readiness(self, skill_id: str) -> SkillReadiness:
        """Return current executable/environment readiness without reading values."""
        current = self.get(skill_id)
        missing_executables = tuple(
            name
            for name in current.record.spec.dependencies.executables
            if self.executable_resolver(name) is None
        )
        missing_environment = tuple(
            name
            for name in current.record.spec.dependencies.environment
            if name not in self.available_environment_keys
        )
        issues: list[str] = []
        if missing_executables:
            issues.append("executable_missing")
        if missing_environment:
            issues.append("environment_missing")
        return SkillReadiness(
            ready=not issues,
            issues=tuple(issues),
            missing_executables=missing_executables,
            missing_environment=missing_environment,
        )

    def snapshot_for_agent(self, agent_id: str) -> SkillSnapshot:
        """Capture the Skill set that one newly assembled Agent may use."""
        _validate_resource_name(agent_id)
        entries: list[SkillSnapshotEntry] = []
        for item in self.list():
            if not item.builtin and agent_id not in item.record.spec.enabled_agent_ids:
                continue
            entries.append(
                SkillSnapshotEntry(
                    name=item.record.metadata.name,
                    description=item.record.spec.description,
                    source=item.record.spec.source.type,
                    digest=item.record.spec.digest,
                    content_root=item.content_root,
                )
            )
        canonical = json.dumps(
            [(item.name, item.digest) for item in entries],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        return SkillSnapshot(
            revision=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
            skills=tuple(entries),
        )

    def _load_builtins(self) -> dict[str, VersionedSkill]:
        builtins: dict[str, VersionedSkill] = {}
        for skill_id, root in sorted(self._builtin_roots.items()):
            _validate_resource_name(skill_id)
            manifest = parse_skill_manifest(root / "SKILL.md")
            if manifest.name != skill_id:
                raise ExtensionError("invalid_manifest", "Builtin Skill name does not match its registered identity.")
            digest = content_digest(root, limits=self.staging.limits)
            identity = ExtensionSourceIdentity(
                type="builtin",
                locator=f"builtin:{skill_id}",
                version=manifest.version if manifest.version != "0.0.0" else "builtin",
                revision=digest,
                digest=digest,
            )
            record = SkillRecord(
                api_version="openppx.io/v1alpha1",
                kind="Skill",
                metadata=ResourceMetadata(name=skill_id),
                spec=SkillRecordSpec(
                    description=manifest.description,
                    version=identity.version,
                    digest=digest,
                    source=identity,
                    risk=manifest.risk,
                    dependencies=manifest.dependencies,
                    capabilities=manifest.capabilities,
                    enabled_agent_ids=[],
                ),
            )
            builtins[skill_id] = VersionedSkill(record, config_revision(record), root, builtin=True)
        return builtins

    def _identity_reservations(self) -> tuple[ResourceIdentityReservation, ...]:
        """Project builtin and directly installed Skill identities."""
        return tuple(
            ResourceIdentityReservation(
                kind="skill",
                name=item.record.metadata.name,
                owner_key=f"skill:{item.record.metadata.name}",
            )
            for item in self.list()
        )

    def _replace_enablement(
        self,
        current: VersionedSkill,
        enabled_agent_ids: list[str],
        *,
        expected_revision: str,
    ) -> VersionedSkill:
        updated = current.record.model_copy(
            update={
                "spec": current.record.spec.model_copy(
                    update={"enabled_agent_ids": enabled_agent_ids}
                )
            }
        )
        self._write_record(updated, expected_revision=expected_revision)
        return self.get(current.record.metadata.name)

    def _write_record(self, record: SkillRecord, *, expected_revision: str | None) -> None:
        path = self._record_path(record.metadata.name)
        try:
            atomic_write_resource(
                path,
                record,
                source=f"skill:{record.metadata.name}",
                expected_revision=expected_revision,
                current_revision=lambda: (
                    current.revision if (current := self._read_optional(record.metadata.name)) is not None else None
                ),
                lock_timeout=self.lock_timeout,
            )
        except ConfigRevisionConflict as exc:
            raise ExtensionError(
                "revision_conflict",
                "Skill revision does not match current state.",
                details={
                    "expectedRevision": exc.expected_revision,
                    "actualRevision": exc.actual_revision,
                },
            ) from exc
        except ConfigWriteError as exc:
            raise ExtensionError(exc.kind, "Skill record could not be written.") from exc

    def _read_optional(self, skill_id: str) -> VersionedSkill | None:
        path = self._record_path(skill_id)
        if not path.exists():
            return None
        return self._read_record(path)

    def _read_record(self, path: Path) -> VersionedSkill:
        try:
            raw = read_json_object(path, source=f"skill:{path.stem}")
            record = SkillRecord.model_validate(raw)
        except (ValidationError, ValueError) as exc:
            raise ExtensionError("invalid_registry", "Installed Skill record is invalid.") from exc
        if record.metadata.name != path.stem:
            raise ExtensionError("invalid_registry", "Installed Skill identity does not match its record path.")
        content_root = self._content_path(record.metadata.name, record.spec.digest)
        if not content_root.joinpath("SKILL.md").is_file():
            raise ExtensionError("extension_unavailable", "Installed Skill content is unavailable.")
        return VersionedSkill(record, config_revision(record), content_root)

    def _record_path(self, skill_id: str) -> Path:
        _validate_resource_name(skill_id)
        path = (self.records_dir / f"{skill_id}.json").resolve(strict=False)
        if not path.is_relative_to(self.root.resolve(strict=False)):
            raise ExtensionError("unsafe_path", "Skill record path is outside the Node root.")
        return path

    def _content_path(self, skill_id: str, digest: str) -> Path:
        _validate_resource_name(skill_id)
        digest_id = digest.removeprefix("sha256:")
        if re.fullmatch(r"[0-9a-f]{64}", digest_id) is None:
            raise ExtensionError("invalid_registry", "Skill content digest is invalid.")
        path = (self.content_dir / skill_id / digest_id).resolve(strict=False)
        if not path.is_relative_to(self.root.resolve(strict=False)):
            raise ExtensionError("unsafe_path", "Skill content path is outside the Node root.")
        return path

    @staticmethod
    def _require_revision(current: VersionedSkill, expected_revision: str) -> None:
        if current.revision != expected_revision:
            raise ExtensionError(
                "revision_conflict",
                "Skill revision does not match current state.",
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
            raise ExtensionError("write_failed", "Skill content could not be activated.") from exc
        finally:
            shutil.rmtree(temporary, ignore_errors=True)


def parse_skill_manifest(path: Path) -> SkillManifest:
    """Parse and validate the bounded OpenPPX metadata in SKILL.md frontmatter."""
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ExtensionError("invalid_manifest", "SKILL.md could not be read as UTF-8.") from exc
    if not content.startswith("---\n"):
        raise ExtensionError("invalid_manifest", "SKILL.md must start with YAML frontmatter.")
    boundary = content.find("\n---", 4)
    if boundary < 0:
        raise ExtensionError("invalid_manifest", "SKILL.md frontmatter is not terminated.")
    try:
        raw = yaml.safe_load(content[4:boundary])
    except yaml.YAMLError as exc:
        raise ExtensionError("invalid_manifest", "SKILL.md frontmatter is invalid YAML.") from exc
    if not isinstance(raw, dict):
        raise ExtensionError("invalid_manifest", "SKILL.md frontmatter must be an object.")
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    openppx = metadata.get("openppx") if isinstance(metadata.get("openppx"), dict) else {}
    candidate = {
        "name": raw.get("name"),
        "description": raw.get("description"),
        "version": openppx.get("version", "0.0.0"),
        "risk": openppx.get("risk", "medium"),
        "dependencies": openppx.get("dependencies", {}),
        "capabilities": openppx.get("capabilities", []),
    }
    try:
        return SkillManifest.model_validate(candidate)
    except ValidationError as exc:
        raise ExtensionError("invalid_manifest", "SKILL.md manifest does not match the Skill schema.") from exc


def _validate_resource_name(value: str) -> None:
    if _RESOURCE_NAME_PATTERN.fullmatch(value) is None:
        raise ExtensionError("invalid_identity", "Skill or Agent identity is invalid.")


def _xml_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


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
    "SkillManager",
    "SkillPreview",
    "SkillReadiness",
    "SkillSnapshot",
    "SkillSnapshotEntry",
    "StagedSkill",
    "VersionedSkill",
    "merge_skill_snapshots",
    "parse_skill_manifest",
]
