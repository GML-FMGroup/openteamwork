"""Durable, redacted audit facts for permission decisions."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from .models import PermissionDecision, PermissionRequest


@dataclass(frozen=True, slots=True)
class PermissionAuditQuery:
    """Bounded filters for operator permission-audit reads."""

    limit: int = 50
    agent_id: str | None = None
    object: str | None = None
    outcome: str | None = None
    permission_revision: str | None = None


class PermissionAuditSink(Protocol):
    """Runtime port for recording one already-redacted authorization result."""

    def record(
        self,
        request: PermissionRequest,
        decision: PermissionDecision,
        *,
        rollout_mode: str,
    ) -> None: ...


class NullPermissionAuditSink:
    """No-op sink for isolated permission-kernel tests."""

    def record(
        self,
        request: PermissionRequest,
        decision: PermissionDecision,
        *,
        rollout_mode: str,
    ) -> None:
        return None


def record_permission_audit(
    sink: PermissionAuditSink,
    request: PermissionRequest,
    decision: PermissionDecision,
    *,
    rollout_mode: str,
) -> bool:
    """Record a decision, isolating observe failures and failing closed in enforce."""

    try:
        sink.record(request, decision, rollout_mode=rollout_mode)
    except Exception as exc:
        if rollout_mode == "enforce":
            raise PermissionError("Permission audit storage is unavailable during enforcement.") from exc
        return False
    return True


class PermissionAuditStore:
    """SQLite store that records decisions without resource values or Tool arguments."""

    def __init__(self, db_path: str | Path) -> None:
        self.db_path = Path(db_path).expanduser().resolve(strict=False)
        self._lock = threading.RLock()

    def record(
        self,
        request: PermissionRequest,
        decision: PermissionDecision,
        *,
        rollout_mode: str,
    ) -> None:
        """Persist bounded identity, policy, and decision facts atomically."""

        subject = request.subject
        resource_kind = request.resource.kind
        matched_rules_json = json.dumps(
            list(decision.matched_rule_ids),
            ensure_ascii=False,
            separators=(",", ":"),
        )
        with self._lock, self._connection() as connection:
            connection.execute(
                """
                INSERT INTO permission_audit (
                    recorded_at, request_id, agent_id, task_id, run_id, session_id,
                    object_kind, action, resource_kind, rollout_mode, outcome,
                    legacy_outcome, shadow_mismatch, reason_code,
                    permission_revision, matched_rule_ids_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    datetime.now(timezone.utc).isoformat(),
                    request.request_id,
                    subject.agent_id,
                    subject.task_id,
                    subject.run_id,
                    subject.session_id,
                    request.object,
                    request.action,
                    resource_kind,
                    rollout_mode,
                    decision.outcome,
                    "allow",
                    0 if decision.outcome == "allow" else 1,
                    decision.reason_code,
                    decision.permission_revision,
                    matched_rules_json,
                ),
            )

    def list(self, query: PermissionAuditQuery | None = None) -> tuple[dict[str, object], ...]:
        """Return newest redacted decisions matching bounded operator filters."""

        selected = query or PermissionAuditQuery()
        conditions: list[str] = []
        params: list[object] = []
        for column, value in (
            ("agent_id", selected.agent_id),
            ("object_kind", selected.object),
            ("outcome", selected.outcome),
            ("permission_revision", selected.permission_revision),
        ):
            if value is not None:
                conditions.append(f"{column} = ?")
                params.append(value)
        where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
        limit = min(200, max(1, int(selected.limit)))
        with self._lock, self._connection() as connection:
            rows = connection.execute(
                "SELECT * FROM permission_audit"
                f"{where} ORDER BY recorded_at DESC, audit_id DESC LIMIT ?",
                (*params, limit),
            ).fetchall()
        return tuple(_project_row(row) for row in rows)

    @contextmanager
    def _connection(self):
        """Open, initialize, commit, and deterministically close one connection."""

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.db_path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=FULL")
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS permission_audit (
                audit_id INTEGER PRIMARY KEY AUTOINCREMENT,
                recorded_at TEXT NOT NULL,
                request_id TEXT NOT NULL,
                agent_id TEXT NOT NULL,
                task_id TEXT,
                run_id TEXT,
                session_id TEXT,
                object_kind TEXT NOT NULL,
                action TEXT NOT NULL,
                resource_kind TEXT NOT NULL,
                rollout_mode TEXT NOT NULL,
                outcome TEXT NOT NULL,
                legacy_outcome TEXT NOT NULL,
                shadow_mismatch INTEGER NOT NULL,
                reason_code TEXT NOT NULL,
                permission_revision TEXT NOT NULL,
                matched_rule_ids_json TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_permission_audit_agent ON permission_audit(agent_id)"
        )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS idx_permission_audit_revision "
            "ON permission_audit(permission_revision)"
        )
        try:
            with connection:
                yield connection
        finally:
            connection.close()


def _project_row(row: sqlite3.Row) -> dict[str, object]:
    """Return a stable JSON-ready audit projection."""

    return {
        "id": row["audit_id"],
        "recordedAt": row["recorded_at"],
        "requestId": row["request_id"],
        "agentId": row["agent_id"],
        "taskId": row["task_id"],
        "runId": row["run_id"],
        "sessionId": row["session_id"],
        "object": row["object_kind"],
        "action": row["action"],
        "resourceKind": row["resource_kind"],
        "rolloutMode": row["rollout_mode"],
        "outcome": row["outcome"],
        "legacyOutcome": row["legacy_outcome"],
        "shadowMismatch": bool(row["shadow_mismatch"]),
        "reasonCode": row["reason_code"],
        "permissionRevision": row["permission_revision"],
        "matchedRuleIds": json.loads(row["matched_rule_ids_json"]),
    }


__all__ = [
    "NullPermissionAuditSink",
    "PermissionAuditQuery",
    "PermissionAuditSink",
    "PermissionAuditStore",
    "record_permission_audit",
]
