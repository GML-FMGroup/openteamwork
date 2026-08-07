"""SQLite-backed Goal and TaskFlow product facts.

The store deliberately owns intent and plan facts only. Actual execution stays
with Google ADK Runs and the existing Task runtime; TaskFlow rows reference
those execution facts instead of attempting to execute work themselves.
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

from .paths import node_database_path


GoalStatus = Literal[
    "active",
    "waiting",
    "paused",
    "blocked",
    "completed",
    "cancelled",
    "failed",
]
FlowStatus = Literal["active", "waiting", "paused", "blocked", "completed", "cancelled", "failed"]

GOAL_ACTIVE_STATUSES = frozenset({"active", "waiting", "paused", "blocked"})
GOAL_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})
FLOW_TERMINAL_STATUSES = frozenset({"completed", "cancelled", "failed"})
FLOW_STEP_STATUSES = frozenset({"pending", "running", "waiting", "blocked", "completed", "cancelled", "failed"})

_GOAL_TRANSITIONS: dict[str, frozenset[str]] = {
    "active": frozenset({"waiting", "paused", "blocked", "completed", "cancelled", "failed"}),
    "waiting": frozenset({"active", "paused", "blocked", "completed", "cancelled", "failed"}),
    "paused": frozenset({"active", "cancelled"}),
    "blocked": frozenset({"active", "completed", "cancelled", "failed"}),
    "completed": frozenset(),
    "cancelled": frozenset(),
    "failed": frozenset(),
}


class GoalStoreError(RuntimeError):
    """Base error for deterministic Goal persistence failures."""


class GoalNotFoundError(GoalStoreError):
    """Raised when a requested Goal or TaskFlow does not exist."""


class GoalConflictError(GoalStoreError):
    """Raised when optimistic revision checks fail."""


class GoalActiveExistsError(GoalStoreError):
    """Raised when a Session already owns an unfinished Goal."""


class GoalStateError(GoalStoreError):
    """Raised when a requested lifecycle transition is invalid."""


@dataclass(frozen=True, slots=True)
class Goal:
    """Durable user objective and completion policy."""

    goal_id: str
    session_id: str
    agent_id: str
    user_id: str
    workspace_ref: str
    objective: str
    completion_criteria_json: str
    constraints_json: str
    status: str
    revision: int
    active_flow_id: str
    budget_policy_json: str
    budget_state_json: str
    permission_revision: str
    model_profile_revision: str
    extension_snapshot_digest: str
    completion_evidence_json: str
    idempotency_key: str
    correlation_id: str
    created_by: str
    created_at_ms: int
    updated_at_ms: int
    completed_at_ms: int | None
    cancelled_at_ms: int | None

    @property
    def completion_criteria(self) -> list[str]:
        """Return normalized completion criteria."""
        return _json_string_list(self.completion_criteria_json)

    @property
    def constraints(self) -> list[str]:
        """Return normalized Goal constraints."""
        return _json_string_list(self.constraints_json)

    @property
    def budget_policy(self) -> dict[str, Any]:
        """Return the configured Goal budget policy."""
        return _json_object(self.budget_policy_json)

    @property
    def budget_state(self) -> dict[str, Any]:
        """Return accumulated Goal budget facts."""
        return _json_object(self.budget_state_json)

    @property
    def completion_evidence(self) -> list[dict[str, Any]]:
        """Return evidence cited by Goal completion."""
        return _json_object_list(self.completion_evidence_json)


@dataclass(frozen=True, slots=True)
class TaskFlow:
    """Durable plan facts for one Goal without execution behavior."""

    flow_id: str
    goal_id: str
    status: str
    revision: int
    steps_json: str
    task_run_refs_json: str
    artifact_refs_json: str
    wait_reason_json: str
    recovery_state_json: str
    last_event: str
    created_at_ms: int
    updated_at_ms: int

    @property
    def steps(self) -> list[dict[str, Any]]:
        """Return normalized TaskFlow steps."""
        return _json_object_list(self.steps_json)

    @property
    def task_run_refs(self) -> list[dict[str, Any]]:
        """Return bound TaskRun references."""
        return _json_object_list(self.task_run_refs_json)

    @property
    def artifact_refs(self) -> list[dict[str, Any]]:
        """Return bound Artifact references."""
        return _json_object_list(self.artifact_refs_json)

    @property
    def wait_reason(self) -> dict[str, Any]:
        """Return the current structured wait reason."""
        return _json_object(self.wait_reason_json)

    @property
    def recovery_state(self) -> dict[str, Any]:
        """Return ADK/runtime recovery references for this Flow."""
        return _json_object(self.recovery_state_json)


@dataclass(frozen=True, slots=True)
class GoalEvent:
    """Append-only audit event for one Goal."""

    event_id: int
    goal_id: str
    flow_id: str
    event_type: str
    actor_id: str
    correlation_id: str
    payload_json: str
    created_at_ms: int

    @property
    def payload(self) -> dict[str, Any]:
        """Return the event payload."""
        return _json_object(self.payload_json)


def goal_db_path() -> Path:
    """Return the default Node-owned Goal database path."""
    return node_database_path("goals.db")


def _now_ms() -> int:
    """Return current wall-clock time in milliseconds."""
    return int(time.time() * 1000)


def _json_dumps(value: Any) -> str:
    """Serialize one stable JSON value for SQLite."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _json_object(raw: str | None) -> dict[str, Any]:
    """Decode a JSON object or return an empty object."""
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _json_object_list(raw: str | None) -> list[dict[str, Any]]:
    """Decode a JSON list containing only objects."""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _json_string_list(raw: str | None) -> list[str]:
    """Decode a JSON list containing only non-empty strings."""
    try:
        value = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    return [str(item).strip() for item in value if isinstance(item, str) and item.strip()] if isinstance(value, list) else []


