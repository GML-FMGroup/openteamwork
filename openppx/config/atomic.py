"""Durable, revision-safe atomic writes for configuration resources."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable

from filelock import FileLock, Timeout
from pydantic import BaseModel

from .diagnostics import ConfigIssue, ConfigRevisionConflict, ConfigWriteError


def atomic_write_resource(
    path: Path,
    document: BaseModel,
    *,
    source: str,
    expected_revision: str | None,
    current_revision: Callable[[], str | None],
    lock_timeout: float,
) -> None:
    """Persist a validated resource under a lock and revision precondition.

    ``expected_revision=None`` is a create-only precondition. Existing resources
    must be updated with their current revision, preventing silent last-writer
    wins across processes.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_name(f"{path.name}.lock")
    lock = FileLock(lock_path, timeout=lock_timeout, mode=0o600)
    try:
        with lock:
            actual_revision = current_revision()
            if actual_revision != expected_revision:
                raise ConfigRevisionConflict(
                    path,
                    source=source,
                    expected_revision=expected_revision,
                    actual_revision=actual_revision,
                )
            _replace_resource(path, document, source=source)
    except Timeout as exc:
        issue = ConfigIssue(
            "lock_timeout",
            (),
            "Configuration resource is busy; retry with a fresh revision.",
            source,
        )
        raise ConfigWriteError(
            path,
            "lock_timeout",
            "Timed out waiting for the configuration lock",
            (issue,),
        ) from exc


def _replace_resource(path: Path, document: BaseModel, *, source: str) -> None:
    """Write and fsync a same-directory temporary file before atomic replace."""
    payload = json.dumps(
        document.model_dump(mode="json", by_alias=True),
        ensure_ascii=False,
        indent=2,
        sort_keys=True,
    ) + "\n"
    descriptor = -1
    temporary_path: Path | None = None
    try:
        descriptor, raw_path = tempfile.mkstemp(
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
        )
        temporary_path = Path(raw_path)
        _set_private_mode(descriptor, temporary_path)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            descriptor = -1
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary_path, path)
        temporary_path = None
        _fsync_directory(path.parent)
    except ConfigWriteError:
        raise
    except OSError as exc:
        issue = ConfigIssue(
            "write_failed",
            (),
            "Configuration resource could not be written atomically.",
            source,
        )
        raise ConfigWriteError(
            path,
            "write_failed",
            "Configuration write failed",
            (issue,),
        ) from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if temporary_path is not None:
            try:
                temporary_path.unlink(missing_ok=True)
            except OSError:
                pass


def _fsync_directory(directory: Path) -> None:
    """Flush a replaced directory entry where directory fsync is supported."""
    try:
        descriptor = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(descriptor)
    except OSError:
        # Windows and some filesystems do not support fsync on directories.
        pass
    finally:
        os.close(descriptor)


def _set_private_mode(descriptor: int, path: Path) -> None:
    """Set private resource permissions on POSIX and best-effort equivalents elsewhere."""
    try:
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        else:
            os.chmod(path, 0o600)
    except OSError:
        # Some Windows filesystems expose only coarse read-only permission bits.
        pass
