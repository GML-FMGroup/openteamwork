"""SQLite-backed agent ownership and membership store."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..product import PRODUCT
from .identity_store import load_identity_store_config


@dataclass(slots=True)
class AgentAccessStoreConfig:
    """Runtime configuration for the agent access store."""

    db_path: str


@dataclass(slots=True)
class AgentRecord:
    """One persistent agent ownership record."""

    agent_id: str
    name: str = ""
    privilege_level: str = "low"
    owner_principal_id: str = ""
    status: str = "active"
    config_ref: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class AgentMembership:
    """One principal-to-agent relation row."""

    agent_id: str
    principal_id: str
    relation: str
    metadata: dict[str, Any] = field(default_factory=dict)
    joined_at_ms: int = 0


@dataclass(slots=True)
class AgentAccessAuditRow:
    """One persisted audit row for an explicit access-management mutation."""

    audit_id: str
    agent_id: str
    actor_principal_id: str
    actor_relation: str
    action: str
    target_principal_id: str
    details: dict[str, Any] = field(default_factory=dict)
    created_at_ms: int = 0


def load_agent_access_store_config() -> AgentAccessStoreConfig:
    """Load access store config from environment variables."""
    default_db_path = load_identity_store_config().db_path
    db_path = os.getenv("OPENPPX_ACCESS_DB_PATH", "").strip() or default_db_path
    return AgentAccessStoreConfig(db_path=db_path)


def _prepare_db_path(db_path: Path) -> Path:
    """Return a writable SQLite path, falling back to workspace-local storage."""
    candidate = db_path.expanduser().resolve(strict=False)
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except PermissionError:
        return _workspace_fallback_db_path(candidate)


def _connect(db_path: Path) -> sqlite3.Connection:
    """Open one SQLite connection with pragmatic defaults."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def _workspace_fallback_db_path(db_path: Path) -> Path:
    """Return the workspace-local fallback path for one SQLite database."""
    fallback = (Path.cwd() / PRODUCT.workspace_state_directory / "database" / db_path.name).resolve(strict=False)
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback


def _now_ms() -> int:
    """Return current wall-clock milliseconds."""
    return int(time.time() * 1000)


def _new_audit_id() -> str:
    """Return one opaque audit id for a persisted mutation row."""
    return f"audit_{_now_ms()}_{os.urandom(4).hex()}"