def _normalize_strings(values: list[str] | tuple[str, ...] | None) -> list[str]:
    """Normalize user-provided string collections without duplicates."""
    result: list[str] = []
    for value in values or ():
        normalized = str(value or "").strip()
        if normalized and normalized not in result:
            result.append(normalized)
    return result


def _connect(path: Path) -> sqlite3.Connection:
    """Open a Goal database connection with durable local defaults."""
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def _goal_from_row(row: sqlite3.Row) -> Goal:
    """Project one Goal row into an immutable dataclass."""
    return Goal(**{field: row[field] for field in Goal.__dataclass_fields__})


def _flow_from_row(row: sqlite3.Row) -> TaskFlow:
    """Project one TaskFlow row into an immutable dataclass."""
    return TaskFlow(**{field: row[field] for field in TaskFlow.__dataclass_fields__})


def _event_from_row(row: sqlite3.Row) -> GoalEvent:
    """Project one Goal event row into an immutable dataclass."""
    return GoalEvent(**{field: row[field] for field in GoalEvent.__dataclass_fields__})


def _normalize_steps(steps: list[dict[str, Any]] | tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Validate and normalize TaskFlow steps and dependency identities."""
    normalized: list[dict[str, Any]] = []
    ids: set[str] = set()
    for raw in steps:
        step_id = str(raw.get("stepId") or "").strip()
        title = str(raw.get("title") or "").strip()
        status = str(raw.get("status") or "pending").strip()
        if not step_id or not title:
            raise GoalStateError("TaskFlow steps require stepId and title")
        if step_id in ids:
            raise GoalStateError(f"duplicate TaskFlow step {step_id!r}")
        if status not in FLOW_STEP_STATUSES:
            raise GoalStateError(f"unsupported TaskFlow step status {status!r}")
        ids.add(step_id)
        normalized.append(
            {
                "stepId": step_id,
                "title": title,
                "status": status,
                "dependsOn": _normalize_strings(raw.get("dependsOn") or []),
                "expectedOutcome": str(raw.get("expectedOutcome") or "").strip(),
                "completionCriteria": _normalize_strings(raw.get("completionCriteria") or []),
            }
        )
    for step in normalized:
        unknown = [dependency for dependency in step["dependsOn"] if dependency not in ids]
        if unknown:
            raise GoalStateError(f"TaskFlow step {step['stepId']!r} has unknown dependencies")
        if step["stepId"] in step["dependsOn"]:
            raise GoalStateError("TaskFlow steps cannot depend on themselves")
    return normalized


class GoalStore:
    """Own transactional Goal, TaskFlow, and append-only Goal event facts."""

    def __init__(self, *, db_path: str | Path | None = None) -> None:
        self._db_path = Path(db_path).expanduser() if db_path is not None else goal_db_path()
        self._lock = threading.RLock()
        self.ensure_schema()

    @property
    def db_path(self) -> Path:
        """Return the backing SQLite path."""
        return self._db_path

    def ensure_schema(self) -> None:
        """Create the Goal domain schema and uniqueness guards."""
        with _connect(self._db_path) as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS goals (
                    goal_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    workspace_ref TEXT NOT NULL,
                    objective TEXT NOT NULL,
                    completion_criteria_json TEXT NOT NULL,
                    constraints_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    active_flow_id TEXT NOT NULL,
                    budget_policy_json TEXT NOT NULL,
                    budget_state_json TEXT NOT NULL,
                    permission_revision TEXT NOT NULL,
                    model_profile_revision TEXT NOT NULL,
                    extension_snapshot_digest TEXT NOT NULL,
                    completion_evidence_json TEXT NOT NULL,
                    idempotency_key TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    created_by TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    completed_at_ms INTEGER,
                    cancelled_at_ms INTEGER
                );
                CREATE TABLE IF NOT EXISTS task_flows (
                    flow_id TEXT PRIMARY KEY,
                    goal_id TEXT NOT NULL REFERENCES goals(goal_id) ON DELETE CASCADE,
                    status TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    steps_json TEXT NOT NULL,
                    task_run_refs_json TEXT NOT NULL,
                    artifact_refs_json TEXT NOT NULL,
                    wait_reason_json TEXT NOT NULL,
                    recovery_state_json TEXT NOT NULL,
                    last_event TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS goal_events (
                    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    goal_id TEXT NOT NULL REFERENCES goals(goal_id) ON DELETE CASCADE,
                    flow_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    actor_id TEXT NOT NULL,
                    correlation_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_goals_session ON goals(session_id, updated_at_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_goals_status ON goals(status, updated_at_ms DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS idx_goals_session_unfinished
                    ON goals(session_id) WHERE status IN ('active', 'waiting', 'paused', 'blocked');
                CREATE UNIQUE INDEX IF NOT EXISTS idx_goals_idempotency
                    ON goals(idempotency_key) WHERE idempotency_key <> '';
                CREATE INDEX IF NOT EXISTS idx_task_flows_goal ON task_flows(goal_id, updated_at_ms DESC);
                CREATE INDEX IF NOT EXISTS idx_goal_events_goal ON goal_events(goal_id, event_id DESC);
                """
            )

    def create_goal(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_id: str,
        objective: str,
        completion_criteria: list[str] | tuple[str, ...] | None = None,
        constraints: list[str] | tuple[str, ...] | None = None,
        workspace_ref: str = "",
        budget_policy: dict[str, Any] | None = None,
        permission_revision: str = "",
        model_profile_revision: str = "",
        extension_snapshot_digest: str = "",
        idempotency_key: str = "",
        created_by: str,
        correlation_id: str = "",
    ) -> tuple[Goal, TaskFlow]:
        """Create one Goal and its initial empty TaskFlow atomically."""
        normalized_session = str(session_id or "").strip()
        normalized_agent = str(agent_id or "").strip()
        normalized_user = str(user_id or "").strip()
        normalized_objective = str(objective or "").strip()
        if not all((normalized_session, normalized_agent, normalized_user, normalized_objective)):
            raise GoalStateError("session_id, agent_id, user_id, and objective are required")
        normalized_key = str(idempotency_key or "").strip()
        now_ms = _now_ms()
        goal_id = f"goal_{uuid.uuid4().hex[:20]}"
        flow_id = f"flow_{uuid.uuid4().hex[:20]}"
        with self._lock, _connect(self._db_path) as conn:
            if normalized_key:
                replay = conn.execute(
                    "SELECT * FROM goals WHERE idempotency_key = ?",
                    (normalized_key,),
                ).fetchone()
                if replay is not None:
                    replay_goal = _goal_from_row(replay)
                    if replay_goal.session_id != normalized_session or replay_goal.objective != normalized_objective:
                        raise GoalConflictError("idempotency key is already bound to a different Goal request")
                    replay_flow = conn.execute(
                        "SELECT * FROM task_flows WHERE flow_id = ?",
                        (replay_goal.active_flow_id,),
                    ).fetchone()
                    if replay_flow is None:
                        raise GoalStoreError("idempotent Goal is missing its TaskFlow")
                    return replay_goal, _flow_from_row(replay_flow)
            active = conn.execute(
                "SELECT goal_id FROM goals WHERE session_id = ? AND status IN ('active', 'waiting', 'paused', 'blocked') LIMIT 1",
                (normalized_session,),
            ).fetchone()
            if active is not None:
                raise GoalActiveExistsError("this Session already has an unfinished Goal")
            conn.execute(
                """
                INSERT INTO goals (
                    goal_id, session_id, agent_id, user_id, workspace_ref,
                    objective, completion_criteria_json, constraints_json,
                    status, revision, active_flow_id, budget_policy_json,
                    budget_state_json, permission_revision, model_profile_revision,
                    extension_snapshot_digest, completion_evidence_json,
                    idempotency_key, correlation_id, created_by, created_at_ms,
                    updated_at_ms, completed_at_ms, cancelled_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', 1, ?, ?, '{}', ?, ?, ?, '[]', ?, ?, ?, ?, ?, NULL, NULL)
                """,
                (
                    goal_id,
                    normalized_session,
                    normalized_agent,
                    normalized_user,
                    str(workspace_ref or "").strip(),
                    normalized_objective,
                    _json_dumps(_normalize_strings(completion_criteria)),
                    _json_dumps(_normalize_strings(constraints)),
                    flow_id,
                    _json_dumps(budget_policy or {}),
                    str(permission_revision or "").strip(),
                    str(model_profile_revision or "").strip(),
                    str(extension_snapshot_digest or "").strip(),
                    normalized_key,
                    str(correlation_id or "").strip(),
                    str(created_by or normalized_user).strip(),
                    now_ms,
                    now_ms,
                ),
            )
            conn.execute(
                """
                INSERT INTO task_flows (
                    flow_id, goal_id, status, revision, steps_json,
                    task_run_refs_json, artifact_refs_json, wait_reason_json,
                    recovery_state_json, last_event, created_at_ms, updated_at_ms
                ) VALUES (?, ?, 'active', 1, '[]', '[]', '[]', '{}', '{}', 'flow.created', ?, ?)
                """,
                (flow_id, goal_id, now_ms, now_ms),
            )
            self._append_event_conn(
                conn,
                goal_id=goal_id,
                flow_id=flow_id,
                event_type="goal.created",
                actor_id=str(created_by or normalized_user).strip(),
                correlation_id=str(correlation_id or "").strip(),
                payload={"objective": normalized_objective, "revision": 1},
                created_at_ms=now_ms,
            )
        goal = self.get_goal(goal_id)
        flow = self.get_flow(flow_id)
        assert goal is not None and flow is not None
        return goal, flow

    def get_goal(self, goal_id: str) -> Goal | None:
        """Return one Goal by immutable ID."""
        with _connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM goals WHERE goal_id = ?", (goal_id,)).fetchone()
        return _goal_from_row(row) if row is not None else None

    def current_goal(self, session_id: str) -> Goal | None:
        """Return the unfinished Goal for one Session, if present."""
        with _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM goals WHERE session_id = ? AND status IN ('active', 'waiting', 'paused', 'blocked') ORDER BY updated_at_ms DESC LIMIT 1",
                (session_id,),
            ).fetchone()
        return _goal_from_row(row) if row is not None else None

    def reconcile_runtime(self) -> list[Goal]:
        """Move orphaned active Goals to a resumable waiting state.

        ADK Runs are process-owned executors. After a Node restart, an active
        Goal whose TaskFlow has no persisted current Run cannot still be
        executing. Reconciliation preserves the Goal and all evidence while
        making that fact explicit instead of leaving a permanently active
        record that blocks the Session.

        A non-empty ``currentRunId`` is left unchanged because another runtime
        reconciliation layer may still know how to recover it. This store does
        not invent Run liveness or completion evidence.
        """
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM goals WHERE status = 'active' ORDER BY updated_at_ms ASC"
            ).fetchall()
        reconciled: list[Goal] = []
        reason = (
            "The Node restarted and no active ADK Run remains for this Goal. "
            "Use /goal resume to continue or /goal cancel before creating another Goal."
        )
        for row in rows:
            goal = _goal_from_row(row)
            flow = self.flow_for_goal(goal.goal_id)
            if flow is None:
                continue
            current_run_id = str(flow.recovery_state.get("currentRunId") or "").strip()
            if current_run_id:
                continue
            try:
                reconciled.append(
                    self.transition_goal(
                        goal.goal_id,
                        status="waiting",
                        expected_revision=goal.revision,
                        actor_id="system:startup-reconciliation",
                        reason=reason,
                        correlation_id=f"goal-reconcile:{goal.goal_id}",
                    )
                )
            except (GoalConflictError, GoalStateError):
                # A concurrent lifecycle transition won the race; its fact is
                # authoritative and must not be overwritten by startup repair.
                continue
        return reconciled

    def list_goals(
        self,
        *,
        session_id: str | None = None,
        user_id: str | None = None,
        statuses: list[str] | tuple[str, ...] | None = None,
        limit: int = 20,
    ) -> list[Goal]:
        """List Goals ordered by most recent update."""
        conditions: list[str] = []
        params: list[Any] = []
        if session_id:
            conditions.append("session_id = ?")
            params.append(session_id)
        if user_id:
            conditions.append("user_id = ?")
            params.append(user_id)
        if statuses:
            unknown = set(statuses) - set(_GOAL_TRANSITIONS)
            if unknown:
                raise GoalStateError("unsupported Goal status filter")
            conditions.append(f"status IN ({', '.join('?' for _ in statuses)})")
            params.extend(statuses)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        safe_limit = max(1, min(int(limit or 20), 200))
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                f"SELECT * FROM goals {where} ORDER BY updated_at_ms DESC LIMIT ?",
                (*params, safe_limit),
            ).fetchall()
        return [_goal_from_row(row) for row in rows]

    def update_goal(
        self,
        goal_id: str,
        *,
        objective: str | None = None,
        completion_criteria: list[str] | tuple[str, ...] | None = None,
        constraints: list[str] | tuple[str, ...] | None = None,
        budget_policy: dict[str, Any] | None = None,
        expected_revision: int,
        actor_id: str,
        correlation_id: str = "",
    ) -> Goal:
        """Edit mutable Goal policy fields using optimistic locking."""
        current = self._required_goal(goal_id)
        if current.status in GOAL_TERMINAL_STATUSES:
            raise GoalStateError("terminal Goals cannot be edited")
        updates: dict[str, Any] = {}
        if objective is not None:
            normalized = objective.strip()
            if not normalized:
                raise GoalStateError("objective cannot be empty")
            updates["objective"] = normalized
        if completion_criteria is not None:
            updates["completion_criteria_json"] = _json_dumps(_normalize_strings(completion_criteria))
        if constraints is not None:
            updates["constraints_json"] = _json_dumps(_normalize_strings(constraints))
        if budget_policy is not None:
            updates["budget_policy_json"] = _json_dumps(budget_policy)
        if not updates:
            return current
        updated = self._update_goal_row(
            current,
            updates,
            expected_revision=expected_revision,
            event_type="goal.updated",
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        return updated

    def transition_goal(
        self,
        goal_id: str,
        *,
        status: GoalStatus,
        expected_revision: int,
        actor_id: str,
        completion_evidence: list[dict[str, Any]] | tuple[dict[str, Any], ...] | None = None,
        user_confirmed: bool = False,
        reason: str = "",
        correlation_id: str = "",
    ) -> Goal:
        """Apply one explicit Goal lifecycle transition with evidence checks."""
        current = self._required_goal(goal_id)
        target = str(status)
        if target not in _GOAL_TRANSITIONS.get(current.status, frozenset()):
            raise GoalStateError(f"Goal cannot transition from {current.status!r} to {target!r}")
        evidence = [dict(item) for item in completion_evidence or () if isinstance(item, dict)]
        if target == "completed" and not evidence and not user_confirmed:
            raise GoalStateError("Goal completion requires evidence or explicit user confirmation")
        now_ms = _now_ms()
        updates: dict[str, Any] = {"status": target}
        if target == "completed":
            updates["completion_evidence_json"] = _json_dumps(
                evidence or [{"type": "user_confirmation", "ref": actor_id, "label": "User confirmed completion"}]
            )
            updates["completed_at_ms"] = now_ms
        if target == "cancelled":
            updates["cancelled_at_ms"] = now_ms
        updated = self._update_goal_row(
            current,
            updates,
            expected_revision=expected_revision,
            event_type=f"goal.{target}",
            actor_id=actor_id,
            correlation_id=correlation_id,
            event_payload={"from": current.status, "to": target, "reason": reason.strip()},
        )
        flow_status = {
            "active": "active",
            "waiting": "waiting",
            "paused": "paused",
            "blocked": "blocked",
            "completed": "completed",
            "cancelled": "cancelled",
            "failed": "failed",
        }.get(target)
        if flow_status is not None:
            flow = self.flow_for_goal(current.goal_id)
            if flow is not None and flow.status not in FLOW_TERMINAL_STATUSES:
                wait_reason = {}
                if target in {"waiting", "paused", "blocked"}:
                    wait_reason = {
                        "kind": target,
                        "message": reason.strip() or f"Goal entered {target} state.",
                    }
                self._update_flow_row(
                    flow,
                    {
                        "status": flow_status,
                        "wait_reason_json": _json_dumps(wait_reason),
                        "last_event": f"flow.{flow_status}",
                    },
                    expected_revision=flow.revision,
                    event_type=f"flow.{flow_status}",
                    actor_id=actor_id,
                    correlation_id=correlation_id,
                    event_payload={"reason": f"goal_{target}"},
                )
        return updated

    def wait_current_goal(
        self,
        session_id: str,
        *,
        reason: str,
        actor_id: str = "system:runtime",
        correlation_id: str = "",
    ) -> Goal | None:
        """Move an actively executing Goal to a resumable waiting state."""
        goal = self.current_goal(session_id)
        if goal is None or goal.status != "active":
            return goal
        return self.transition_goal(
            goal.goal_id,
            status="waiting",
            expected_revision=goal.revision,
            actor_id=actor_id,
            reason=str(reason or "Goal execution is waiting for another Run")[:2_000],
            correlation_id=correlation_id,
        )

    def pause_current_goal(
        self,
        session_id: str,
        *,
        reason: str,
        actor_id: str = "system:runtime",
        correlation_id: str = "",
    ) -> Goal | None:
        """Pause an active Goal when its current Run is cancelled directly."""
        goal = self.current_goal(session_id)
        if goal is None or goal.status != "active":
            return goal
        return self.transition_goal(
            goal.goal_id,
            status="paused",
            expected_revision=goal.revision,
            actor_id=actor_id,
            reason=str(reason or "The current Goal Run was cancelled")[:2_000],
            correlation_id=correlation_id,
        )

    def block_current_goal(
        self,
        session_id: str,
        *,
        reason: str,
        actor_id: str = "system:runtime",
        correlation_id: str = "",
    ) -> Goal | None:
        """Block the current Goal after its initial ADK Run cannot start."""
        goal = self.current_goal(session_id)
        if goal is None or goal.status not in {"active", "waiting"}:
            return goal
        return self.transition_goal(
            goal.goal_id,
            status="blocked",
            expected_revision=goal.revision,
            actor_id=actor_id,
            reason=str(reason or "ADK Run failed to start")[:2_000],
            correlation_id=correlation_id,
        )

    def request_completion(
        self,
        goal_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        invocation_id: str,
        completion_evidence: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
        correlation_id: str = "",
    ) -> TaskFlow:
        """Stage completion until the requesting ADK invocation succeeds.

        The Goal remains unfinished while its current Run is active. Runtime
        reconciliation consumes this request only after it persists a
        successful Run fact for the same ADK invocation.
        """
        goal = self._required_goal(goal_id)
        if expected_revision != goal.revision:
            raise GoalConflictError("Goal revision changed; refresh and retry")
        if goal.status not in GOAL_ACTIVE_STATUSES:
            raise GoalStateError("terminal Goals cannot request completion")
        normalized_invocation = str(invocation_id or "").strip()
        if not normalized_invocation:
            raise GoalStateError("Goal completion requires an ADK invocation identity")
        flow = self._required_flow(goal.active_flow_id)
        if flow.steps and any(step.get("status") != "completed" for step in flow.steps):
            raise GoalStateError("Goal completion requires every declared TaskFlow step to be completed")
        recovery = flow.recovery_state
        recovery["pendingCompletion"] = {
            "goalId": goal.goal_id,
            "invocationId": normalized_invocation,
            "requestedBy": str(actor_id or "system").strip(),
            "requestedAtMs": _now_ms(),
            "evidence": [dict(item) for item in completion_evidence if isinstance(item, dict)],
        }
        return self._update_flow_row(
            flow,
            {
                "recovery_state_json": _json_dumps(recovery),
                "last_event": "goal.completion.requested",
            },
            expected_revision=flow.revision,
            event_type="goal.completion.requested",
            actor_id=actor_id,
            correlation_id=correlation_id or normalized_invocation,
            event_payload={"invocationId": normalized_invocation},
        )

    def get_flow(self, flow_id: str) -> TaskFlow | None:
        """Return one TaskFlow by immutable ID."""
        with _connect(self._db_path) as conn:
            row = conn.execute("SELECT * FROM task_flows WHERE flow_id = ?", (flow_id,)).fetchone()
        return _flow_from_row(row) if row is not None else None

    def flow_for_goal(self, goal_id: str) -> TaskFlow | None:
        """Return the Goal's active TaskFlow."""
        goal = self.get_goal(goal_id)
        return self.get_flow(goal.active_flow_id) if goal is not None else None

    def list_flows(self, goal_id: str, *, limit: int = 20) -> list[TaskFlow]:
        """List TaskFlows for one Goal in most-recently-updated order."""
        safe_limit = max(1, min(int(limit or 20), 200))
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM task_flows WHERE goal_id = ? ORDER BY updated_at_ms DESC LIMIT ?",
                (goal_id, safe_limit),
            ).fetchall()
        return [_flow_from_row(row) for row in rows]

    def update_flow(
        self,
        flow_id: str,
        *,
        steps: list[dict[str, Any]] | tuple[dict[str, Any], ...],
        expected_revision: int,
        actor_id: str,
        correlation_id: str = "",
    ) -> TaskFlow:
        """Replace one non-terminal Flow plan after validating dependencies."""
        current = self._required_flow(flow_id)
        if current.status in FLOW_TERMINAL_STATUSES:
            raise GoalStateError("terminal TaskFlows cannot be edited")
        normalized = _normalize_steps(steps)
        return self._update_flow_row(
            current,
            {"steps_json": _json_dumps(normalized), "last_event": "flow.updated"},
            expected_revision=expected_revision,
            event_type="flow.updated",
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    def advance_flow_step(
        self,
        flow_id: str,
        *,
        step_id: str,
        status: str,
        expected_revision: int,
        actor_id: str,
        correlation_id: str = "",
    ) -> TaskFlow:
        """Advance one TaskFlow step while enforcing completed dependencies."""
        current = self._required_flow(flow_id)
        if current.status in FLOW_TERMINAL_STATUSES:
            raise GoalStateError("terminal TaskFlows cannot advance")
        target = str(status or "").strip()
        if target not in FLOW_STEP_STATUSES:
            raise GoalStateError("unsupported TaskFlow step status")
        steps = current.steps
        selected = next((step for step in steps if step.get("stepId") == step_id), None)
        if selected is None:
            raise GoalNotFoundError(f"TaskFlow step {step_id!r} was not found")
        if target in {"running", "completed"}:
            by_id = {str(step.get("stepId")): step for step in steps}
            incomplete = [
                dependency
                for dependency in selected.get("dependsOn", [])
                if by_id.get(str(dependency), {}).get("status") != "completed"
            ]
            if incomplete:
                raise GoalStateError("TaskFlow step dependencies are not complete")
        selected["status"] = target
        step_statuses = {str(step.get("status") or "pending") for step in steps}
        if "blocked" in step_statuses:
            flow_status = "blocked"
        elif "waiting" in step_statuses:
            flow_status = "waiting"
        else:
            # Advancing a previously waiting/blocked Flow must make it runnable
            # again. Terminal Flow state is guarded above.
            flow_status = "active"
        return self._update_flow_row(
            current,
            {
                "steps_json": _json_dumps(steps),
                "status": flow_status,
                "last_event": "flow.step.updated",
            },
            expected_revision=expected_revision,
            event_type="flow.step.updated",
            actor_id=actor_id,
            correlation_id=correlation_id,
            event_payload={"stepId": step_id, "status": target},
        )

    def bind_task(
        self,
        flow_id: str,
        *,
        step_id: str,
        task_id: str,
        expected_revision: int,
        actor_id: str,
        correlation_id: str = "",
    ) -> TaskFlow:
        """Bind an existing TaskRun fact to one TaskFlow step idempotently."""
        current = self._required_flow(flow_id)
        normalized_task = str(task_id or "").strip()
        if not normalized_task:
            raise GoalStateError("task_id is required")
        if not any(step.get("stepId") == step_id for step in current.steps):
            raise GoalNotFoundError(f"TaskFlow step {step_id!r} was not found")
        refs = current.task_run_refs
        if not any(ref.get("taskId") == normalized_task for ref in refs):
            refs.append({"stepId": step_id, "taskId": normalized_task})
        return self._update_flow_row(
            current,
            {"task_run_refs_json": _json_dumps(refs), "last_event": "flow.task.bound"},
            expected_revision=expected_revision,
            event_type="flow.task.bound",
            actor_id=actor_id,
            correlation_id=correlation_id,
            event_payload={"stepId": step_id, "taskId": normalized_task},
        )

    def record_run_fact(
        self,
        *,
        session_id: str,
        run_id: str,
        status: str,
        actor_id: str = "system:runtime",
        correlation_id: str = "",
        snapshot: dict[str, Any] | None = None,
        invocation_id: str = "",
    ) -> TaskFlow | None:
        """Attach one ADK Run lifecycle fact to the current Session Goal.

        A successful Run is not sufficient by itself to complete a Goal. The
        only terminal reconciliation performed here consumes an explicit
        ``request_completion`` fact from the same ADK invocation.
        """
        normalized_session = str(session_id or "").strip()
        normalized_run = str(run_id or "").strip()
        normalized_status = str(status or "").strip()
        if not normalized_session or not normalized_run or not normalized_status:
            raise GoalStateError("session_id, run_id, and status are required")
        goal = self.current_goal(normalized_session)
        if goal is None:
            return None
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM task_flows WHERE flow_id = ?",
                (goal.active_flow_id,),
            ).fetchone()
            if row is None:
                raise GoalNotFoundError("current Goal is missing its active TaskFlow")
            current = _flow_from_row(row)
            recovery = current.recovery_state
            runs = [item for item in recovery.get("runs", []) if isinstance(item, dict)]
            run_fact = next((item for item in runs if item.get("runId") == normalized_run), None)
            payload = {
                "runId": normalized_run,
                "status": normalized_status,
                **({"invocationId": invocation_id} if str(invocation_id or "").strip() else {}),
                **(dict(snapshot or {})),
            }
            if run_fact is None:
                runs.append(payload)
            else:
                run_fact.update(payload)
            recovery.update(
                {
                    "currentRunId": normalized_run if normalized_status in {"queued", "running", "cancelling"} else "",
                    "latestRunId": normalized_run,
                    "latestRunStatus": normalized_status,
                    "runs": runs[-20:],
                }
            )
            pending = recovery.get("pendingCompletion")
            pending_matches = (
                isinstance(pending, dict)
                and str(pending.get("invocationId") or "").strip()
                and str(pending.get("invocationId") or "").strip()
                == str(invocation_id or "").strip()
            )
            completion_evidence: list[dict[str, Any]] | None = None
            completion_actor = actor_id
            if pending_matches and normalized_status == "completed":
                completion_evidence = [
                    dict(item)
                    for item in pending.get("evidence", [])
                    if isinstance(item, dict)
                ]
                completion_evidence.append(
                    {
                        "type": "run",
                        "ref": normalized_run,
                        "label": "Completed ADK Run",
                        "invocationId": str(invocation_id or "").strip(),
                    }
                )
                completion_actor = str(pending.get("requestedBy") or actor_id).strip()
                recovery.pop("pendingCompletion", None)
                recovery["lastCompletionRequest"] = {
                    "invocationId": str(invocation_id or "").strip(),
                    "runId": normalized_run,
                    "status": "completed",
                }
            elif pending_matches and normalized_status in {"failed", "cancelled"}:
                recovery.pop("pendingCompletion", None)
                recovery["lastCompletionRequest"] = {
                    "invocationId": str(invocation_id or "").strip(),
                    "runId": normalized_run,
                    "status": "rejected",
                    "runStatus": normalized_status,
                }
            now_ms = _now_ms()
            conn.execute(
                """
                UPDATE task_flows
                SET recovery_state_json = ?, last_event = ?, revision = revision + 1, updated_at_ms = ?
                WHERE flow_id = ?
                """,
                (
                    _json_dumps(recovery),
                    "flow.completed" if completion_evidence is not None else f"run.{normalized_status}",
                    now_ms,
                    current.flow_id,
                ),
            )
            self._append_event_conn(
                conn,
                goal_id=goal.goal_id,
                flow_id=current.flow_id,
                event_type=f"run.{normalized_status}",
                actor_id=actor_id,
                correlation_id=correlation_id or goal.correlation_id,
                payload=payload,
                created_at_ms=now_ms,
            )
            if completion_evidence is not None:
                conn.execute(
                    """
                    UPDATE task_flows
                    SET status = 'completed'
                    WHERE flow_id = ?
                    """,
                    (current.flow_id,),
                )
                cursor = conn.execute(
                    """
                    UPDATE goals
                    SET status = 'completed', completion_evidence_json = ?,
                        completed_at_ms = ?, updated_at_ms = ?, revision = revision + 1
                    WHERE goal_id = ? AND status IN ('active', 'waiting', 'paused', 'blocked')
                    """,
                    (_json_dumps(completion_evidence), now_ms, now_ms, goal.goal_id),
                )
                if cursor.rowcount == 0:
                    raise GoalConflictError("Goal changed before Run completion could be reconciled")
                self._append_event_conn(
                    conn,
                    goal_id=goal.goal_id,
                    flow_id=current.flow_id,
                    event_type="goal.completed",
                    actor_id=completion_actor,
                    correlation_id=correlation_id or str(invocation_id or "").strip(),
                    payload={
                        "from": goal.status,
                        "to": "completed",
                        "reason": "explicit_agent_completion_after_successful_run",
                        "runId": normalized_run,
                    },
                    created_at_ms=now_ms,
                )
        return self._required_flow(goal.active_flow_id)

    def record_continuation_fact(
        self,
        *,
        session_id: str,
        run_id: str,
        continuation_index: int,
        max_continuations: int,
        max_llm_calls_per_invocation: int,
        exhausted: bool = False,
        actor_id: str = "system:runtime",
        correlation_id: str = "",
    ) -> Goal | None:
        """Persist one bounded ADK continuation and its accumulated budget.

        A continuation is an execution fact, not a Goal lifecycle transition.
        Recording it therefore keeps the Goal active while making restart and
        diagnostics decisions deterministic.
        """
        goal = self.current_goal(str(session_id or "").strip())
        if goal is None:
            return None
        index = max(0, int(continuation_index))
        limit = max(0, int(max_continuations))
        llm_limit = max(1, int(max_llm_calls_per_invocation))
        now_ms = _now_ms()
        event_type = "goal.continuation.exhausted" if exhausted else "goal.continuation.started"
        budget_state = goal.budget_state
        budget_state.update(
            {
                "continuationCount": index,
                "maxContinuations": limit,
                "maxLlmCallsPerInvocation": llm_limit,
                "continuationExhausted": bool(exhausted),
                "lastContinuationAtMs": now_ms,
                "latestRunId": str(run_id or "").strip(),
            }
        )
        payload = {
            "runId": str(run_id or "").strip(),
            "continuationIndex": index,
            "maxContinuations": limit,
            "maxLlmCallsPerInvocation": llm_limit,
            "exhausted": bool(exhausted),
        }
        with self._lock, _connect(self._db_path) as conn:
            conn.execute(
                """
                UPDATE goals
                SET budget_state_json = ?, updated_at_ms = ?, revision = revision + 1
                WHERE goal_id = ?
                """,
                (_json_dumps(budget_state), now_ms, goal.goal_id),
            )
            flow_row = conn.execute(
                "SELECT * FROM task_flows WHERE flow_id = ?",
                (goal.active_flow_id,),
            ).fetchone()
            if flow_row is None:
                raise GoalNotFoundError("current Goal is missing its active TaskFlow")
            flow = _flow_from_row(flow_row)
            recovery = flow.recovery_state
            continuations = [
                item for item in recovery.get("continuations", []) if isinstance(item, dict)
            ]
            continuations.append({**payload, "createdAtMs": now_ms})
            recovery["continuations"] = continuations[-20:]
            conn.execute(
                """
                UPDATE task_flows
                SET recovery_state_json = ?, last_event = ?,
                    updated_at_ms = ?, revision = revision + 1
                WHERE flow_id = ?
                """,
                (_json_dumps(recovery), event_type, now_ms, flow.flow_id),
            )
            self._append_event_conn(
                conn,
                goal_id=goal.goal_id,
                flow_id=flow.flow_id,
                event_type=event_type,
                actor_id=actor_id,
                correlation_id=correlation_id or str(run_id or "").strip(),
                payload=payload,
                created_at_ms=now_ms,
            )
        return self._required_goal(goal.goal_id)

    def record_artifact_fact(
        self,
        *,
        session_id: str,
        run_id: str,
        artifact_ref: str,
        version: int | None = None,
        actor_id: str = "system:artifact",
        correlation_id: str = "",
    ) -> TaskFlow | None:
        """Attach one ADK Artifact reference to the current Session Goal."""
        normalized_ref = str(artifact_ref or "").strip()
        if not normalized_ref:
            raise GoalStateError("artifact_ref is required")
        goal = self.current_goal(str(session_id or "").strip())
        if goal is None:
            return None
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                "SELECT * FROM task_flows WHERE flow_id = ?",
                (goal.active_flow_id,),
            ).fetchone()
            if row is None:
                raise GoalNotFoundError("current Goal is missing its active TaskFlow")
            current = _flow_from_row(row)
            refs = current.artifact_refs
            payload: dict[str, Any] = {
                "artifactId": normalized_ref,
                "ref": normalized_ref,
                "runId": str(run_id or "").strip(),
            }
            if version is not None:
                payload["version"] = int(version)
            if not any(
                item.get("artifactId") == normalized_ref and item.get("version") == payload.get("version")
                for item in refs
            ):
                refs.append(payload)
            now_ms = _now_ms()
            conn.execute(
                """
                UPDATE task_flows
                SET artifact_refs_json = ?, last_event = 'artifact.saved',
                    revision = revision + 1, updated_at_ms = ?
                WHERE flow_id = ?
                """,
                (_json_dumps(refs), now_ms, current.flow_id),
            )
            self._append_event_conn(
                conn,
                goal_id=goal.goal_id,
                flow_id=current.flow_id,
                event_type="artifact.saved",
                actor_id=actor_id,
                correlation_id=correlation_id or goal.correlation_id,
                payload=payload,
                created_at_ms=now_ms,
            )
        return self._required_flow(goal.active_flow_id)

    def finish_flow(
        self,
        flow_id: str,
        *,
        expected_revision: int,
        actor_id: str,
        correlation_id: str = "",
    ) -> TaskFlow:
        """Mark a TaskFlow complete only after every declared step completes."""
        current = self._required_flow(flow_id)
        if not current.steps or any(step.get("status") != "completed" for step in current.steps):
            raise GoalStateError("TaskFlow completion requires all declared steps to be completed")
        return self._update_flow_row(
            current,
            {"status": "completed", "last_event": "flow.completed"},
            expected_revision=expected_revision,
            event_type="flow.completed",
            actor_id=actor_id,
            correlation_id=correlation_id,
        )

    def list_events(self, goal_id: str, *, limit: int = 100) -> list[GoalEvent]:
        """List the most recent Goal events in chronological order."""
        safe_limit = max(1, min(int(limit or 100), 500))
        with _connect(self._db_path) as conn:
            rows = conn.execute(
                "SELECT * FROM goal_events WHERE goal_id = ? ORDER BY event_id DESC LIMIT ?",
                (goal_id, safe_limit),
            ).fetchall()
        return [_event_from_row(row) for row in reversed(rows)]

    def _required_goal(self, goal_id: str) -> Goal:
        """Return one Goal or raise a stable not-found error."""
        goal = self.get_goal(goal_id)
        if goal is None:
            raise GoalNotFoundError(f"Goal {goal_id!r} was not found")
        return goal

    def _required_flow(self, flow_id: str) -> TaskFlow:
        """Return one TaskFlow or raise a stable not-found error."""
        flow = self.get_flow(flow_id)
        if flow is None:
            raise GoalNotFoundError(f"TaskFlow {flow_id!r} was not found")
        return flow

    def _update_goal_row(
        self,
        current: Goal,
        updates: dict[str, Any],
        *,
        expected_revision: int,
        event_type: str,
        actor_id: str,
        correlation_id: str,
        event_payload: dict[str, Any] | None = None,
    ) -> Goal:
        """Update one Goal and append its event in the same transaction."""
        if expected_revision != current.revision:
            raise GoalConflictError("Goal revision changed; refresh and retry")
        now_ms = _now_ms()
        updates = {**updates, "updated_at_ms": now_ms}
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._lock, _connect(self._db_path) as conn:
            cursor = conn.execute(
                f"UPDATE goals SET {assignments}, revision = revision + 1 WHERE goal_id = ? AND revision = ?",
                (*updates.values(), current.goal_id, expected_revision),
            )
            if cursor.rowcount == 0:
                raise GoalConflictError("Goal revision changed; refresh and retry")
            self._append_event_conn(
                conn,
                goal_id=current.goal_id,
                flow_id=current.active_flow_id,
                event_type=event_type,
                actor_id=actor_id,
                correlation_id=correlation_id,
                payload=event_payload or {"revision": expected_revision + 1},
                created_at_ms=now_ms,
            )
        return self._required_goal(current.goal_id)

    def _update_flow_row(
        self,
        current: TaskFlow,
        updates: dict[str, Any],
        *,
        expected_revision: int,
        event_type: str,
        actor_id: str,
        correlation_id: str,
        event_payload: dict[str, Any] | None = None,
    ) -> TaskFlow:
        """Update one TaskFlow and append its Goal event atomically."""
        if expected_revision != current.revision:
            raise GoalConflictError("TaskFlow revision changed; refresh and retry")
        now_ms = _now_ms()
        updates = {**updates, "updated_at_ms": now_ms}
        assignments = ", ".join(f"{key} = ?" for key in updates)
        with self._lock, _connect(self._db_path) as conn:
            cursor = conn.execute(
                f"UPDATE task_flows SET {assignments}, revision = revision + 1 WHERE flow_id = ? AND revision = ?",
                (*updates.values(), current.flow_id, expected_revision),
            )
            if cursor.rowcount == 0:
                raise GoalConflictError("TaskFlow revision changed; refresh and retry")
            self._append_event_conn(
                conn,
                goal_id=current.goal_id,
                flow_id=current.flow_id,
                event_type=event_type,
                actor_id=actor_id,
                correlation_id=correlation_id,
                payload=event_payload or {"revision": expected_revision + 1},
                created_at_ms=now_ms,
            )
        return self._required_flow(current.flow_id)

    @staticmethod
    def _append_event_conn(
        conn: sqlite3.Connection,
        *,
        goal_id: str,
        flow_id: str,
        event_type: str,
        actor_id: str,
        correlation_id: str,
        payload: dict[str, Any],
        created_at_ms: int,
    ) -> None:
        """Append one Goal event using the caller's transaction."""
        conn.execute(
            """
            INSERT INTO goal_events (
                goal_id, flow_id, event_type, actor_id, correlation_id,
                payload_json, created_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                goal_id,
                flow_id,
                event_type,
                str(actor_id or "system").strip(),
                str(correlation_id or "").strip(),
                _json_dumps(payload),
                created_at_ms,
            ),
        )


__all__ = [
    "FLOW_STEP_STATUSES",
    "GOAL_ACTIVE_STATUSES",
    "GOAL_TERMINAL_STATUSES",
    "Goal",
    "GoalActiveExistsError",
    "GoalConflictError",
    "GoalEvent",
    "GoalNotFoundError",
    "GoalStateError",
    "GoalStore",
    "GoalStoreError",
    "TaskFlow",
    "goal_db_path",
]
