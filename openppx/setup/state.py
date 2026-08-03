"""Durable proof that first-run configuration completed a real model turn."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import StringConstraints, ValidationError

from openppx.config import ConfigIssue, ConfigLoadError, StrictConfigModel, config_revision, read_json_object, validation_issues
from openppx.config.atomic import atomic_write_resource


Revision = Annotated[str, StringConstraints(min_length=8, max_length=128)]


class SetupVerification(StrictConfigModel):
    """Non-sensitive revisions proven by one successful Runtime Hello."""

    api_version: Literal["openppx.io/v1alpha1"] = "openppx.io/v1alpha1"
    kind: Literal["SetupVerification"] = "SetupVerification"
    node_revision: Revision
    agent_revision: Revision
    profile_revision: Revision
    session_id: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    verified_at: Annotated[str, StringConstraints(min_length=20, max_length=64)]


class SetupStateRepository:
    """Read and atomically replace the Node-local setup verification record."""

    def __init__(self, node_root: Path, *, lock_timeout: float = 5.0) -> None:
        self.path = node_root.expanduser().resolve(strict=False) / "state" / "setup.json"
        self.lock_timeout = lock_timeout

    def read(self) -> SetupVerification:
        """Load the current verification record with strict diagnostics."""
        raw = read_json_object(self.path, source="setup-verification")
        try:
            return SetupVerification.model_validate(raw)
        except ValidationError as exc:
            raise ConfigLoadError(
                self.path,
                "invalid_schema",
                "Setup verification does not match its schema",
                validation_issues(exc, source="setup-verification"),
            ) from exc

    def mark_verified(
        self,
        *,
        node_revision: str,
        agent_revision: str,
        profile_revision: str,
        session_id: str,
    ) -> SetupVerification:
        """Persist a successful Hello against the exact effective resources."""
        document = SetupVerification(
            node_revision=node_revision,
            agent_revision=agent_revision,
            profile_revision=profile_revision,
            session_id=session_id,
            verified_at=datetime.now(timezone.utc).isoformat(),
        )
        expected = self._current_revision()
        atomic_write_resource(
            self.path,
            document,
            source="setup-verification",
            expected_revision=expected,
            current_revision=self._current_revision,
            lock_timeout=self.lock_timeout,
        )
        return self.read()

    def _current_revision(self) -> str | None:
        try:
            return config_revision(self.read())
        except ConfigLoadError as exc:
            if exc.kind == "not_found":
                return None
            raise


def verification_issue(error: ConfigLoadError) -> dict[str, object]:
    """Project corrupt verification state without leaking paths or raw values."""
    issue = error.issues[0] if error.issues else ConfigIssue(
        "invalid_setup_state",
        (),
        "Setup verification could not be read.",
        "setup-verification",
    )
    return {"code": issue.code, "message": issue.message}
