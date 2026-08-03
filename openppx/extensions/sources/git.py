"""Fixed-revision Git Source Adapter."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit, urlunsplit

from openppx.extensions.errors import ExtensionError
from openppx.extensions.models import ExtensionSourceRef

from .base import StagedExtension, StagingStore, _validate_relative_path, stage_directory
from .filesystem import _require_type


_COMMIT_PATTERN = re.compile(r"^[0-9a-fA-F]{40}$")


class GitSourceAdapter:
    """Clone and stage one explicitly pinned Git commit."""

    source_type = "git"

    def __init__(self, *, git_binary: str | None = None, timeout_seconds: float = 60.0) -> None:
        self.git_binary = git_binary or shutil.which("git") or ""
        self.timeout_seconds = timeout_seconds

    def stage(self, reference: ExtensionSourceRef, store: StagingStore) -> StagedExtension:
        """Resolve exactly one commit and copy content without the Git metadata."""
        _require_type(reference, self.source_type)
        if not self.git_binary:
            raise ExtensionError("invalid_source", "Git is unavailable on this Node.")
        if reference.revision is None or _COMMIT_PATTERN.fullmatch(reference.revision) is None:
            raise ExtensionError("invalid_source", "Git sources require a full fixed commit revision.")
        container, checkout = store.create()
        repository = container / "repository"
        try:
            _run_git(
                [self.git_binary, "clone", "--quiet", "--no-checkout", "--", reference.locator, str(repository)],
                timeout=self.timeout_seconds,
            )
            resolved = _run_git(
                [self.git_binary, "-C", str(repository), "rev-parse", f"{reference.revision}^{{commit}}"],
                timeout=self.timeout_seconds,
                capture=True,
            ).lower()
            if resolved != reference.revision.lower():
                raise ExtensionError("invalid_source", "Git source did not resolve to the requested commit.")
            _run_git(
                [self.git_binary, "-C", str(repository), "checkout", "--quiet", "--detach", resolved],
                timeout=self.timeout_seconds,
            )
            source = repository
            if reference.subpath:
                relative = _validate_relative_path(reference.subpath)
                source = repository.joinpath(*PurePosixPath(relative.as_posix()).parts)
                resolved_source = source.resolve(strict=False)
                if not resolved_source.is_relative_to(repository.resolve(strict=True)):
                    raise ExtensionError("unsafe_path", "Git subpath is outside the repository.")
            shutil.rmtree(checkout, ignore_errors=True)
            staged = stage_directory(
                source,
                store,
                identity_type="git",
                locator=f"git:{_git_locator(reference.locator)}",
                version=reference.version or resolved[:12],
                revision=resolved,
                exclude_names=frozenset({".git"}),
            )
            shutil.rmtree(container, ignore_errors=True)
            return staged
        except ExtensionError:
            shutil.rmtree(container, ignore_errors=True)
            raise
        except (OSError, subprocess.SubprocessError) as exc:
            shutil.rmtree(container, ignore_errors=True)
            raise ExtensionError("invalid_source", "Git source could not be staged.") from exc


def _run_git(command: list[str], *, timeout: float, capture: bool = False) -> str:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    if completed.returncode != 0:
        raise ExtensionError("invalid_source", "Git source could not be resolved.")
    return completed.stdout.strip() if capture else ""


def _git_locator(locator: str) -> str:
    """Return stable Git provenance without local paths or URL credentials."""
    path = Path(locator)
    if path.is_absolute() or path.exists():
        return path.name or "repository"
    parsed = urlsplit(locator)
    if parsed.scheme and parsed.hostname:
        host = parsed.hostname
        if parsed.port is not None:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path.removesuffix(".git"), "", ""))
    if "@" in locator and ":" in locator.split("@", 1)[1]:
        locator = locator.split("@", 1)[1]
    return locator.removesuffix(".git")


__all__ = ["GitSourceAdapter"]
