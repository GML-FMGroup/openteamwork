"""Durable user Automation definitions, triggers, runs, and events.

Automation definitions are the only user-facing configuration facts.  The
Cron service is deliberately treated as a derived schedule adapter and never
as an alternative product model.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from openppx.runtime.paths import node_database_path


AutomationStatus = Literal["active", "paused", "blocked", "deleted"]
AutomationRunStatus = Literal["queued", "running", "completed", "failed", "cancelled", "skipped", "blocked"]
AUTOMATION_VISIBLE_STATUSES = frozenset({"active", "paused", "blocked"})
AUTOMATION_RUN_TERMINAL_STATUSES = frozenset({"completed", "failed", "cancelled", "skipped", "blocked"})


class AutomationStoreError(RuntimeError):
    """Base error for deterministic Automation persistence failures."""


class AutomationNotFoundError(AutomationStoreError):
    """Raised when one Automation resource does not exist."""


class AutomationConflictError(AutomationStoreError):
    """Raised when an optimistic revision precondition fails."""


class AutomationStateError(AutomationStoreError):
    """Raised when a requested lifecycle operation is invalid."""


@dataclass(frozen=True, slots=True)
class AutomationDefinition:
    """One user-created, durable background workflow definition."""

    automation_id: str
    name: str
    description: str
    instructions: str
    output_requirements_json: str
    status: str
    agent_id: str
    user_id: str
    workspace_ref: str
    context_mode: str
    model_profile_ref: str
    extension_policy_json: str
    permission_policy_json: str
    delivery_policy_json: str
    concurrency_policy_json: str
    missed_run_policy_json: str
    retry_policy_json: str
    budget_policy_json: str
    monitor_policy_json: str
    revision: int
    created_by: str
    idempotency_key: str
    correlation_id: str
    scheduler_job_id: str
    created_at_ms: int
    updated_at_ms: int
    deleted_at_ms: int | None

    @property
    def output_requirements(self) -> list[str]:
        return _json_string_list(self.output_requirements_json)

    @property
    def extension_policy(self) -> dict[str, Any]:
        return _json_object(self.extension_policy_json)

    @property
    def permission_policy(self) -> dict[str, Any]:
        return _json_object(self.permission_policy_json)

    @property
    def delivery_policy(self) -> dict[str, Any]:
        return _json_object(self.delivery_policy_json)

    @property
    def concurrency_policy(self) -> dict[str, Any]:
        return _json_object(self.concurrency_policy_json)

    @property
    def missed_run_policy(self) -> dict[str, Any]:
        return _json_object(self.missed_run_policy_json)

    @property
    def retry_policy(self) -> dict[str, Any]:
        return _json_object(self.retry_policy_json)

    @property
    def budget_policy(self) -> dict[str, Any]:
        return _json_object(self.budget_policy_json)

    @property
    def monitor_policy(self) -> dict[str, Any]:
        return _json_object(self.monitor_policy_json)


@dataclass(frozen=True, slots=True)
class AutomationTrigger:
    """One persisted automatic start condition for an Automation."""

    trigger_id: str
    automation_id: str
    trigger_type: str
    enabled: int
    schedule_kind: str
    every_seconds: int | None
    cron_expr: str
    at_ms: int | None
    timezone: str
    event_key: str
    input_schema_json: str
    next_run_at_ms: int | None
    last_run_at_ms: int | None
    created_at_ms: int
    updated_at_ms: int

    @property
    def input_schema(self) -> dict[str, Any]:
        return _json_object(self.input_schema_json)


@dataclass(frozen=True, slots=True)
class AutomationRun:
    """One immutable-definition execution attempt and its runtime facts."""

    automation_run_id: str
    automation_id: str
    definition_revision: int
    trigger_type: str
    trigger_occurrence_id: str
    started_by: str
    input_snapshot_json: str
    principal_snapshot_json: str
    permission_revision: str
    agent_revision: str
    model_profile_revision: str
    extension_snapshot_digest: str
    session_id: str
    adk_run_id: str
    task_run_refs_json: str
    goal_id: str
    status: str
    attempt: int
    artifact_refs_json: str
    delivery_refs_json: str
    output_summary: str
    error_summary: str
    blocked_reason: str
    budget_state_json: str
    correlation_id: str
    created_at_ms: int
    started_at_ms: int | None
    ended_at_ms: int | None
    updated_at_ms: int

    @property
    def input_snapshot(self) -> dict[str, Any]:
        return _json_object(self.input_snapshot_json)

    @property
    def principal_snapshot(self) -> dict[str, Any]:
        return _json_object(self.principal_snapshot_json)

    @property
    def task_run_refs(self) -> list[dict[str, Any]]:
        return _json_object_list(self.task_run_refs_json)

    @property
    def artifact_refs(self) -> list[dict[str, Any]]:
        return _json_object_list(self.artifact_refs_json)

    @property
    def delivery_refs(self) -> list[dict[str, Any]]:
        return _json_object_list(self.delivery_refs_json)

    @property
    def budget_state(self) -> dict[str, Any]:
        return _json_object(self.budget_state_json)


@dataclass(frozen=True, slots=True)
class AutomationEvent:
    """Append-only Automation audit and lifecycle event."""

    event_id: int
    automation_id: str
    automation_run_id: str
    event_type: str
    actor_id: str
    correlation_id: str
    payload_json: str
    created_at_ms: int

    @property
    def payload(self) -> dict[str, Any]:
        return _json_object(self.payload_json)


def automation_db_path() -> Path:
    """Return the default Node-owned Automation database path."""
    return node_database_path("automations.db")


def _now_ms() -> int:
    return int(time.time() * 1000)


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json_object(raw: str | None) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_object_list(raw: str | None) -> list[dict[str, Any]]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _json_string_list(raw: str | None) -> list[str]:
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item) for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _from_row(cls, row: sqlite3.Row):
    return cls(**{field: row[field] for field in cls.__dataclass_fields__})


class AutomationStore:
    """Persist Automation product facts with revision and occurrence guards."""

    def __init__(self, db_path: Path | None = None) -> None:
        self.db_path = (db_path or automation_db_path()).expanduser().resolve(strict=False)
        self._lock = threading.RLock()
        self._initialize()

    def _initialize(self) -> None:
        with self._lock, _connect(self.db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS automation_definitions (
                    automation_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT NOT NULL,
                    instructions TEXT NOT NULL,
                    output_requirements_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    workspace_ref TEXT NOT NULL,
                    context_mode TEXT NOT NULL,
                    model_profile_ref TEXT NOT NULL,
                    extension_policy_json TEXT NOT NULL,
                    permission_policy_json TEXT NOT NULL,
                    delivery_policy_json TEXT NOT NULL,
                    concurrency_policy_json TEXT NOT NULL,
                    missed_run_policy_json TEXT NOT NULL,
                    retry_policy_json TEXT NOT NULL,
                    budget_policy_json TEXT NOT NULL,
                    monitor_policy_json TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    created_by TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    correlation_id TEXT NOT NULL,
                    scheduler_job_id TEXT NOT NULL DEFAULT '',
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    deleted_at_ms INTEGER
                );
                CREATE INDEX IF NOT EXISTS automation_definitions_user_status_idx
                    ON automation_definitions(user_id, status, updated_at_ms DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS automation_definitions_user_name_idx
                    ON automation_definitions(user_id, lower(name)) WHERE status != 'deleted';

                CREATE TABLE IF NOT EXISTS automation_triggers (
                    trigger_id TEXT PRIMARY KEY,
                    automation_id TEXT NOT NULL REFERENCES automation_definitions(automation_id) ON DELETE CASCADE,
                    trigger_type TEXT NOT NULL,
                    enabled INTEGER NOT NULL,
                    schedule_kind TEXT NOT NULL,
                    every_seconds INTEGER,
                    cron_expr TEXT NOT NULL,
                    at_ms INTEGER,
                    timezone TEXT NOT NULL,
                    event_key TEXT NOT NULL,
                    input_schema_json TEXT NOT NULL,
                    next_run_at_ms INTEGER,
                    last_run_at_ms INTEGER,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS automation_triggers_automation_idx
                    ON automation_triggers(automation_id, trigger_type);

                CREATE TABLE IF NOT EXISTS automation_runs (
                    automation_run_id TEXT PRIMARY KEY,
                    automation_id TEXT NOT NULL REFERENCES automation_definitions(automation_id),
                    definition_revision INTEGER NOT NULL,
                    trigger_type TEXT NOT NULL,
                    trigger_occurrence_id TEXT NOT NULL,
                    started_by TEXT NOT NULL,
                    input_snapshot_json TEXT NOT NULL,
                    principal_snapshot_json TEXT NOT NULL,
                    permission_revision TEXT NOT NULL,
                    agent_revision TEXT NOT NULL,
                    model_profile_revision TEXT NOT NULL,
                    extension_snapshot_digest TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    adk_run_id TEXT NOT NULL,
                    task_run_refs_json TEXT NOT NULL,
                    goal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    attempt INTEGER NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    delivery_refs_json TEXT NOT NULL,
                    output_summary TEXT NOT NULL,
                    error_summary TEXT NOT NULL,
                    blocked_reason TEXT NOT NULL,
                    budget_state_json TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    started_at_ms INTEGER,
                    ended_at_ms INTEGER,
                    updated_at_ms INTEGER NOT NULL,
                    UNIQUE(automation_id, trigger_occurrence_id)
                );
                CREATE INDEX IF NOT EXISTS automation_runs_history_idx
                    ON automation_runs(automation_id, created_at_ms DESC);

                CREATE TABLE IF NOT EXISTS automation_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    automation_id TEXT NOT NULL,
                    automation_run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS automation_events_automation_idx
                    ON automation_events(automation_id, event_id DESC);

                CREATE TABLE IF NOT EXISTS automation_monitor_state (
                    automation_id TEXT PRIMARY KEY REFERENCES automation_definitions(automation_id) ON DELETE CASCADE,
                    observation_digest TEXT NOT NULL,
                    observation_summary TEXT NOT NULL,
                    cursor_json TEXT NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                """
            )

    def create_definition(
        self,
        *,
        name: str,
        description: str,
        instructions: str,
        output_requirements: list[str],
        agent_id: str,
        user_id: str,
        workspace_ref: str,
        context_mode: str,
        model_profile_ref: str,
        extension_policy: dict[str, Any],
        permission_policy: dict[str, Any],
        delivery_policy: dict[str, Any],
        concurrency_policy: dict[str, Any],
        missed_run_policy: dict[str, Any],
        retry_policy: dict[str, Any],
        budget_policy: dict[str, Any],
        monitor_policy: dict[str, Any],
        schedule: dict[str, Any] | None,
        idempotency_key: str,
        actor_id: str,
        correlation_id: str,
        local_event: dict[str, Any] | None = None,
    ) -> tuple[AutomationDefinition, AutomationTrigger | None]:
        """Create one definition and optional automatic trigger atomically."""
        now = _now_ms()
        automation_id = f"auto_{uuid.uuid4().hex[:20]}"
        with self._lock, _connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT * FROM automation_definitions WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
            if existing is not None:
                definition = _from_row(AutomationDefinition, existing)
                return definition, self.trigger_for(definition.automation_id, conn=conn)
            try:
                conn.execute(
                    """
                    INSERT INTO automation_definitions VALUES (
                        ?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        1, ?, ?, ?, '', ?, ?, NULL
                    )
                    """,
                    (
                        automation_id,
                        name.strip(),
                        description.strip(),
                        instructions.strip(),
                        _json_dumps(output_requirements),
                        agent_id,
                        user_id,
                        workspace_ref,
                        context_mode,
                        model_profile_ref,
                        _json_dumps(extension_policy),
                        _json_dumps(permission_policy),
                        _json_dumps(delivery_policy),
                        _json_dumps(concurrency_policy),
                        _json_dumps(missed_run_policy),
                        _json_dumps(retry_policy),
                        _json_dumps(budget_policy),
                        _json_dumps(monitor_policy),
                        actor_id,
                        idempotency_key,
                        correlation_id,
                        now,
                        now,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise AutomationConflictError("automation name already exists") from exc
            trigger_config = schedule or local_event
            trigger_type = "schedule" if schedule else "local_event"
            trigger = (
                self._insert_trigger(conn, automation_id, trigger_type, trigger_config, now)
                if trigger_config
                else None
            )
            self._append_event(conn, automation_id, "", "automation.created", actor_id, correlation_id, {})
            row = conn.execute("SELECT * FROM automation_definitions WHERE automation_id = ?", (automation_id,)).fetchone()
            assert row is not None
            return _from_row(AutomationDefinition, row), trigger

    def read_definition(self, automation_id: str, *, include_deleted: bool = False) -> AutomationDefinition:
        with self._lock, _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM automation_definitions WHERE automation_id = ?", (automation_id,)).fetchone()
            if row is None or (row["status"] == "deleted" and not include_deleted):
                raise AutomationNotFoundError("automation not found")
            return _from_row(AutomationDefinition, row)

    def list_definitions(
        self,
        *,
        user_id: str,
        statuses: list[str] | None = None,
        limit: int = 100,
    ) -> list[AutomationDefinition]:
        requested = list(statuses or AUTOMATION_VISIBLE_STATUSES)
        placeholders = ",".join("?" for _ in requested)
        with self._lock, _connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM automation_definitions WHERE user_id = ? AND status IN ({placeholders}) ORDER BY updated_at_ms DESC LIMIT ?",
                (user_id, *requested, limit),
            ).fetchall()
            return [_from_row(AutomationDefinition, row) for row in rows]

    def list_all_definitions(
        self,
        *,
        statuses: list[str] | None = None,
        limit: int = 10_000,
    ) -> list[AutomationDefinition]:
        """Return Node-owned definitions for startup reconciliation."""
        requested = list(statuses or AUTOMATION_VISIBLE_STATUSES)
        placeholders = ",".join("?" for _ in requested)
        with self._lock, _connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM automation_definitions WHERE status IN ({placeholders}) "
                "ORDER BY updated_at_ms DESC LIMIT ?",
                (*requested, max(1, min(int(limit), 50_000))),
            ).fetchall()
            return [_from_row(AutomationDefinition, row) for row in rows]

    def update_definition(self, automation_id: str, *, expected_revision: int, actor_id: str, correlation_id: str, **changes: Any) -> AutomationDefinition:
        """Update mutable definition fields under optimistic concurrency."""
        allowed = {
            "name", "description", "instructions", "output_requirements_json", "agent_id", "workspace_ref",
            "context_mode", "model_profile_ref", "extension_policy_json", "permission_policy_json",
            "delivery_policy_json", "concurrency_policy_json", "missed_run_policy_json", "retry_policy_json",
            "budget_policy_json", "monitor_policy_json", "scheduler_job_id",
        }
        updates = {key: value for key, value in changes.items() if key in allowed and value is not None}
        with self._lock, _connect(self.db_path) as conn:
            current = self._require_definition(conn, automation_id)
            if current.status == "deleted":
                raise AutomationStateError("deleted automation cannot be edited")
            if current.revision != expected_revision:
                raise AutomationConflictError("automation revision conflict")
            if updates:
                assignments = ", ".join(f"{key} = ?" for key in updates)
                try:
                    conn.execute(
                        f"UPDATE automation_definitions SET {assignments}, revision = revision + 1, updated_at_ms = ? WHERE automation_id = ? AND revision = ?",
                        (*updates.values(), _now_ms(), automation_id, expected_revision),
                    )
                except sqlite3.IntegrityError as exc:
                    raise AutomationConflictError("automation name already exists") from exc
            self._append_event(conn, automation_id, "", "automation.updated", actor_id, correlation_id, {"fields": sorted(updates)})
            return self._require_definition(conn, automation_id)

    def set_status(self, automation_id: str, *, status: AutomationStatus, expected_revision: int, actor_id: str, correlation_id: str) -> AutomationDefinition:
        with self._lock, _connect(self.db_path) as conn:
            current = self._require_definition(conn, automation_id)
            if current.revision != expected_revision:
                raise AutomationConflictError("automation revision conflict")
            if current.status == "deleted":
                raise AutomationStateError("deleted automation cannot transition")
            if status not in AUTOMATION_VISIBLE_STATUSES and status != "deleted":
                raise AutomationStateError("unsupported automation status")
            now = _now_ms()
            conn.execute(
                "UPDATE automation_definitions SET status = ?, revision = revision + 1, updated_at_ms = ?, deleted_at_ms = ? WHERE automation_id = ?",
                (status, now, now if status == "deleted" else None, automation_id),
            )
            self._append_event(conn, automation_id, "", f"automation.{status}", actor_id, correlation_id, {})
            return self._require_definition(conn, automation_id)

    def replace_trigger(
        self,
        automation_id: str,
        *,
        schedule: dict[str, Any] | None,
        local_event: dict[str, Any] | None,
    ) -> AutomationTrigger | None:
        """Replace the single automatic trigger without exposing Cron as a product fact."""
        if schedule is not None and local_event is not None:
            raise AutomationStateError("schedule and local event triggers are mutually exclusive")
        with self._lock, _connect(self.db_path) as conn:
            conn.execute("DELETE FROM automation_triggers WHERE automation_id = ?", (automation_id,))
            config = schedule or local_event
            if config is None:
                return None
            trigger_type = "schedule" if schedule is not None else "local_event"
            return self._insert_trigger(conn, automation_id, trigger_type, config, _now_ms())

    def replace_schedule(self, automation_id: str, schedule: dict[str, Any] | None) -> AutomationTrigger | None:
        """Compatibility helper for callers that only manage schedule triggers."""
        return self.replace_trigger(automation_id, schedule=schedule, local_event=None)

    def trigger_for(self, automation_id: str, *, conn: sqlite3.Connection | None = None) -> AutomationTrigger | None:
        owns = conn is None
        resolved = conn or _connect(self.db_path)
        try:
            row = resolved.execute(
                "SELECT * FROM automation_triggers WHERE automation_id = ? ORDER BY created_at_ms LIMIT 1",
                (automation_id,),
            ).fetchone()
            return _from_row(AutomationTrigger, row) if row is not None else None
        finally:
            if owns:
                resolved.close()

    def update_trigger_runtime(self, automation_id: str, *, next_run_at_ms: int | None, last_run_at_ms: int | None = None) -> None:
        with self._lock, _connect(self.db_path) as conn:
            conn.execute(
                "UPDATE automation_triggers SET next_run_at_ms = ?, last_run_at_ms = COALESCE(?, last_run_at_ms), updated_at_ms = ? WHERE automation_id = ?",
                (next_run_at_ms, last_run_at_ms, _now_ms(), automation_id),
            )

    def set_scheduler_job_id(self, automation_id: str, scheduler_job_id: str) -> AutomationDefinition:
        """Update the derived schedule reference without changing product revision."""
        with self._lock, _connect(self.db_path) as conn:
            self._require_definition(conn, automation_id)
            conn.execute(
                "UPDATE automation_definitions SET scheduler_job_id = ?, updated_at_ms = ? WHERE automation_id = ?",
                (scheduler_job_id, _now_ms(), automation_id),
            )
            return self._require_definition(conn, automation_id)

    def create_run(
        self,
        *,
        definition: AutomationDefinition,
        trigger_type: str,
        occurrence_id: str,
        started_by: str,
        input_snapshot: dict[str, Any],
        principal_snapshot: dict[str, Any],
        permission_revision: str,
        agent_revision: str,
        model_profile_revision: str,
        extension_snapshot_digest: str,
        task_id: str,
        correlation_id: str,
    ) -> tuple[AutomationRun, bool]:
        """Create one run once per trigger occurrence."""
        now = _now_ms()
        run_id = f"arun_{uuid.uuid4().hex[:20]}"
        session_id = f"automation-{run_id}"
        with self._lock, _connect(self.db_path) as conn:
            existing = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_id = ? AND trigger_occurrence_id = ?",
                (definition.automation_id, occurrence_id),
            ).fetchone()
            if existing is not None:
                return _from_row(AutomationRun, existing), False
            conn.execute(
                """
                INSERT INTO automation_runs VALUES (
                    ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, '', ?, '', 'queued', 1,
                    '[]', '[]', '', '', '', '{}', ?, ?, NULL, NULL, ?
                )
                """,
                (
                    run_id, definition.automation_id, definition.revision, trigger_type, occurrence_id, started_by,
                    _json_dumps(input_snapshot), _json_dumps(principal_snapshot), permission_revision, agent_revision,
                    model_profile_revision, extension_snapshot_digest, session_id,
                    _json_dumps([{"taskId": task_id, "role": "automation"}]), correlation_id, now, now,
                ),
            )
            self._append_event(conn, definition.automation_id, run_id, "automation_run.queued", started_by, correlation_id, {})
            row = conn.execute("SELECT * FROM automation_runs WHERE automation_run_id = ?", (run_id,)).fetchone()
            assert row is not None
            return _from_row(AutomationRun, row), True

    def read_run(self, automation_run_id: str) -> AutomationRun:
        with self._lock, _connect(self.db_path) as conn:
            row = conn.execute("SELECT * FROM automation_runs WHERE automation_run_id = ?", (automation_run_id,)).fetchone()
            if row is None:
                raise AutomationNotFoundError("automation run not found")
            return _from_row(AutomationRun, row)

    def run_for_occurrence(self, automation_id: str, occurrence_id: str) -> AutomationRun | None:
        """Resolve the idempotency fact before allocating a duplicate Task."""
        with self._lock, _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_id = ? AND trigger_occurrence_id = ?",
                (automation_id, occurrence_id),
            ).fetchone()
            return _from_row(AutomationRun, row) if row is not None else None

    def list_runs(self, automation_id: str, *, limit: int = 50) -> list[AutomationRun]:
        with self._lock, _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_id = ? ORDER BY created_at_ms DESC LIMIT ?",
                (automation_id, limit),
            ).fetchall()
            return [_from_row(AutomationRun, row) for row in rows]

    def list_incomplete_runs(self, *, limit: int = 1_000) -> list[AutomationRun]:
        """Return Runs that require explicit restart reconciliation."""
        with self._lock, _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM automation_runs WHERE status IN ('queued','running') "
                "ORDER BY created_at_ms ASC LIMIT ?",
                (max(1, min(int(limit), 10_000)),),
            ).fetchall()
            return [_from_row(AutomationRun, row) for row in rows]

    def active_run_count(self, automation_id: str) -> int:
        with self._lock, _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM automation_runs WHERE automation_id = ? AND status IN ('queued','running')",
                (automation_id,),
            ).fetchone()
            return int(row["count"] if row else 0)

    def running_run_count(self, automation_id: str) -> int:
        """Return running executions without counting a deliberately queued Run."""
        with self._lock, _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT COUNT(*) AS count FROM automation_runs WHERE automation_id = ? AND status = 'running'",
                (automation_id,),
            ).fetchone()
            return int(row["count"] if row else 0)

    def queued_runs(self, automation_id: str, *, limit: int = 1) -> list[AutomationRun]:
        """Return oldest queued Runs for queue-one draining."""
        with self._lock, _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_id = ? AND status = 'queued' "
                "ORDER BY created_at_ms ASC LIMIT ?",
                (automation_id, max(1, min(limit, 100))),
            ).fetchall()
            return [_from_row(AutomationRun, row) for row in rows]

    def set_run_attempt(self, automation_run_id: str, attempt: int) -> AutomationRun:
        """Persist the bounded retry attempt without changing terminal facts."""
        with self._lock, _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_run_id = ?",
                (automation_run_id,),
            ).fetchone()
            if row is None:
                raise AutomationNotFoundError("automation run not found")
            current = _from_row(AutomationRun, row)
            if current.status in AUTOMATION_RUN_TERMINAL_STATUSES:
                return current
            conn.execute(
                "UPDATE automation_runs SET attempt = ?, updated_at_ms = ? WHERE automation_run_id = ?",
                (max(1, int(attempt)), _now_ms(), automation_run_id),
            )
            row = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_run_id = ?",
                (automation_run_id,),
            ).fetchone()
            assert row is not None
            return _from_row(AutomationRun, row)

    def update_run(self, automation_run_id: str, *, status: AutomationRunStatus, **facts: Any) -> AutomationRun:
        allowed = {
            "adk_run_id", "artifact_refs_json", "delivery_refs_json", "goal_id", "output_summary",
            "error_summary", "blocked_reason", "budget_state_json", "started_at_ms", "ended_at_ms",
        }
        changes = {key: value for key, value in facts.items() if key in allowed and value is not None}
        now = _now_ms()
        if status == "running" and "started_at_ms" not in changes:
            changes["started_at_ms"] = now
        if status in AUTOMATION_RUN_TERMINAL_STATUSES and "ended_at_ms" not in changes:
            changes["ended_at_ms"] = now
        with self._lock, _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_run_id = ?",
                (automation_run_id,),
            ).fetchone()
            if row is None:
                raise AutomationNotFoundError("automation run not found")
            current = _from_row(AutomationRun, row)
            if current.status in AUTOMATION_RUN_TERMINAL_STATUSES:
                return current
            assignments = ", ".join(["status = ?", *(f"{key} = ?" for key in changes), "updated_at_ms = ?"])
            conn.execute(
                f"UPDATE automation_runs SET {assignments} WHERE automation_run_id = ?",
                (status, *changes.values(), now, automation_run_id),
            )
            row = conn.execute(
                "SELECT * FROM automation_runs WHERE automation_run_id = ?",
                (automation_run_id,),
            ).fetchone()
            assert row is not None
            updated = _from_row(AutomationRun, row)
            self._append_event(
                conn, updated.automation_id, automation_run_id, f"automation_run.{status}",
                updated.started_by, updated.correlation_id,
                {"error": updated.error_summary, "blockedReason": updated.blocked_reason},
            )
            return updated

    def history(self, automation_id: str, *, limit: int = 100) -> list[AutomationEvent]:
        with self._lock, _connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM automation_events WHERE automation_id = ? ORDER BY event_id DESC LIMIT ?",
                (automation_id, limit),
            ).fetchall()
            return [_from_row(AutomationEvent, row) for row in rows]

    def monitor_state(self, automation_id: str) -> dict[str, Any]:
        """Read the last redacted observation used for monitor change detection."""
        with self._lock, _connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT * FROM automation_monitor_state WHERE automation_id = ?",
                (automation_id,),
            ).fetchone()
            if row is None:
                return {}
            return {
                "observationDigest": str(row["observation_digest"]),
                "observationSummary": str(row["observation_summary"]),
                "cursor": _json_object(str(row["cursor_json"])),
                "updatedAtMs": int(row["updated_at_ms"]),
            }

    def update_monitor_state(
        self,
        automation_id: str,
        *,
        observation_digest: str,
        observation_summary: str,
        cursor: dict[str, Any] | None = None,
    ) -> None:
        """Atomically replace the last monitor observation after a successful Run."""
        with self._lock, _connect(self.db_path) as conn:
            self._require_definition(conn, automation_id)
            conn.execute(
                """
                INSERT INTO automation_monitor_state (
                    automation_id, observation_digest, observation_summary, cursor_json, updated_at_ms
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(automation_id) DO UPDATE SET
                    observation_digest = excluded.observation_digest,
                    observation_summary = excluded.observation_summary,
                    cursor_json = excluded.cursor_json,
                    updated_at_ms = excluded.updated_at_ms
                """,
                (
                    automation_id,
                    observation_digest,
                    observation_summary[:16_384],
                    _json_dumps(cursor or {}),
                    _now_ms(),
                ),
            )

    def _insert_trigger(
        self,
        conn: sqlite3.Connection,
        automation_id: str,
        trigger_type: str,
        config: dict[str, Any] | None,
        now: int,
    ) -> AutomationTrigger:
        if not config:
            raise AutomationStateError("trigger configuration is required")
        if trigger_type == "local_event":
            event_key = str(config.get("eventKey") or config.get("event_key") or "").strip()
            input_schema = config.get("inputSchema") or config.get("input_schema") or {}
            if not event_key or not isinstance(input_schema, dict):
                raise AutomationStateError("local event trigger requires eventKey and inputSchema")
            trigger_id = f"atrg_{uuid.uuid4().hex[:20]}"
            conn.execute(
                "INSERT INTO automation_triggers VALUES (?, ?, 'local_event', 1, '', NULL, '', NULL, '', ?, ?, NULL, NULL, ?, ?)",
                (trigger_id, automation_id, event_key, _json_dumps(input_schema), now, now),
            )
            row = conn.execute("SELECT * FROM automation_triggers WHERE trigger_id = ?", (trigger_id,)).fetchone()
            assert row is not None
            return _from_row(AutomationTrigger, row)
        schedule = config
        kind = str(schedule.get("kind") or "").strip()
        if kind not in {"every", "cron", "at"}:
            raise AutomationStateError("unsupported schedule kind")
        trigger_id = f"atrg_{uuid.uuid4().hex[:20]}"
        conn.execute(
            "INSERT INTO automation_triggers VALUES (?, ?, 'schedule', 1, ?, ?, ?, ?, ?, '', '{}', NULL, NULL, ?, ?)",
            (
                trigger_id, automation_id, kind, schedule.get("everySeconds"), str(schedule.get("cronExpr") or ""),
                schedule.get("atMs"), str(schedule.get("timezone") or ""), now, now,
            ),
        )
        row = conn.execute("SELECT * FROM automation_triggers WHERE trigger_id = ?", (trigger_id,)).fetchone()
        assert row is not None
        return _from_row(AutomationTrigger, row)

    def _require_definition(self, conn: sqlite3.Connection, automation_id: str) -> AutomationDefinition:
        row = conn.execute("SELECT * FROM automation_definitions WHERE automation_id = ?", (automation_id,)).fetchone()
        if row is None:
            raise AutomationNotFoundError("automation not found")
        return _from_row(AutomationDefinition, row)

    @staticmethod
    def _append_event(
        conn: sqlite3.Connection,
        automation_id: str,
        automation_run_id: str,
        event_type: str,
        actor_id: str,
        correlation_id: str,
        payload: dict[str, Any],
    ) -> None:
        conn.execute(
            "INSERT INTO automation_events (automation_id, automation_run_id, event_type, actor_id, correlation_id, payload_json, created_at_ms) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (automation_id, automation_run_id, event_type, actor_id, correlation_id, _json_dumps(payload), _now_ms()),
        )


__all__ = [
    "AUTOMATION_RUN_TERMINAL_STATUSES",
    "AUTOMATION_VISIBLE_STATUSES",
    "AutomationConflictError",
    "AutomationDefinition",
    "AutomationEvent",
    "AutomationNotFoundError",
    "AutomationRun",
    "AutomationStateError",
    "AutomationStore",
    "AutomationStoreError",
    "AutomationTrigger",
]
