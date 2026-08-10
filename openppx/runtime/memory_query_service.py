"""Explicit multi-principal memory query service with audit logging."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.memory.memory_entry import MemoryEntry

from ..product import PRODUCT
from .access_decision import AccessDecision
from .access_policy import AccessPolicy
from .identity_store import IdentityStore, create_identity_store
from .memory_service import create_memory_service, load_memory_config
from .memory_shared import memory_entry_text


@dataclass(slots=True)
class MemoryQueryResult:
    """Structured result for one explicit memory query."""

    decision: AccessDecision
    memories: list[MemoryEntry]


@dataclass(slots=True)
class MemoryAuditResult:
    """Structured result for one explicit memory-audit query."""

    decision: AccessDecision
    rows: list[dict[str, Any]]


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


def _json_dumps(payload: dict[str, Any]) -> str:
    """Serialize a JSON object for SQLite storage."""
    return json.dumps(payload, ensure_ascii=False, default=str)


class MemoryQueryService:
    """Run explicit owner/root memory queries on top of ADK memory backends."""

    def __init__(
        self,
        *,
        app_name: str = "openppx",
        identity_store: IdentityStore | None = None,
        access_policy: AccessPolicy | None = None,
        memory_service: Any | None = None,
        audit_db_path: str | Path | None = None,
    ) -> None:
        self._app_name = app_name
        self._identity_store = identity_store or create_identity_store()
        self._access_policy = access_policy or AccessPolicy(identity_store=self._identity_store)
        self._memory_service = memory_service if memory_service is not None else create_memory_service()
        config = load_memory_config()
        default_db_path = audit_db_path or config.sqlite_db_path
        self._audit_db_path = _prepare_db_path(Path(str(default_db_path)))
        self._lock = threading.Lock()
        try:
            self._ensure_schema()
        except sqlite3.OperationalError:
            fallback = _workspace_fallback_db_path(self._audit_db_path)
            if fallback == self._audit_db_path:
                raise
            self._audit_db_path = fallback
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create audit table when missing."""
        with _connect(self._audit_db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS memory_access_audit (
                    audit_id TEXT PRIMARY KEY,
                    requester_principal_id TEXT NOT NULL,
                    requester_principal_type TEXT NOT NULL,
                    requester_level TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    target_scope TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    result_count INTEGER NOT NULL,
                    access_kind TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_memory_access_audit_scope "
                "ON memory_access_audit(agent_id, requester_principal_id, created_at_ms DESC)"
            )

    def _write_audit(
        self,
        *,
        requester_principal_id: str,
        agent_id: str,
        query_text: str,
        result_count: int,
        decision: AccessDecision,
    ) -> None:
        """Persist one explicit query audit row."""
        principal = self._identity_store.get_principal(requester_principal_id)
        requester_type = principal.principal_type if principal is not None else "unknown"
        requester_level = principal.privilege_level if principal is not None else "unknown"
        if decision.scope_kind == "all":
            target_scope = "all"
        elif decision.scope_kind == "none":
            target_scope = "none"
        else:
            target_scope = ",".join(sorted(decision.visible_principal_ids))
        with self._lock, _connect(self._audit_db_path) as conn:
            conn.execute(
                """
                INSERT INTO memory_access_audit (
                    audit_id,
                    requester_principal_id,
                    requester_principal_type,
                    requester_level,
                    agent_id,
                    target_scope,
                    query_text,
                    result_count,
                    access_kind,
                    reason,
                    created_at_ms
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    requester_principal_id,
                    requester_type,
                    requester_level,
                    agent_id,
                    target_scope,
                    query_text,
                    result_count,
                    decision.access_kind,
                    decision.reason,
                    _now_ms(),
                ),
            )

    def _annotate_memory(self, *, memory: MemoryEntry, agent_id: str, subject_principal_id: str) -> MemoryEntry:
        """Attach subject scope metadata to one returned memory entry."""
        metadata = dict(memory.custom_metadata)
        metadata.setdefault("subject_principal_id", subject_principal_id)
        metadata.setdefault("agent_id", agent_id)
        return MemoryEntry(
            id=memory.id,
            author=memory.author,
            timestamp=memory.timestamp,
            content=memory.content,
            custom_metadata=metadata,
        )

    async def search(
        self,
        *,
        agent_id: str,
        requester_principal_id: str,
        query: str,
        limit: int = 20,
    ) -> MemoryQueryResult:
        """Search visible memories for the requester and write one audit record."""
        decision = self._access_policy.decide_agent_scope(
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
            access_kind="memory_query",
        )
        if not decision.allow or self._memory_service is None:
            self._write_audit(
                requester_principal_id=requester_principal_id,
                agent_id=agent_id,
                query_text=query,
                result_count=0,
                decision=decision,
            )
            return MemoryQueryResult(decision=decision, memories=[])

        visible_principal_ids = decision.resolved_scope(self._identity_store.list_principal_ids())
        combined: list[MemoryEntry] = []
        seen_keys: set[tuple[str, str, str]] = set()

        for subject_principal_id in visible_principal_ids:
            response = await self._memory_service.search_memory(
                app_name=self._app_name,
                user_id=subject_principal_id,
                query=query,
            )
            for memory in response.memories:
                annotated = self._annotate_memory(
                    memory=memory,
                    agent_id=agent_id,
                    subject_principal_id=subject_principal_id,
                )
                dedupe_key = (
                    subject_principal_id,
                    str(annotated.id or ""),
                    memory_entry_text(annotated),
                )
                if dedupe_key in seen_keys:
                    continue
                seen_keys.add(dedupe_key)
                combined.append(annotated)

        combined.sort(key=lambda item: str(item.timestamp or ""), reverse=True)
        limited = combined[: max(int(limit), 0)]
        self._write_audit(
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
            query_text=query,
            result_count=len(limited),
            decision=decision,
        )
        return MemoryQueryResult(decision=decision, memories=limited)

    def list_audit_rows(self) -> list[dict[str, Any]]:
        """Return audit rows for tests and debugging."""
        with self._lock, _connect(self._audit_db_path) as conn:
            rows = conn.execute(
                """
                SELECT
                    audit_id,
                    requester_principal_id,
                    requester_principal_type,
                    requester_level,
                    agent_id,
                    target_scope,
                    query_text,
                    result_count,
                    access_kind,
                    reason,
                    created_at_ms
                FROM memory_access_audit
                ORDER BY created_at_ms ASC
                """
            ).fetchall()
        return [dict(row) for row in rows]

    def list_audit(
        self,
        *,
        agent_id: str,
        requester_principal_id: str,
        limit: int = 50,
    ) -> MemoryAuditResult:
        """Return visible audit rows for one requester and agent."""
        decision = self._access_policy.decide_agent_scope(
            requester_principal_id=requester_principal_id,
            agent_id=agent_id,
            access_kind="memory_audit_read",
        )
        if not decision.allow:
            return MemoryAuditResult(decision=decision, rows=[])

        normalized_limit = max(int(limit), 0)
        query = """
            SELECT
                audit_id,
                requester_principal_id,
                requester_principal_type,
                requester_level,
                agent_id,
                target_scope,
                query_text,
                result_count,
                access_kind,
                reason,
                created_at_ms
            FROM memory_access_audit
            WHERE agent_id = ?
        """
        params: list[Any] = [agent_id]
        if decision.scope_kind != "all":
            visible_requester_ids = [
                item for item in decision.resolved_scope(self._identity_store.list_principal_ids()) if str(item).strip()
            ]
            if not visible_requester_ids:
                return MemoryAuditResult(decision=decision, rows=[])
            placeholders = ", ".join("?" for _ in visible_requester_ids)
            query += f" AND requester_principal_id IN ({placeholders})"
            params.extend(visible_requester_ids)
        query += " ORDER BY created_at_ms DESC"
        if normalized_limit > 0:
            query += " LIMIT ?"
            params.append(normalized_limit)

        with self._lock, _connect(self._audit_db_path) as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return MemoryAuditResult(decision=decision, rows=[dict(row) for row in rows])
