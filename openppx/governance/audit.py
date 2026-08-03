"""Durable, redacted Action audit facts owned by one OpenPPX Node."""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from openppx.actions.models import ActionOutcome

from .models import PolicyContext, PolicyDecision
from .redaction import redact


@dataclass(frozen=True, slots=True)
class AuditQuery:
    """Bounded filters for operator audit reads."""

    limit: int = 50
    actor_id: str | None = None
    agent_id: str | None = None
    run_id: str | None = None
    extension_id: str | None = None
    action_id: str | None = None
    outcome: str | None = None


class ActionAuditSink(Protocol):
    """Execution-layer port for beginning and completing one Action audit fact."""

    def begin(self, context: PolicyContext, decision: PolicyDecision) -> str | None: ...

    def complete(self, audit_id: str | None, outcome: "ActionOutcome") -> None: ...


class NullActionAuditSink:
    """No-op sink used only by isolated Action-kernel tests."""

    def begin(self, context: PolicyContext, decision: PolicyDecision) -> str | None:
        return None

    def complete(self, audit_id: str | None, outcome: "ActionOutcome") -> None:
        return None


class ActionAuditStore:
    """SQLite audit store that never persists Action input or output payloads."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve(strict=False)
        self._lock = threading.RLock()
        self._last_error: str | None = None

    def begin(self, context: PolicyContext, decision: PolicyDecision) -> str:
        """Persist one pending or denied decision before handler execution."""
        audit_id = f"audit_{uuid.uuid4().hex}"
        now = datetime.now(timezone.utc).isoformat()
        with self._lock:
            try:
                with self._connection() as connection:
                    connection.execute(
                        """
                        INSERT INTO action_audit (
                            audit_id, recorded_at, completed_at, request_id, correlation_id,
                            actor_id, client_id, device_id, node_id, agent_id, session_id,
                            run_id, task_id, extension_id, resource_id, action_id, risk,
                            decision_code, decision_reason, outcome_code, ok
                        ) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            audit_id,
                            now,
                            context.request_id,
                            context.correlation_id,
                            context.actor_id,
                            context.client_id,
                            context.device_id,
                            context.node_id,
                            context.agent_id,
                            context.session_id,
                            context.run_id,
                            context.task_id,
                            context.extension_id,
                            context.resource_id,
                            context.action_id,
                            context.risk,
                            decision.code,
                            decision.reason,
                            None if decision.allow else decision.code,
                            None if decision.allow else 0,
                        ),
                    )
                self._last_error = None
            except Exception as exc:
                self._last_error = _safe_error(exc)
                raise
        return audit_id

    def complete(self, audit_id: str | None, outcome: "ActionOutcome") -> None:
        """Complete one pending audit row without persisting result data."""
        if audit_id is None:
            return
        completed_at = datetime.now(timezone.utc).isoformat()
        outcome_code = "success" if outcome.ok else (outcome.error.code if outcome.error is not None else "failed")
        with self._lock:
            try:
                with self._connection() as connection:
                    connection.execute(
                        "UPDATE action_audit SET completed_at = ?, outcome_code = ?, ok = ? WHERE audit_id = ?",
                        (completed_at, outcome_code, 1 if outcome.ok else 0, audit_id),
                    )
                self._last_error = None
            except Exception as exc:
                self._last_error = _safe_error(exc)
                raise

    def list(self, query: AuditQuery | None = None) -> tuple[dict[str, object], ...]:
        """Return newest redacted audit rows matching bounded operator filters."""
        selected = query or AuditQuery()
        conditions: list[str] = []
        params: list[object] = []
        for column, value in (
            ("actor_id", selected.actor_id),
            ("agent_id", selected.agent_id),
            ("run_id", selected.run_id),
            ("extension_id", selected.extension_id),
            ("action_id", selected.action_id),
            ("outcome_code", selected.outcome),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                params.append(value)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        limit = min(200, max(1, int(selected.limit)))
        with self._lock:
            try:
                with self._connection() as connection:
                    rows = connection.execute(
                        "SELECT * FROM action_audit"
                        f"{where} ORDER BY recorded_at DESC, audit_id DESC LIMIT ?",
                        (*params, limit),
                    ).fetchall()
                self._last_error = None
            except Exception as exc:
                self._last_error = _safe_error(exc)
                raise
        return tuple(_project_row(row) for row in rows)

    def health(self) -> dict[str, object]:
        """Return a non-sensitive availability result for unified Node health."""
        try:
            with self._lock, self._connection() as connection:
                connection.execute("SELECT 1").fetchone()
            self._last_error = None
        except Exception as exc:
            self._last_error = _safe_error(exc)
        return {
            "state": "healthy" if self._last_error is None else "unavailable",
            "code": "audit_ready" if self._last_error is None else "audit_unavailable",
            "reason": "Audit storage is ready." if self._last_error is None else "Audit storage is unavailable.",
        }

    def _connect(self) -> sqlite3.Connection:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS action_audit (
                audit_id TEXT PRIMARY KEY,
                recorded_at TEXT NOT NULL,
                completed_at TEXT,
                request_id TEXT NOT NULL,
                correlation_id TEXT NOT NULL,
                actor_id TEXT NOT NULL,
                client_id TEXT,
                device_id TEXT,
                node_id TEXT,
                agent_id TEXT,
                session_id TEXT,
                run_id TEXT,
                task_id TEXT,
                extension_id TEXT,
                resource_id TEXT,
                action_id TEXT NOT NULL,
                risk TEXT NOT NULL,
                decision_code TEXT NOT NULL,
                decision_reason TEXT NOT NULL,
                outcome_code TEXT,
                ok INTEGER
            )
            """
        )
        connection.execute("CREATE INDEX IF NOT EXISTS idx_action_audit_action ON action_audit(action_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_action_audit_agent ON action_audit(agent_id)")
        connection.execute("CREATE INDEX IF NOT EXISTS idx_action_audit_run ON action_audit(run_id)")
        return connection

    @contextmanager
    def _connection(self):
        """Close every SQLite handle deterministically after commit or rollback."""
        connection = self._connect()
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _project_row(row: sqlite3.Row) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": row["audit_id"],
        "recordedAt": row["recorded_at"],
        "completedAt": row["completed_at"],
        "requestId": row["request_id"],
        "correlationId": row["correlation_id"],
        "actorId": row["actor_id"],
        "clientId": row["client_id"],
        "deviceId": row["device_id"],
        "nodeId": row["node_id"],
        "agentId": row["agent_id"],
        "sessionId": row["session_id"],
        "runId": row["run_id"],
        "taskId": row["task_id"],
        "extensionId": row["extension_id"],
        "resourceId": row["resource_id"],
        "actionId": row["action_id"],
        "risk": row["risk"],
        "decisionCode": row["decision_code"],
        "decisionReason": row["decision_reason"],
        "outcomeCode": row["outcome_code"],
        "ok": None if row["ok"] is None else bool(row["ok"]),
    }
    return redact(payload)  # type: ignore[return-value]


def _safe_error(exc: BaseException) -> str:
    projected = redact(str(exc))
    return str(projected)[:240]


__all__ = [
    "ActionAuditSink",
    "ActionAuditStore",
    "AuditQuery",
    "NullActionAuditSink",
]
