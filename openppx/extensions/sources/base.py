"""Source Adapter contracts and controlled staging helpers."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Protocol

from openppx.extensions.errors import ExtensionError
from openppx.extensions.models import ExtensionSourceIdentity, ExtensionSourceRef


@dataclass(frozen=True, slots=True)
class SourceLimits:
    """Bound filesystem work performed by one Source Adapter."""

    max_files: int = 2_000
    max_file_bytes: int = 16 * 1024 * 1024
    max_total_bytes: int = 64 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class StagedExtension:
    """Pinned source content copied into a controlled Node staging directory."""

    content_root: Path
    source: ExtensionSourceIdentity
    digest: str
    file_count: int
    size_bytes: int

    def cleanup(self) -> None:
        """Remove this temporary staging directory if it is still present."""
        shutil.rmtree(self.content_root.parent, ignore_errors=True)


class SourceAdapter(Protocol):
    """Fetch one source reference into controlled staging."""

    source_type: str

    def stage(self, reference: ExtensionSourceRef, store: "StagingStore") -> StagedExtension: ...


class StagingStore:
    """Create bounded staging directories below one explicit Node root."""

    def __init__(self, node_root: Path, *, limits: SourceLimits | None = None) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.root = self.node_root / "extensions" / "staging"
        self.limits = limits or SourceLimits()

    def create(self) -> tuple[Path, Path]:
        """Return a unique stage container and its content directory."""
        self.root.mkdir(parents=True, exist_ok=True)
        container = self.root / uuid.uuid4().hex
        content = container / "content"
        content.mkdir(parents=True)
        return container, content


def stage_directory(
    source_root: Path,
    store: StagingStore,
    *,
    identity_type: str,
    locator: str,
    version: str,
    revision: str | None = None,
    exclude_names: frozenset[str] = frozenset(),
) -> StagedExtension:
    """Validate and copy one directory without following filesystem links."""
    requested_source = source_root.expanduser()
    if requested_source.is_symlink():
        raise ExtensionError("unsafe_path", "Source directory cannot be a symbolic link.")
    source = requested_source.resolve(strict=False)
    if not source.is_dir():
        raise ExtensionError("invalid_source", "Source directory is unavailable.")
    container, destination = store.create()
    try:
        files, total = _copy_validated_tree(
            source,
            destination,
            limits=store.limits,
            exclude_names=exclude_names,
        )
        digest = content_digest(destination, limits=store.limits)
        identity = ExtensionSourceIdentity(
            type=identity_type,
            locator=locator,
            version=version,
            revision=revision or digest,
            digest=digest,
        )
        return StagedExtension(destination, identity, digest, files, total)
    except Exception:
        shutil.rmtree(container, ignore_errors=True)
        raise


def content_digest(root: Path, *, limits: SourceLimits) -> str:
    """Return a stable digest over relative paths, bytes, and executable bits."""
    digest = hashlib.sha256()
    files, _total = validated_files(root, limits=limits)
    for path, relative, mode in files:
        file_digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                file_digest.update(chunk)
        executable = "1" if mode & 0o111 else "0"
        digest.update(f"{relative}:{executable}:{file_digest.hexdigest()}\n".encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def validated_files(
    root: Path,
    *,
    limits: SourceLimits,
    exclude_names: frozenset[str] = frozenset(),
) -> tuple[list[tuple[Path, str, int]], int]:
    """Enumerate a regular-file-only tree under strict resource limits."""
    if not root.is_dir() or root.is_symlink():
        raise ExtensionError("invalid_source", "Source directory is unavailable.")
    files: list[tuple[Path, str, int]] = []
    total = 0
    for current, directories, names in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        safe_directories: list[str] = []
        for directory in sorted(directories):
            candidate = current_path / directory
            if directory in exclude_names:
                continue
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
                raise ExtensionError("unsafe_path", "Source contains an unsafe filesystem entry.")
            safe_directories.append(directory)
        directories[:] = safe_directories
        for name in sorted(names):
            if name in exclude_names:
                continue
            candidate = current_path / name
            info = candidate.lstat()
            if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode) or info.st_nlink > 1:
                raise ExtensionError("unsafe_path", "Source contains an unsafe filesystem entry.")
            relative = candidate.relative_to(root).as_posix()
            _validate_relative_path(relative)
            if info.st_size > limits.max_file_bytes:
                raise ExtensionError("archive_limit_exceeded", "Source file exceeds the size limit.")
            total += info.st_size
            if total > limits.max_total_bytes or len(files) >= limits.max_files:
                raise ExtensionError("archive_limit_exceeded", "Source expands beyond the configured limits.")
            files.append((candidate, relative, info.st_mode & 0o777))
    if not any(relative == "SKILL.md" for _path, relative, _mode in files):
        raise ExtensionError("invalid_manifest", "Source does not contain a root SKILL.md.")
    return files, total


def _copy_validated_tree(
    source: Path,
    destination: Path,
    *,
    limits: SourceLimits,
    exclude_names: frozenset[str],
) -> tuple[int, int]:
    files, total = validated_files(source, limits=limits, exclude_names=exclude_names)
    for path, relative, mode in files:
        target = destination.joinpath(*PurePosixPath(relative).parts)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target, follow_symlinks=False)
        target.chmod(0o755 if mode & 0o111 else 0o644)
    return len(files), total


def _validate_relative_path(raw_name: str) -> PurePosixPath:
    normalized = raw_name.replace("\\", "/")
    path = PurePosixPath(normalized)
    if (
        not normalized
        or "\x00" in normalized
        or path.is_absolute()
        or ".." in path.parts
        or (path.parts and ":" in path.parts[0])
    ):
        raise ExtensionError("unsafe_path", "Source contains an unsafe path.")
    return path


__all__ = [
    "SourceAdapter",
    "SourceLimits",
    "StagedExtension",
    "StagingStore",
    "content_digest",
    "stage_directory",
    "validated_files",
]
