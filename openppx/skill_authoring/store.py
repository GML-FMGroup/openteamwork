"""Private Node-local persistence for active Skill drafts and provenance."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import ValidationError

from openppx.config import ConfigRevisionConflict, ConfigWriteError, config_revision
from openppx.config.atomic import atomic_write_resource

from .models import MakeSkillDraftRecord, SkillProvenanceRecord


class MakeSkillStoreError(RuntimeError):
    """Raised when private Skill authoring state cannot be persisted safely."""


class MakeSkillDraftStore:
    """Persist one active draft per principal-scoped Session plus final provenance."""

    def __init__(self, node_root: Path, *, lock_timeout: float = 5.0) -> None:
        root = node_root.expanduser().resolve(strict=False) / "extensions"
        self.active_root = root / "skill-drafts" / "active"
        self.provenance_root = root / "skill-provenance"
        self.lock_timeout = lock_timeout

    def read_active(
        self,
        *,
        agent_id: str,
        user_id: str,
        session_id: str,
    ) -> MakeSkillDraftRecord | None:
        """Return the current Session draft without exposing another principal's state."""
        path = self._active_path(agent_id=agent_id, user_id=user_id, session_id=session_id)
        if not path.exists():
            return None
        try:
            record = MakeSkillDraftRecord.model_validate_json(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValidationError, ValueError) as exc:
            raise MakeSkillStoreError("The pending Skill draft is unavailable.") from exc
        if (
            record.agent_id != agent_id
            or record.user_id != user_id
            or record.session_id != session_id
        ):
            raise MakeSkillStoreError("The pending Skill draft ownership record is invalid.")
        return record

    def save_active(
        self,
        record: MakeSkillDraftRecord,
        *,
        previous: MakeSkillDraftRecord | None,
    ) -> None:
        """Create or revision-check one active draft atomically."""
        path = self._active_path(
            agent_id=record.agent_id,
            user_id=record.user_id,
            session_id=record.session_id,
        )
        try:
            atomic_write_resource(
                path,
                record,
                source=f"skill-draft:{record.draft_id}",
                expected_revision=None if previous is None else config_revision(previous),
                current_revision=lambda: self._revision(path),
                lock_timeout=self.lock_timeout,
            )
        except (ConfigRevisionConflict, ConfigWriteError) as exc:
            raise MakeSkillStoreError("The pending Skill draft changed; retry the command.") from exc

    def delete_active(self, record: MakeSkillDraftRecord) -> None:
        """Delete exactly the expected active draft after cancel or publication."""
        path = self._active_path(
            agent_id=record.agent_id,
            user_id=record.user_id,
            session_id=record.session_id,
        )
        lock = FileLock(path.with_name(f"{path.name}.lock"), timeout=self.lock_timeout, mode=0o600)
        try:
            with lock:
                current = self.read_active(
                    agent_id=record.agent_id,
                    user_id=record.user_id,
                    session_id=record.session_id,
                )
                if current is None:
                    return
                if config_revision(current) != config_revision(record):
                    raise MakeSkillStoreError("The pending Skill draft changed; retry the command.")
                path.unlink()
        except Timeout as exc:
            raise MakeSkillStoreError("The pending Skill draft is busy; retry the command.") from exc
        except OSError as exc:
            raise MakeSkillStoreError("The pending Skill draft could not be removed.") from exc

    def write_provenance(self, record: SkillProvenanceRecord) -> None:
        """Create immutable publication provenance without retaining raw transcript text."""
        path = self.provenance_root / f"{record.metadata.name}.json"
        try:
            atomic_write_resource(
                path,
                record,
                source=f"skill-provenance:{record.metadata.name}",
                expected_revision=None,
                current_revision=lambda: self._revision(path),
                lock_timeout=self.lock_timeout,
            )
        except (ConfigRevisionConflict, ConfigWriteError) as exc:
            raise MakeSkillStoreError("Skill provenance could not be persisted.") from exc

    def _active_path(self, *, agent_id: str, user_id: str, session_id: str) -> Path:
        identity = json.dumps(
            [agent_id, user_id, session_id],
            ensure_ascii=True,
            separators=(",", ":"),
        ).encode("utf-8")
        name = hashlib.sha256(identity).hexdigest()
        return self.active_root / f"{name}.json"

    @staticmethod
    def _revision(path: Path) -> str | None:
        if not path.exists():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if path.parent.name == "active":
                return config_revision(MakeSkillDraftRecord.model_validate(payload))
            return config_revision(SkillProvenanceRecord.model_validate(payload))
        except (OSError, UnicodeError, ValueError, ValidationError) as exc:
            raise MakeSkillStoreError("Skill authoring state is invalid.") from exc


__all__ = ["MakeSkillDraftStore", "MakeSkillStoreError"]
