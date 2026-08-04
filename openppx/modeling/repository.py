"""Filesystem repository for strict Model Profile resources."""

from __future__ import annotations

from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import ValidationError

from openppx.config.atomic import atomic_write_resource
from openppx.config.diagnostics import ConfigIssue, ConfigLoadError, ConfigWriteError, read_json_object, validation_issues
from openppx.config.paths import ConfigPaths
from openppx.config.repository import ConfigSource, FilesystemConfigRepository, VersionedResource

from .profiles import ModelProfile


class ModelProfileRepository:
    """Read and atomically mutate Model Profiles below one explicit Node root."""

    def __init__(self, node_root: Path, *, lock_timeout: float = 5.0) -> None:
        self.paths = ConfigPaths(node_root)
        self.lock_timeout = lock_timeout

    def read_profile(self, profile_id: str) -> VersionedResource[ModelProfile]:
        """Load one Model Profile and enforce path/resource identity."""
        path = self.paths.model_profile_file(profile_id)
        source = ConfigSource("model_profile_file", path)
        raw = read_json_object(path, source=f"model-profile:{profile_id}")
        try:
            document = ModelProfile.model_validate(raw)
        except ValidationError as exc:
            issues = validation_issues(exc, source=f"model-profile:{profile_id}")
            raise ConfigLoadError(
                path,
                "invalid_schema",
                "Model Profile does not match its schema",
                issues,
            ) from exc
        self._require_matching_name(profile_id, document)
        return FilesystemConfigRepository._versioned(
            f"model-profile/{profile_id}",
            document,
            source,
        )

    def list_profile_ids(self) -> tuple[str, ...]:
        """Return sorted identifiers for every valid persisted Model Profile."""
        directory = self.paths.model_profiles_dir
        if not directory.exists():
            return ()
        try:
            entries = sorted((entry for entry in directory.iterdir() if entry.is_dir()), key=lambda item: item.name)
        except OSError as exc:
            issue = ConfigIssue("io_error", (), "Model Profile directory could not be read.", "model-profiles")
            raise ConfigLoadError(
                directory,
                "io_error",
                "Model Profile directory could not be read",
                (issue,),
            ) from exc
        identifiers: list[str] = []
        for entry in entries:
            self.read_profile(entry.name)
            identifiers.append(entry.name)
        return tuple(identifiers)

    def write_profile(
        self,
        profile_id: str,
        document: ModelProfile,
        *,
        expected_revision: str | None,
    ) -> VersionedResource[ModelProfile]:
        """Create or update one profile under an optimistic revision precondition."""
        self._require_matching_name(profile_id, document)
        path = self.paths.model_profile_file(profile_id)
        source = f"model-profile:{profile_id}"
        try:
            self.paths.node_root.mkdir(parents=True, exist_ok=True)
            with FileLock(
                self.paths.node_root / ".model-profiles.lock",
                timeout=self.lock_timeout,
                mode=0o600,
            ):
                self._require_unique_display_name(profile_id, document)
                atomic_write_resource(
                    path,
                    document,
                    source=source,
                    expected_revision=expected_revision,
                    current_revision=lambda: self._current_revision(profile_id),
                    lock_timeout=self.lock_timeout,
                )
        except Timeout as exc:
            issue = ConfigIssue(
                "lock_timeout",
                (),
                "Model Profile catalog is busy; retry with a fresh revision.",
                source,
            )
            raise ConfigWriteError(
                path,
                "lock_timeout",
                "Timed out waiting for the Model Profile catalog lock",
                (issue,),
            ) from exc
        except OSError as exc:
            issue = ConfigIssue(
                "write_failed",
                (),
                "Model Profile catalog could not be prepared for writing.",
                source,
            )
            raise ConfigWriteError(
                path,
                "write_failed",
                "Model Profile catalog write failed",
                (issue,),
            ) from exc
        return self.read_profile(profile_id)

    def _require_unique_display_name(self, profile_id: str, document: ModelProfile) -> None:
        """Enforce one case-insensitive human name across the entire Node catalog."""
        candidate = document.spec.display_name.strip().casefold()
        for existing_id in self.list_profile_ids():
            if existing_id == profile_id:
                continue
            existing = self.read_profile(existing_id)
            if existing.document.spec.display_name.strip().casefold() != candidate:
                continue
            path = self.paths.model_profile_file(profile_id)
            issue = ConfigIssue(
                "name_conflict",
                ("spec", "displayName"),
                "Model Profile displayName must be unique within the Node.",
                f"model-profile:{profile_id}",
            )
            raise ConfigWriteError(
                path,
                "name_conflict",
                "Model Profile display name conflict",
                (issue,),
            )

    def _current_revision(self, profile_id: str) -> str | None:
        try:
            return self.read_profile(profile_id).revision
        except ConfigLoadError as exc:
            if exc.kind == "not_found":
                return None
            raise

    def _require_matching_name(self, profile_id: str, document: ModelProfile) -> None:
        if document.metadata.name == profile_id:
            return
        path = self.paths.model_profile_file(profile_id)
        issue = ConfigIssue(
            "name_mismatch",
            ("metadata", "name"),
            "Model Profile metadata.name must match its resource path.",
            f"model-profile:{profile_id}",
        )
        raise ConfigLoadError(path, "name_mismatch", "Model Profile identity does not match its path", (issue,))
