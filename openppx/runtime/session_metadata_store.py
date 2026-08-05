"""Durable Node-owned presentation metadata for Google ADK Sessions."""

from __future__ import annotations

import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True, slots=True)
class SessionMetadata:
    """User-controlled metadata layered over an immutable ADK Session identity."""

    session_id: str
    agent_id: str
    principal_id: str
    title: str | None
    archived: bool
    updated_at: str


class SessionMetadataStore:
    """Persist Session titles and archive state without mutating ADK event history."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser().resolve(strict=False)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS session_metadata (
                    session_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    title TEXT,
                    archived INTEGER NOT NULL DEFAULT 0 CHECK (archived IN (0, 1)),
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_session_metadata_agent ON session_metadata(agent_id, principal_id)"
            )

    def get(self, session_id: str) -> SessionMetadata | None:
        """Return metadata for one Session, or ``None`` before its first mutation."""
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM session_metadata WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._project(row) if row is not None else None

    def update(
        self,
        *,
        session_id: str,
        agent_id: str,
        principal_id: str,
        title: str | None = None,
        archived: bool | None = None,
    ) -> SessionMetadata:
        """Upsert bounded metadata while retaining unspecified existing fields."""
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            current = connection.execute(
                "SELECT title, archived FROM session_metadata WHERE session_id = ?",
                (session_id,),
            ).fetchone()
            resolved_title = title if title is not None else (str(current["title"]) if current and current["title"] else None)
            resolved_archived = archived if archived is not None else bool(current["archived"]) if current else False
            connection.execute(
                """
                INSERT INTO session_metadata(session_id, agent_id, principal_id, title, archived, updated_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    principal_id=excluded.principal_id,
                    title=excluded.title,
                    archived=excluded.archived,
                    updated_at=excluded.updated_at
                """,
                (session_id, agent_id, principal_id, resolved_title, int(resolved_archived), now),
            )
        return SessionMetadata(session_id, agent_id, principal_id, resolved_title, resolved_archived, now)

    def delete(self, session_id: str) -> bool:
        """Delete presentation metadata after the owning ADK Session is removed."""
        with self._lock, self._connect() as connection:
            cursor = connection.execute("DELETE FROM session_metadata WHERE session_id = ?", (session_id,))
            return cursor.rowcount > 0

    @staticmethod
    def _project(row: sqlite3.Row) -> SessionMetadata:
        return SessionMetadata(
            session_id=str(row["session_id"]),
            agent_id=str(row["agent_id"]),
            principal_id=str(row["principal_id"]),
            title=str(row["title"]) if row["title"] is not None else None,
            archived=bool(row["archived"]),
            updated_at=str(row["updated_at"]),
        )
