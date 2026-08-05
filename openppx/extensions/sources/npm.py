"""Pinned npm package Source Adapter for portable Plugin packages."""

from __future__ import annotations

import json
import re
import shutil
import stat
import subprocess
import tarfile
from pathlib import Path, PurePosixPath

from openppx.extensions.errors import ExtensionError
from openppx.extensions.models import ExtensionSourceIdentity, ExtensionSourceRef

from .base import StagedExtension, StagingStore, _validate_relative_path, content_digest
from .filesystem import _require_type


_PACKAGE = re.compile(r"^(?:@[a-z0-9][a-z0-9._-]*/)?[a-z0-9][a-z0-9._-]*$")
_VERSION = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


class NpmSourceAdapter:
    """Download and safely stage one exact npm package version without scripts."""

    source_type = "npm"

    def __init__(self, *, npm_binary: str | None = None, timeout_seconds: float = 60.0) -> None:
        self.npm_binary = npm_binary or shutil.which("npm") or ""
        self.timeout_seconds = timeout_seconds

    def stage(self, reference: ExtensionSourceRef, store: StagingStore) -> StagedExtension:
        """Pack one fixed package version, then extract only bounded regular files."""
        _require_type(reference, self.source_type)
        package = reference.locator.strip()
        version = (reference.version or "").strip()
        if _PACKAGE.fullmatch(package) is None or _VERSION.fullmatch(version) is None:
            raise ExtensionError("invalid_source", "npm sources require a valid package and exact version.")
        if reference.revision is not None or reference.subpath is not None or reference.provider is not None:
            raise ExtensionError("invalid_source", "npm sources do not accept revision, provider, or subpath.")
        if not self.npm_binary:
            raise ExtensionError("dependency_missing", "npm is unavailable on this Node.")

        container, destination = store.create()
        try:
            completed = subprocess.run(
                [
                    self.npm_binary,
                    "pack",
                    "--ignore-scripts",
                    "--json",
                    "--pack-destination",
                    str(container),
                    f"{package}@{version}",
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=self.timeout_seconds,
            )
            archive_path = _packed_archive(container, completed)
            files, total = _extract_package(archive_path, destination, store)
            digest = content_digest(destination, limits=store.limits)
            identity = ExtensionSourceIdentity(
                type="npm",
                locator=f"npm:{package}",
                version=version,
                revision=digest,
                digest=digest,
            )
            return StagedExtension(destination, identity, digest, files, total)
        except ExtensionError:
            shutil.rmtree(container, ignore_errors=True)
            raise
        except (OSError, subprocess.SubprocessError, tarfile.TarError) as exc:
            shutil.rmtree(container, ignore_errors=True)
            raise ExtensionError("invalid_source", "npm package could not be staged.") from exc


def _packed_archive(container: Path, completed: subprocess.CompletedProcess[str]) -> Path:
    if completed.returncode != 0:
        raise ExtensionError("invalid_source", "npm package could not be resolved.")
    try:
        payload = json.loads(completed.stdout)
        filename = payload[0]["filename"]
    except (json.JSONDecodeError, IndexError, KeyError, TypeError) as exc:
        raise ExtensionError("invalid_source", "npm pack returned an invalid result.") from exc
    if not isinstance(filename, str) or Path(filename).name != filename or not filename.endswith(".tgz"):
        raise ExtensionError("invalid_source", "npm pack returned an unsafe archive name.")
    archive = container / filename
    if not archive.is_file() or archive.is_symlink():
        raise ExtensionError("invalid_source", "npm pack did not produce a safe archive.")
    return archive


def _extract_package(archive_path: Path, destination: Path, store: StagingStore) -> tuple[int, int]:
    seen: set[str] = set()
    files = 0
    total = 0
    with tarfile.open(archive_path, mode="r:gz") as archive:
        for member in archive.getmembers():
            raw = member.name.replace("\\", "/")
            if not raw.startswith("package/"):
                raise ExtensionError("unsafe_path", "npm archive contains a path outside package/.")
            relative_raw = raw.removeprefix("package/")
            if not relative_raw or member.isdir():
                continue
            if not member.isreg() or member.issym() or member.islnk():
                raise ExtensionError("unsafe_path", "npm archive contains an unsafe filesystem entry.")
            relative = _validate_relative_path(relative_raw).as_posix()
            if relative in seen:
                raise ExtensionError("unsafe_path", "npm archive contains a duplicate path.")
            seen.add(relative)
            if member.size > store.limits.max_file_bytes:
                raise ExtensionError("archive_limit_exceeded", "npm package file exceeds the size limit.")
            files += 1
            total += member.size
            if files > store.limits.max_files or total > store.limits.max_total_bytes:
                raise ExtensionError("archive_limit_exceeded", "npm package expands beyond configured limits.")
            source = archive.extractfile(member)
            if source is None:
                raise ExtensionError("invalid_source", "npm package member could not be read.")
            target = destination.joinpath(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with source, target.open("wb") as output:
                written = shutil.copyfileobj(source, output)
            if written is not None:
                raise AssertionError("copyfileobj unexpectedly returned a value")
            if target.stat().st_size != member.size:
                raise ExtensionError("invalid_source", "npm package member size is inconsistent.")
            target.chmod(0o755 if member.mode & stat.S_IXUSR else 0o644)
    if store.required_root_file not in seen:
        raise ExtensionError(
            "invalid_manifest",
            f"npm package does not contain required manifest '{store.required_root_file}'.",
        )
    return files, total


__all__ = ["NpmSourceAdapter"]
