"""Codex-compatible Plugin marketplace sources and catalog projection.

A marketplace is discovery metadata, never a fifth Extension kind. Entries are
translated into the existing staged Plugin lifecycle so source limits, preview,
digest confirmation, and trust rules remain mandatory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Annotated, Any, Literal
from urllib.parse import urlsplit

from pydantic import Field, StringConstraints, ValidationError, field_validator, model_validator

from openppx.config import (
    ConfigRevisionConflict,
    ConfigWriteError,
    config_revision,
    read_json_object,
)
from openppx.config.atomic import atomic_write_resource
from openppx.config.models import ResourceMetadata, ResourceName, StrictConfigModel

from .errors import ExtensionError
from .models import ExtensionSourceRef
from .sources import GitSourceAdapter, LocalDirectorySourceAdapter, SourceLimits, StagingStore


_MARKETPLACE_PATH = ".agents/plugins/marketplace.json"
_FULL_COMMIT = re.compile(r"^[0-9a-fA-F]{40}$")


class PluginMarketplaceSourceSpec(StrictConfigModel):
    """One local or Git marketplace repository managed by the Node."""

    display_name: Annotated[str, StringConstraints(min_length=1, max_length=80)]
    type: Literal["local", "git"]
    locator: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    ref: Annotated[str, StringConstraints(min_length=1, max_length=256)] = "HEAD"
    resolved_revision: str | None = None
    catalog_digest: str | None = None
    entry_count: int = Field(default=0, ge=0, le=10_000)
    refreshed_at: str | None = None

    @field_validator("display_name", "locator", "ref")
    @classmethod
    def text_is_visible(cls, value: str) -> str:
        """Reject control-bearing source values."""
        if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("Marketplace source text is invalid")
        return value.strip()

    @field_validator("resolved_revision")
    @classmethod
    def revision_is_full_commit(cls, value: str | None) -> str | None:
        """Persist only immutable Git commits."""
        if value is not None and _FULL_COMMIT.fullmatch(value) is None:
            raise ValueError("Marketplace resolved revision is invalid")
        return value.lower() if value is not None else None

    @field_validator("catalog_digest")
    @classmethod
    def digest_is_sha256(cls, value: str | None) -> str | None:
        """Require a complete catalog digest when present."""
        if value is not None and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None:
            raise ValueError("Marketplace catalog digest is invalid")
        return value

    @model_validator(mode="after")
    def source_shape_is_safe(self) -> "PluginMarketplaceSourceSpec":
        """Reject credential-bearing Git URLs and ambiguous local refs."""
        if self.type == "local":
            if self.ref != "HEAD" or self.resolved_revision is not None:
                raise ValueError("Local marketplace cannot configure Git revisions")
        else:
            parsed = urlsplit(self.locator)
            local_git = Path(self.locator).expanduser()
            if not local_git.exists() and (
                parsed.scheme not in {"https", "ssh"}
                or (parsed.scheme == "https" and (not parsed.hostname or parsed.username or parsed.password))
            ):
                raise ValueError("Git marketplace locator is invalid")
        return self


class PluginMarketplaceSourceRecord(StrictConfigModel):
    """Revisioned marketplace source resource."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["PluginMarketplaceSource"]
    metadata: ResourceMetadata
    spec: PluginMarketplaceSourceSpec


@dataclass(frozen=True, slots=True)
class VersionedPluginMarketplaceSource:
    """Validated marketplace source and optimistic revision."""

    record: PluginMarketplaceSourceRecord
    revision: str

    def to_payload(self) -> dict[str, Any]:
        """Return client-safe source state."""
        return {
            "id": self.record.metadata.name,
            "revision": self.revision,
            "displayName": self.record.spec.display_name,
            "type": self.record.spec.type,
            "locator": self.record.spec.locator,
            "ref": self.record.spec.ref,
            "resolvedRevision": self.record.spec.resolved_revision,
            "catalogDigest": self.record.spec.catalog_digest,
            "entryCount": self.record.spec.entry_count,
            "refreshedAt": self.record.spec.refreshed_at,
            "ready": self.record.spec.catalog_digest is not None,
        }


