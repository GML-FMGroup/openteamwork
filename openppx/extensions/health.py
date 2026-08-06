"""Durable, credential-free Extension health observations."""

from __future__ import annotations

import json
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


_SECRET = re.compile(
    r"(?i)(authorization|api[-_ ]?key|token|secret|password)\s*[:=]\s*([^\s,;]+)"
)


@dataclass(frozen=True, slots=True)
class ExtensionHealthObservation:
    """One bounded result from an explicit Extension connection test."""

    observation_id: int
    kind: str
    resource_id: str
    revision: str
    ready: bool
    status: str
    transport: str
    elapsed_ms: int
    attempts: int
    tool_count: int
    issues_json: str
    error_kind: str
    message: str
    checked_at_ms: int

    @property
    def issues(self) -> list[str]:
        """Decode the stable issue-code array."""
        try:
            value = json.loads(self.issues_json)
        except ValueError:
            return []
        return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []

    def to_payload(self) -> dict[str, object]:
        """Project a client-safe health observation."""
        return {
            "observationId": self.observation_id,
            "kind": self.kind,
            "id": self.resource_id,
            "revision": self.revision,
            "ready": self.ready,
            "status": self.status,
            "transport": self.transport,
            "elapsedMs": self.elapsed_ms,
            "attempts": self.attempts,
            "toolCount": self.tool_count,
            "issues": self.issues,
            "errorKind": self.error_kind or None,
            "message": self.message,
            "checkedAtMs": self.checked_at_ms,
        }


class ExtensionHealthStore:
    """Persist recent explicit probes without storing tools or credentials."""

    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path.expanduser().resolve(strict=False)
        self._lock = threading.RLock()
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS extension_health_observations (
                    observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    revision TEXT NOT NULL,
                    ready INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    transport TEXT NOT NULL,
                    elapsed_ms INTEGER NOT NULL,
                    attempts INTEGER NOT NULL,
                    tool_count INTEGER NOT NULL,
                    issues_json TEXT NOT NULL,
                    error_kind TEXT NOT NULL,
                    message TEXT NOT NULL,
                    checked_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS extension_health_resource_idx
                    ON extension_health_observations(kind, resource_id, observation_id DESC);
                """
            )

    def record(self, result: dict[str, object]) -> ExtensionHealthObservation:
        """Append one normalized probe and retain a bounded per-resource history."""
        kind = str(result.get("kind") or "")
        resource_id = str(result.get("id") or "")
        if kind not in {"mcp", "app_connection"} or not resource_id:
            raise ValueError("Extension health requires a supported resource identity")
        issues = sorted({str(item)[:128] for item in (result.get("issues") or []) if isinstance(item, str)})
        checked_at_ms = int(time.time() * 1_000)
        message = _SECRET.sub(lambda match: f"{match.group(1)}=[REDACTED]", str(result.get("message") or ""))[:2_000]
        values = (
            kind,
            resource_id,
            str(result.get("revision") or "")[:256],
            1 if bool(result.get("ready")) else 0,
            str(result.get("status") or "unknown")[:64],
            str(result.get("transport") or "unknown")[:64],
            max(0, int(result.get("elapsedMs") or 0)),
            max(0, int(result.get("attempts") or 0)),
            max(0, int(result.get("toolCount") or 0)),
            json.dumps(issues, separators=(",", ":")),
            str(result.get("errorKind") or "")[:128],
            message,
            checked_at_ms,
        )
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "INSERT INTO extension_health_observations (kind, resource_id, revision, ready, status, transport, elapsed_ms, attempts, tool_count, issues_json, error_kind, message, checked_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                values,
            )
            observation_id = int(cursor.lastrowid)
            connection.execute(
                "DELETE FROM extension_health_observations WHERE kind = ? AND resource_id = ? AND observation_id NOT IN (SELECT observation_id FROM extension_health_observations WHERE kind = ? AND resource_id = ? ORDER BY observation_id DESC LIMIT 50)",
                (kind, resource_id, kind, resource_id),
            )
            row = connection.execute(
                "SELECT * FROM extension_health_observations WHERE observation_id = ?",
                (observation_id,),
            ).fetchone()
        assert row is not None
        return _from_row(row)

    def recent(self, kind: str, resource_id: str, *, limit: int = 20) -> list[ExtensionHealthObservation]:
        """Return newest observations for one resource."""
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM extension_health_observations WHERE kind = ? AND resource_id = ? ORDER BY observation_id DESC LIMIT ?",
                (kind, resource_id, max(1, min(limit, 50))),
            ).fetchall()
        return [_from_row(row) for row in rows]

    def summary(self, kind: str, resource_id: str) -> dict[str, object]:
        """Return last success, last failure, and the most recent observation."""
        observations = self.recent(kind, resource_id, limit=50)
        latest = observations[0] if observations else None
        success = next((item for item in observations if item.ready), None)
        failure = next((item for item in observations if not item.ready), None)
        return {
            "latest": latest.to_payload() if latest else None,
            "lastSuccessAtMs": success.checked_at_ms if success else None,
            "lastFailureAtMs": failure.checked_at_ms if failure else None,
            "consecutiveFailures": next((index for index, item in enumerate(observations) if item.ready), len(observations)),
        }


def _from_row(row: sqlite3.Row) -> ExtensionHealthObservation:
    return ExtensionHealthObservation(
        observation_id=int(row["observation_id"]),
        kind=str(row["kind"]),
        resource_id=str(row["resource_id"]),
        revision=str(row["revision"]),
        ready=bool(row["ready"]),
        status=str(row["status"]),
        transport=str(row["transport"]),
        elapsed_ms=int(row["elapsed_ms"]),
        attempts=int(row["attempts"]),
        tool_count=int(row["tool_count"]),
        issues_json=str(row["issues_json"]),
        error_kind=str(row["error_kind"]),
        message=str(row["message"]),
        checked_at_ms=int(row["checked_at_ms"]),
    )


__all__ = ["ExtensionHealthObservation", "ExtensionHealthStore"]