def _json_dumps(payload: dict[str, Any]) -> str:
    """Serialize a JSON object for SQLite storage."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def _json_loads(raw: str | None) -> dict[str, Any]:
    """Deserialize stored JSON metadata into a dict."""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


class AgentAccessStore:
    """Persist lightweight agent ownership and membership relationships."""

    def __init__(self, *, db_path: str | Path | None = None):
        cfg = (
            load_agent_access_store_config()
            if db_path is None
            else AgentAccessStoreConfig(db_path=str(db_path))
        )
        self._db_path = _prepare_db_path(Path(cfg.db_path))
        self._lock = threading.Lock()
        try:
            self._ensure_schema()
        except sqlite3.OperationalError:
            fallback = _workspace_fallback_db_path(self._db_path)
            if fallback == self._db_path:
                raise
            self._db_path = fallback
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create agent access tables when missing."""
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_records (
                    agent_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    privilege_level TEXT NOT NULL,
                    owner_principal_id TEXT NOT NULL,
                    status TEXT NOT NULL,
                    config_ref TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_memberships (
                    agent_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    joined_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (agent_id, principal_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_memberships_agent_relation "
                "ON agent_memberships(agent_id, relation)"
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS agent_access_audit (
                    audit_id TEXT PRIMARY KEY,
                    agent_id TEXT NOT NULL,
                    actor_principal_id TEXT NOT NULL,
                    actor_relation TEXT NOT NULL,
                    action TEXT NOT NULL,
                    target_principal_id TEXT NOT NULL,
                    details_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_agent_access_audit_agent_created "
                "ON agent_access_audit(agent_id, created_at_ms DESC)"
            )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> AgentRecord:
        """Project one SQLite row into an agent record."""
        return AgentRecord(
            agent_id=str(row["agent_id"]),
            name=str(row["name"]),
            privilege_level=str(row["privilege_level"]),
            owner_principal_id=str(row["owner_principal_id"]),
            status=str(row["status"]),
            config_ref=str(row["config_ref"]),
            metadata=_json_loads(row["metadata_json"]),
        )

    @staticmethod
    def _membership_from_row(row: sqlite3.Row) -> AgentMembership:
        """Project one SQLite row into an agent membership."""
        return AgentMembership(
            agent_id=str(row["agent_id"]),
            principal_id=str(row["principal_id"]),
            relation=str(row["relation"]),
            metadata=_json_loads(row["metadata_json"]),
            joined_at_ms=int(row["joined_at_ms"]),
        )

    @staticmethod
    def _audit_row_from_row(row: sqlite3.Row) -> AgentAccessAuditRow:
        """Project one SQLite row into an access-audit row."""
        return AgentAccessAuditRow(
            audit_id=str(row["audit_id"]),
            agent_id=str(row["agent_id"]),
            actor_principal_id=str(row["actor_principal_id"]),
            actor_relation=str(row["actor_relation"]),
            action=str(row["action"]),
            target_principal_id=str(row["target_principal_id"]),
            details=_json_loads(row["details_json"]),
            created_at_ms=int(row["created_at_ms"]),
        )

    def upsert_agent_record(self, record: AgentRecord) -> AgentRecord:
        """Insert or update one agent ownership record."""
        now_ms = _now_ms()
        payload = AgentRecord(
            agent_id=str(record.agent_id),
            name=str(record.name or record.agent_id),
            privilege_level=str(record.privilege_level or "low"),
            owner_principal_id=str(record.owner_principal_id or ""),
            status=str(record.status or "active"),
            config_ref=str(record.config_ref or ""),
            metadata=dict(record.metadata),
        )
        with self._lock, _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO agent_records (
                    agent_id,
                    name,
                    privilege_level,
                    owner_principal_id,
                    status,
                    config_ref,
                    metadata_json,
                    created_at_ms,
                    updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id) DO UPDATE SET
                    name=excluded.name,
                    privilege_level=excluded.privilege_level,
                    owner_principal_id=excluded.owner_principal_id,
                    status=excluded.status,
                    config_ref=excluded.config_ref,
                    metadata_json=excluded.metadata_json,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    payload.agent_id,
                    payload.name,
                    payload.privilege_level,
                    payload.owner_principal_id,
                    payload.status,
                    payload.config_ref,
                    _json_dumps(payload.metadata),
                    now_ms,
                    now_ms,
                ),
            )
        return payload

    def set_agent_owner(
        self,
        *,
        agent_id: str,
        owner_principal_id: str,
        name: str | None = None,
        privilege_level: str = "low",
        status: str = "active",
        config_ref: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> AgentRecord:
        """Upsert one agent record with a canonical owner."""
        return self.upsert_agent_record(
            AgentRecord(
                agent_id=agent_id,
                name=name or agent_id,
                privilege_level=privilege_level,
                owner_principal_id=owner_principal_id,
                status=status,
                config_ref=config_ref,
                metadata=dict(metadata or {}),
            )
        )

    def get_agent_record(self, agent_id: str) -> AgentRecord | None:
        """Return the agent record for one agent id."""
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    agent_id,
                    name,
                    privilege_level,
                    owner_principal_id,
                    status,
                    config_ref,
                    metadata_json
                FROM agent_records
                WHERE agent_id = ?
                """,
                (agent_id,),
            ).fetchone()
        if row is None:
            return None
        return self._record_from_row(row)

    def list_agent_records(self) -> list[AgentRecord]:
        """Return all Agent records in deterministic immutable-id order."""
        with self._lock, _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    agent_id,
                    name,
                    privilege_level,
                    owner_principal_id,
                    status,
                    config_ref,
                    metadata_json
                FROM agent_records
                ORDER BY agent_id ASC
                """
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def upsert_membership(self, membership: AgentMembership) -> AgentMembership:
        """Insert or update one principal membership for an agent."""
        now_ms = membership.joined_at_ms or _now_ms()
        payload = AgentMembership(
            agent_id=str(membership.agent_id),
            principal_id=str(membership.principal_id),
            relation=str(membership.relation or "participant"),
            metadata=dict(membership.metadata),
            joined_at_ms=now_ms,
        )
        with self._lock, _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO agent_records (
                    agent_id,
                    name,
                    privilege_level,
                    owner_principal_id,
                    status,
                    config_ref,
                    metadata_json,
                    created_at_ms,
                    updated_at_ms
                )
                SELECT ?, ?, ?, '', 'active', '', '{}', ?, ?
                WHERE NOT EXISTS (
                    SELECT 1 FROM agent_records WHERE agent_id = ?
                )
                """,
                (
                    payload.agent_id,
                    payload.agent_id,
                    "low",
                    now_ms,
                    now_ms,
                    payload.agent_id,
                ),
            )
            conn.execute(
                """
                INSERT INTO agent_memberships (
                    agent_id,
                    principal_id,
                    relation,
                    metadata_json,
                    joined_at_ms,
                    updated_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(agent_id, principal_id) DO UPDATE SET
                    relation=excluded.relation,
                    metadata_json=excluded.metadata_json,
                    updated_at_ms=excluded.updated_at_ms
                """,
                (
                    payload.agent_id,
                    payload.principal_id,
                    payload.relation,
                    _json_dumps(payload.metadata),
                    payload.joined_at_ms,
                    _now_ms(),
                ),
            )
        return payload

    def get_membership(self, *, agent_id: str, principal_id: str) -> AgentMembership | None:
        """Return one membership row for the agent/principal pair."""
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    agent_id,
                    principal_id,
                    relation,
                    metadata_json,
                    joined_at_ms
                FROM agent_memberships
                WHERE agent_id = ? AND principal_id = ?
                """,
                (agent_id, principal_id),
            ).fetchone()
        if row is None:
            return None
        return self._membership_from_row(row)

    def list_memberships(
        self,
        *,
        agent_id: str,
        relations: Sequence[str] | None = None,
    ) -> list[AgentMembership]:
        """List memberships for one agent, optionally filtered by relation."""
        query = """
            SELECT
                agent_id,
                principal_id,
                relation,
                metadata_json,
                joined_at_ms
            FROM agent_memberships
            WHERE agent_id = ?
        """
        params: list[Any] = [agent_id]
        normalized_relations = [str(item) for item in (relations or []) if str(item).strip()]
        if normalized_relations:
            placeholders = ", ".join("?" for _ in normalized_relations)
            query += f" AND relation IN ({placeholders})"
            params.extend(normalized_relations)
        query += " ORDER BY principal_id ASC"
        with self._lock, _connect(self._db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._membership_from_row(row) for row in rows]

    def delete_membership(self, *, agent_id: str, principal_id: str) -> bool:
        """Delete one membership row and return whether anything changed."""
        with self._lock, _connect(self._db_path) as conn:
            cursor = conn.execute(
                """
                DELETE FROM agent_memberships
                WHERE agent_id = ? AND principal_id = ?
                """,
                (agent_id, principal_id),
            )
        return cursor.rowcount > 0

    def record_audit(
        self,
        *,
        agent_id: str,
        actor_principal_id: str,
        actor_relation: str,
        action: str,
        target_principal_id: str,
        details: dict[str, Any] | None = None,
        created_at_ms: int = 0,
    ) -> AgentAccessAuditRow:
        """Persist one explicit access-management mutation audit row."""
        payload = AgentAccessAuditRow(
            audit_id=_new_audit_id(),
            agent_id=str(agent_id),
            actor_principal_id=str(actor_principal_id),
            actor_relation=str(actor_relation or "none"),
            action=str(action),
            target_principal_id=str(target_principal_id or ""),
            details=dict(details or {}),
            created_at_ms=int(created_at_ms or _now_ms()),
        )
        with self._lock, _connect(self._db_path) as conn:
            conn.execute(
                """
                INSERT INTO agent_access_audit (
                    audit_id,
                    agent_id,
                    actor_principal_id,
                    actor_relation,
                    action,
                    target_principal_id,
                    details_json,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.audit_id,
                    payload.agent_id,
                    payload.actor_principal_id,
                    payload.actor_relation,
                    payload.action,
                    payload.target_principal_id,
                    _json_dumps(payload.details),
                    payload.created_at_ms,
                ),
            )
        return payload

    def list_audit(
        self,
        *,
        agent_id: str,
        limit: int = 50,
        actions: Sequence[str] | None = None,
    ) -> list[AgentAccessAuditRow]:
        """List recent access-management audit rows for one agent."""
        normalized_limit = max(1, int(limit or 50))
        query = """
            SELECT
                audit_id,
                agent_id,
                actor_principal_id,
                actor_relation,
                action,
                target_principal_id,
                details_json,
                created_at_ms
            FROM agent_access_audit
            WHERE agent_id = ?
        """
        params: list[Any] = [agent_id]
        normalized_actions = [str(item) for item in (actions or []) if str(item).strip()]
        if normalized_actions:
            placeholders = ", ".join("?" for _ in normalized_actions)
            query += f" AND action IN ({placeholders})"
            params.extend(normalized_actions)
        query += " ORDER BY created_at_ms DESC, audit_id DESC LIMIT ?"
        params.append(normalized_limit)
        with self._lock, _connect(self._db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._audit_row_from_row(row) for row in rows]


def create_agent_access_store(config: AgentAccessStoreConfig | None = None) -> AgentAccessStore:
    """Create the runtime agent access store."""
    if config is None:
        return AgentAccessStore()
    return AgentAccessStore(db_path=config.db_path)