@dataclass(frozen=True, slots=True)
class PluginMarketplaceEntry:
    """One marketplace Plugin projected into the standard install lifecycle."""

    marketplace_id: str
    plugin_id: str
    display_name: str
    description: str
    version: str
    developer: str
    category: str
    install_policy: str
    authentication_policy: str
    source: ExtensionSourceRef | None
    source_kind: str
    issue: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return discovery metadata without protected values."""
        return {
            "marketplaceId": self.marketplace_id,
            "pluginId": self.plugin_id,
            "displayName": self.display_name,
            "description": self.description,
            "version": self.version,
            "developer": self.developer,
            "category": self.category,
            "installationPolicy": self.install_policy,
            "authenticationPolicy": self.authentication_policy,
            "sourceKind": self.source_kind,
            "source": None if self.source is None else self.source.model_dump(mode="json", by_alias=True),
            "ready": self.source is not None and self.issue is None,
            "issue": self.issue,
        }


class PluginMarketplaceManager:
    """Own marketplace source persistence, refresh, and entry projection."""

    def __init__(
        self,
        node_root: Path,
        *,
        source_limits: SourceLimits | None = None,
        git_binary: str | None = None,
        lock_timeout: float = 5.0,
    ) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.root = self.node_root / "extensions" / "plugins" / "marketplaces"
        self.records_dir = self.root / "records"
        self.cache_dir = self.root / "cache"
        self.staging = StagingStore(
            self.node_root,
            limits=source_limits,
            required_root_file=_MARKETPLACE_PATH,
        )
        self.git_binary = git_binary or shutil.which("git") or ""
        self.lock_timeout = lock_timeout

    def create(
        self,
        marketplace_id: str,
        spec: PluginMarketplaceSourceSpec,
        *,
        expected_revision: None = None,
    ) -> VersionedPluginMarketplaceSource:
        """Create one unrefreshed source under a create-only precondition."""
        if expected_revision is not None:
            raise ExtensionError("revision_conflict", "New marketplace sources require an empty revision.")
        record = PluginMarketplaceSourceRecord(
            api_version="openppx.io/v1alpha1",
            kind="PluginMarketplaceSource",
            metadata=ResourceMetadata(name=marketplace_id),
            spec=spec.model_copy(
                update={
                    "resolved_revision": None,
                    "catalog_digest": None,
                    "entry_count": 0,
                    "refreshed_at": None,
                }
            ),
        )
        self._write(record, expected_revision=None)
        return self.get(marketplace_id)

    def update(
        self,
        marketplace_id: str,
        spec: PluginMarketplaceSourceSpec,
        *,
        expected_revision: str,
    ) -> VersionedPluginMarketplaceSource:
        """Update source coordinates and invalidate the previous refresh."""
        current = self.get(marketplace_id)
        self._require_revision(current, expected_revision)
        record = current.record.model_copy(
            update={
                "spec": spec.model_copy(
                    update={
                        "resolved_revision": None,
                        "catalog_digest": None,
                        "entry_count": 0,
                        "refreshed_at": None,
                    }
                )
            }
        )
        self._write(record, expected_revision=expected_revision)
        return self.get(marketplace_id)

    def refresh(self, marketplace_id: str, *, expected_revision: str) -> VersionedPluginMarketplaceSource:
        """Resolve and cache one exact marketplace repository snapshot."""
        current = self.get(marketplace_id)
        self._require_revision(current, expected_revision)
        staged = None
        try:
            spec = current.record.spec
            if spec.type == "local":
                staged = LocalDirectorySourceAdapter().stage(
                    ExtensionSourceRef(type="local_directory", locator=spec.locator),
                    self.staging,
                )
                resolved_revision = None
            else:
                resolved_revision = self._resolve_git_revision(spec.locator, spec.ref)
                staged = GitSourceAdapter(git_binary=self.git_binary).stage(
                    ExtensionSourceRef(
                        type="git",
                        locator=spec.locator,
                        revision=resolved_revision,
                    ),
                    self.staging,
                )
            document = _read_marketplace_document(staged.content_root / _MARKETPLACE_PATH)
            entries = _parse_entries(
                marketplace_id,
                document,
                source_record=current.record,
                content_root=staged.content_root,
                resolved_revision=resolved_revision,
            )
            target = self._cache_path(marketplace_id, staged.digest)
            _activate_tree(staged.content_root, target)
            updated = current.record.model_copy(
                update={
                    "spec": spec.model_copy(
                        update={
                            "resolved_revision": resolved_revision,
                            "catalog_digest": staged.digest,
                            "entry_count": len(entries),
                            "refreshed_at": datetime.now(UTC).isoformat(),
                        }
                    )
                }
            )
            self._write(updated, expected_revision=expected_revision)
            return self.get(marketplace_id)
        finally:
            if staged is not None:
                staged.cleanup()

    def entries(self, marketplace_id: str) -> tuple[PluginMarketplaceEntry, ...]:
        """Return entries from the last immutable refreshed snapshot."""
        current = self.get(marketplace_id)
        digest = current.record.spec.catalog_digest
        if digest is None:
            raise ExtensionError("dependency_missing", "Marketplace source must be refreshed first.")
        root = self._cache_path(marketplace_id, digest)
        document = _read_marketplace_document(root / _MARKETPLACE_PATH)
        return _parse_entries(
            marketplace_id,
            document,
            source_record=current.record,
            content_root=root,
            resolved_revision=current.record.spec.resolved_revision,
        )

    def list_entries(self, *, query: str | None = None) -> tuple[PluginMarketplaceEntry, ...]:
        """Aggregate ready cached entries across configured sources."""
        needle = (query or "").strip().casefold()
        values: list[PluginMarketplaceEntry] = []
        for source in self.list():
            if source.record.spec.catalog_digest is None:
                continue
            values.extend(self.entries(source.record.metadata.name))
        if needle:
            values = [
                item
                for item in values
                if needle in " ".join(
                    (item.display_name, item.description, item.developer, item.category)
                ).casefold()
            ]
        return tuple(sorted(values, key=lambda item: (item.display_name.casefold(), item.marketplace_id)))

    def get(self, marketplace_id: str) -> VersionedPluginMarketplaceSource:
        """Read one configured marketplace source."""
        path = self._record_path(marketplace_id)
        if not path.exists():
            raise ExtensionError("not_found", f"Plugin marketplace '{marketplace_id}' was not found.")
        try:
            record = PluginMarketplaceSourceRecord.model_validate(
                read_json_object(path, source=f"plugin-marketplace:{marketplace_id}")
            )
        except (ValidationError, ValueError) as exc:
            raise ExtensionError("invalid_registry", "Plugin marketplace source is invalid.") from exc
        if record.metadata.name != marketplace_id:
            raise ExtensionError("invalid_registry", "Plugin marketplace identity is inconsistent.")
        return VersionedPluginMarketplaceSource(record, config_revision(record))

    def list(self) -> tuple[VersionedPluginMarketplaceSource, ...]:
        """Return all configured sources in deterministic order."""
        if not self.records_dir.exists():
            return ()
        return tuple(self.get(path.stem) for path in sorted(self.records_dir.glob("*.json")))

    def remove(self, marketplace_id: str, *, expected_revision: str) -> None:
        """Remove one source record; immutable cache may be reclaimed later."""
        current = self.get(marketplace_id)
        self._require_revision(current, expected_revision)
        try:
            self._record_path(marketplace_id).unlink()
        except OSError as exc:
            raise ExtensionError("write_failed", "Plugin marketplace source could not be removed.") from exc

    def _resolve_git_revision(self, locator: str, ref: str) -> str:
        if not self.git_binary:
            raise ExtensionError("dependency_missing", "Git is unavailable on this Node.")
        if _FULL_COMMIT.fullmatch(ref):
            return ref.lower()
        try:
            completed = subprocess.run(
                [self.git_binary, "ls-remote", "--exit-code", locator, ref],
                check=False,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ExtensionError("invalid_source", "Git marketplace reference could not be resolved.") from exc
        candidates = [line.split()[0] for line in completed.stdout.splitlines() if line.split()]
        if completed.returncode != 0 or not candidates or _FULL_COMMIT.fullmatch(candidates[0]) is None:
            raise ExtensionError("invalid_source", "Git marketplace reference could not be resolved.")
        return candidates[0].lower()

    def _write(self, record: PluginMarketplaceSourceRecord, *, expected_revision: str | None) -> None:
        try:
            atomic_write_resource(
                self._record_path(record.metadata.name),
                record,
                source=f"plugin-marketplace:{record.metadata.name}",
                expected_revision=expected_revision,
                current_revision=lambda: (
                    current.revision
                    if self._record_path(record.metadata.name).exists()
                    and (current := self.get(record.metadata.name))
                    else None
                ),
                lock_timeout=self.lock_timeout,
            )
        except ConfigRevisionConflict as exc:
            raise ExtensionError("revision_conflict", "Plugin marketplace revision changed.") from exc
        except ConfigWriteError as exc:
            raise ExtensionError(exc.kind, "Plugin marketplace source could not be written.") from exc

    def _record_path(self, marketplace_id: str) -> Path:
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", marketplace_id) is None:
            raise ExtensionError("invalid_identity", "Plugin marketplace identity is invalid.")
        return self.records_dir / f"{marketplace_id}.json"

    def _cache_path(self, marketplace_id: str, digest: str) -> Path:
        digest_id = digest.removeprefix("sha256:")
        if re.fullmatch(r"[0-9a-f]{64}", digest_id) is None:
            raise ExtensionError("invalid_registry", "Plugin marketplace cache digest is invalid.")
        return self.cache_dir / marketplace_id / digest_id

    @staticmethod
    def _require_revision(current: VersionedPluginMarketplaceSource, expected_revision: str) -> None:
        if current.revision != expected_revision:
            raise ExtensionError(
                "revision_conflict",
                "Plugin marketplace revision changed.",
                details={"expectedRevision": expected_revision, "actualRevision": current.revision},
            )


def _read_marketplace_document(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ExtensionError("invalid_manifest", "Plugin marketplace document is invalid.") from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("plugins"), list):
        raise ExtensionError("invalid_manifest", "Plugin marketplace must contain a plugins array.")
    if len(raw["plugins"]) > 10_000:
        raise ExtensionError("archive_limit_exceeded", "Plugin marketplace contains too many entries.")
    return raw


def _parse_entries(
    marketplace_id: str,
    document: dict[str, Any],
    *,
    source_record: PluginMarketplaceSourceRecord,
    content_root: Path,
    resolved_revision: str | None,
) -> tuple[PluginMarketplaceEntry, ...]:
    values: list[PluginMarketplaceEntry] = []
    seen: set[str] = set()
    for raw in document["plugins"]:
        if not isinstance(raw, dict):
            raise ExtensionError("invalid_manifest", "Plugin marketplace entry is invalid.")
        plugin_id = _entry_text(raw, "name", 63)
        if re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", plugin_id) is None or plugin_id in seen:
            raise ExtensionError("invalid_manifest", "Plugin marketplace identities must be unique.")
        seen.add(plugin_id)
        source, source_kind, issue = _entry_source(
            raw.get("source"),
            source_record=source_record,
            content_root=content_root,
            resolved_revision=resolved_revision,
        )
        interface = raw.get("interface") if isinstance(raw.get("interface"), dict) else {}
        author = raw.get("author")
        developer = (
            author.get("name") if isinstance(author, dict) and isinstance(author.get("name"), str) else author
        )
        values.append(
            PluginMarketplaceEntry(
                marketplace_id=marketplace_id,
                plugin_id=plugin_id,
                display_name=_optional_text(interface.get("displayName"), _title(plugin_id), 80),
                description=_optional_text(raw.get("description"), "Plugin marketplace package.", 2048),
                version=_optional_text(raw.get("version"), "latest", 128),
                developer=_optional_text(developer, "Unknown developer", 128),
                category=_optional_text(raw.get("category") or interface.get("category"), "Other", 80),
                install_policy=_policy(raw.get("installationPolicy"), "AVAILABLE"),
                authentication_policy=_policy(raw.get("authenticationPolicy"), "ON_INSTALL"),
                source=source,
                source_kind=source_kind,
                issue=issue,
            )
        )
    return tuple(values)


def _entry_source(
    raw: object,
    *,
    source_record: PluginMarketplaceSourceRecord,
    content_root: Path,
    resolved_revision: str | None,
) -> tuple[ExtensionSourceRef | None, str, str | None]:
    if isinstance(raw, str):
        if not raw.startswith("./"):
            return None, "unknown", "marketplace_source_unsupported"
        subpath = _safe_subpath(raw)
        if source_record.spec.type == "git" and resolved_revision is not None:
            return (
                ExtensionSourceRef(
                    type="git",
                    locator=source_record.spec.locator,
                    revision=resolved_revision,
                    subpath=subpath,
                ),
                "local",
                None,
            )
        return ExtensionSourceRef(type="local_directory", locator=str(content_root / subpath)), "local", None
    if not isinstance(raw, dict):
        return None, "unknown", "marketplace_source_missing"
    kind = str(raw.get("source") or raw.get("type") or "").lower()
    if kind in {"local", "path"}:
        path = raw.get("path")
        if not isinstance(path, str) or not path.startswith("./"):
            return None, "local", "marketplace_local_path_invalid"
        return _entry_source(
            path,
            source_record=source_record,
            content_root=content_root,
            resolved_revision=resolved_revision,
        )
    if kind in {"git", "github", "git-subdir"}:
        url = raw.get("url")
        ref = raw.get("sha") or raw.get("ref")
        subpath = raw.get("path") or raw.get("subdir")
        if not isinstance(url, str) or not isinstance(ref, str) or _FULL_COMMIT.fullmatch(ref) is None:
            return None, "git", "marketplace_git_source_requires_pinned_sha"
        return (
            ExtensionSourceRef(
                type="git",
                locator=url,
                revision=ref.lower(),
                subpath=_safe_subpath(subpath) if isinstance(subpath, str) else None,
            ),
            "git",
            None,
        )
    if kind == "npm":
        package = raw.get("package") or raw.get("name")
        version = raw.get("version")
        if not isinstance(package, str) or not isinstance(version, str):
            return None, "npm", "marketplace_npm_source_requires_exact_version"
        try:
            return ExtensionSourceRef(type="npm", locator=package, version=version), "npm", None
        except ValidationError:
            return None, "npm", "marketplace_npm_source_requires_exact_version"
    return None, kind or "unknown", "marketplace_source_unsupported"


def _safe_subpath(value: str) -> str:
    normalized = value.removeprefix("./")
    path = PurePosixPath(normalized)
    if not path.parts or path.is_absolute() or ".." in path.parts or "\\" in value:
        raise ExtensionError("unsafe_path", "Plugin marketplace path is unsafe.")
    return path.as_posix()


def _entry_text(raw: dict[str, Any], key: str, limit: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ExtensionError("invalid_manifest", f"Plugin marketplace {key} is invalid.")
    return value.strip()


def _optional_text(value: object, default: str, limit: int) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    if len(value) > limit or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ExtensionError("invalid_manifest", "Plugin marketplace text is invalid.")
    return value.strip()


def _policy(value: object, default: str) -> str:
    if not isinstance(value, str) or not value.strip():
        return default
    normalized = value.strip().upper()
    if normalized not in {"AVAILABLE", "INSTALLED_BY_DEFAULT", "NOT_AVAILABLE", "ON_INSTALL", "ON_USE"}:
        raise ExtensionError("invalid_manifest", "Plugin marketplace policy is invalid.")
    return normalized


def _title(value: str) -> str:
    return " ".join(part.capitalize() for part in value.replace("_", "-").split("-") if part)


def _activate_tree(source: Path, target: Path) -> None:
    if target.exists():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        shutil.copytree(source, temporary, symlinks=False)
        os.replace(temporary, target)
    except OSError as exc:
        raise ExtensionError("write_failed", "Plugin marketplace cache could not be activated.") from exc
    finally:
        shutil.rmtree(temporary, ignore_errors=True)


__all__ = [
    "PluginMarketplaceEntry",
    "PluginMarketplaceManager",
    "PluginMarketplaceSourceRecord",
    "PluginMarketplaceSourceSpec",
    "VersionedPluginMarketplaceSource",
]
