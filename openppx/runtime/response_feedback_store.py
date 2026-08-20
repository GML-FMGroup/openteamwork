"""Durable authenticated-user feedback for visible Agent responses."""

from __future__ import annotations

import secrets
import sqlite3
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


ResponseRating = str
_RATINGS = frozenset({"up", "down"})


@dataclass(frozen=True, slots=True)
class ResponseFeedback:
    """One user's current rating for one stable response identity."""

    feedback_id: str
    principal_id: str
    agent_id: str
    session_id: str
    response_id: str
    run_id: str | None
    message_id: str
    rating: ResponseRating
    created_at_ms: int
    updated_at_ms: int


class ResponseFeedbackStore:
    """Persist mutually exclusive up/down ratings in the Session database."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self.db_path = Path(db_path).expanduser().resolve(strict=False)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        return connection

    def _initialize(self) -> None:
        """Create the additive response-feedback table and lookup index."""

        with self._lock, self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS response_feedback (
                    feedback_id TEXT PRIMARY KEY,
                    principal_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    response_id TEXT NOT NULL,
                    run_id TEXT,
                    message_id TEXT NOT NULL,
                    rating TEXT NOT NULL CHECK (rating IN ('up', 'down')),
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE(principal_id, session_id, response_id)
                )
                """
            )
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_response_feedback_session "
                "ON response_feedback(principal_id, session_id, updated_at_ms)"
            )

    @staticmethod
    def _required(value: str, label: str) -> str:
        normalized = str(value or "").strip()
        if not normalized:
            raise ValueError(f"{label} is required.")
        return normalized

    @staticmethod
    def _project(row: sqlite3.Row) -> ResponseFeedback:
        return ResponseFeedback(
            feedback_id=str(row["feedback_id"]),
            principal_id=str(row["principal_id"]),
            agent_id=str(row["agent_id"]),
            session_id=str(row["session_id"]),
            response_id=str(row["response_id"]),
            run_id=str(row["run_id"]) if row["run_id"] is not None else None,
            message_id=str(row["message_id"]),
            rating=str(row["rating"]),
            created_at_ms=int(row["created_at_ms"]),
            updated_at_ms=int(row["updated_at_ms"]),
        )

    def get(self, principal_id: str, session_id: str, response_id: str) -> ResponseFeedback | None:
        """Return the current rating for one authenticated-user response key."""

        with self._lock, self._connect() as connection:
            row = connection.execute(
                """
                SELECT * FROM response_feedback
                WHERE principal_id = ? AND session_id = ? AND response_id = ?
                """,
                (principal_id, session_id, response_id),
            ).fetchone()
        return self._project(row) if row is not None else None

    def list_for_session(self, principal_id: str, session_id: str) -> dict[str, ResponseRating]:
        """Return response ID to rating mappings for one visible Session."""

        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """
                SELECT response_id, rating FROM response_feedback
                WHERE principal_id = ? AND session_id = ?
                """,
                (principal_id, session_id),
            ).fetchall()
        return {str(row["response_id"]): str(row["rating"]) for row in rows}

    def set(
        self,
        *,
        principal_id: str,
        agent_id: str,
        session_id: str,
        response_id: str,
        run_id: str | None,
        message_id: str,
        rating: ResponseRating | None,
    ) -> ResponseFeedback | None:
        """Set, switch, or clear one user's current response rating."""

        normalized_principal_id = self._required(principal_id, "Principal ID")
        normalized_agent_id = self._required(agent_id, "Agent ID")
        normalized_session_id = self._required(session_id, "Session ID")
        normalized_response_id = self._required(response_id, "Response ID")
        normalized_message_id = self._required(message_id, "Message ID")
        normalized_run_id = str(run_id or "").strip() or None
        if rating is not None and rating not in _RATINGS:
            raise ValueError("Response rating must be up or down.")

        with self._lock, self._connect() as connection:
            if rating is None:
                connection.execute(
                    """
                    DELETE FROM response_feedback
                    WHERE principal_id = ? AND session_id = ? AND response_id = ?
                    """,
                    (normalized_principal_id, normalized_session_id, normalized_response_id),
                )
                return None
            now_ms = self._clock_ms()
            connection.execute(
                """
                INSERT INTO response_feedback (
                    feedback_id, principal_id, agent_id, session_id, response_id,
                    run_id, message_id, rating, created_at_ms, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(principal_id, session_id, response_id) DO UPDATE SET
                    agent_id=excluded.agent_id,
                    run_id=excluded.run_id,
                    message_id=excluded.message_id,
                    rating=excluded.rating,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    f"feedback_{secrets.token_hex(12)}",
                    normalized_principal_id,
                    normalized_agent_id,
                    normalized_session_id,
                    normalized_response_id,
                    normalized_run_id,
                    normalized_message_id,
                    rating,
                    now_ms,
                    now_ms,
                ),
            )
            row = connection.execute(
                """
                SELECT * FROM response_feedback
                WHERE principal_id = ? AND session_id = ? AND response_id = ?
                """,
                (normalized_principal_id, normalized_session_id, normalized_response_id),
            ).fetchone()
        if row is None:  # pragma: no cover - SQLite transaction invariant
            raise RuntimeError("Response feedback was not persisted.")
        return self._project(row)


__all__ = ["ResponseFeedback", "ResponseFeedbackStore", "ResponseRating"]
