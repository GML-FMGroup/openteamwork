"""Filesystem repository for strict Model Profile resources."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError

from openppx.config.atomic import atomic_write_resource
from openppx.config.diagnostics import ConfigIssue, ConfigLoadError, read_json_object, validation_issues
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
        atomic_write_resource(
            path,
            document,
            source=f"model-profile:{profile_id}",
            expected_revision=expected_revision,
            current_revision=lambda: self._current_revision(profile_id),
            lock_timeout=self.lock_timeout,
        )
        return self.read_profile(profile_id)

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
