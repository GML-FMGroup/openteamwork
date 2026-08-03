"""Safe local ZIP Source Adapter."""

from __future__ import annotations

import shutil
import stat
import zipfile
from pathlib import Path, PurePosixPath

from openppx.extensions.errors import ExtensionError
from openppx.extensions.models import ExtensionSourceIdentity, ExtensionSourceRef

from .base import StagedExtension, StagingStore, _validate_relative_path, content_digest
from .filesystem import _require_type


class LocalArchiveSourceAdapter:
    """Safely unpack one bounded local ZIP into controlled staging."""

    source_type = "local_archive"

    def stage(self, reference: ExtensionSourceRef, store: StagingStore) -> StagedExtension:
        """Validate every archive member before writing any extracted content."""
        _require_type(reference, self.source_type)
        requested_archive = Path(reference.locator).expanduser()
        if requested_archive.is_symlink():
            raise ExtensionError("unsafe_path", "Local archive cannot be a symbolic link.")
        archive_path = requested_archive.resolve(strict=False)
        if not archive_path.is_file() or archive_path.suffix.lower() != ".zip":
            raise ExtensionError("invalid_source", "Local archive must be an available ZIP file.")
        container, destination = store.create()
        try:
            with zipfile.ZipFile(archive_path, "r") as archive:
                entries, total = _validated_entries(archive, store)
                for info, normalized, mode in entries:
                    target = destination.joinpath(*PurePosixPath(normalized).parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(info, "r") as source, target.open("wb") as output:
                        _copy_bounded(source, output, expected_bytes=info.file_size)
                    target.chmod(0o755 if mode & 0o111 else 0o644)
            digest = content_digest(destination, limits=store.limits)
            identity = ExtensionSourceIdentity(
                type="local_archive",
                locator=f"local-archive:{archive_path.name}",
                version=reference.version or "local",
                revision=digest,
                digest=digest,
            )
            return StagedExtension(destination, identity, digest, len(entries), total)
        except ExtensionError:
            shutil.rmtree(container, ignore_errors=True)
            raise
        except (OSError, zipfile.BadZipFile) as exc:
            shutil.rmtree(container, ignore_errors=True)
            raise ExtensionError("invalid_source", "Local archive could not be staged.") from exc


def _validated_entries(
    archive: zipfile.ZipFile,
    store: StagingStore,
) -> tuple[list[tuple[zipfile.ZipInfo, str, int]], int]:
    """Return regular, unique, bounded ZIP entries."""
    entries: list[tuple[zipfile.ZipInfo, str, int]] = []
    seen: set[str] = set()
    total = 0
    for info in archive.infolist():
        raw_name = info.filename.replace("\\", "/")
        path = _validate_relative_path(raw_name)
        normalized = path.as_posix()
        mode = info.external_attr >> 16
        kind = stat.S_IFMT(mode)
        if kind == stat.S_IFLNK or kind not in {0, stat.S_IFREG, stat.S_IFDIR}:
            raise ExtensionError("unsafe_path", "Archive contains an unsafe filesystem entry.")
        if info.is_dir():
            continue
        if normalized in seen:
            raise ExtensionError("unsafe_path", "Archive contains a duplicate path.")
        seen.add(normalized)
        if info.file_size > store.limits.max_file_bytes:
            raise ExtensionError("archive_limit_exceeded", "Archive file exceeds the size limit.")
        total += info.file_size
        if len(entries) >= store.limits.max_files or total > store.limits.max_total_bytes:
            raise ExtensionError("archive_limit_exceeded", "Archive expands beyond the configured limits.")
        entries.append((info, normalized, mode & 0o777))
    if "SKILL.md" not in seen:
        raise ExtensionError("invalid_manifest", "Archive does not contain a root SKILL.md.")
    return entries, total


def _copy_bounded(source, output, *, expected_bytes: int) -> None:
    """Copy exactly the validated member size and reject inconsistent archives."""
    written = 0
    while chunk := source.read(min(1024 * 1024, expected_bytes - written + 1)):
        written += len(chunk)
        if written > expected_bytes:
            raise ExtensionError("archive_limit_exceeded", "Archive member exceeds its declared size.")
        output.write(chunk)
    if written != expected_bytes:
        raise ExtensionError("invalid_source", "Archive member size does not match its metadata.")


__all__ = ["LocalArchiveSourceAdapter"]
