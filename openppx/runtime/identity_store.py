"""SQLite-backed identity resolution helpers for Node/runtime integration."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from .identity_models import ResolvedPrincipal
from .paths import node_database_path
from .system_principals import get_system_principal


@dataclass(slots=True)
class IdentityStoreConfig:
    """Runtime configuration for the identity store."""

    db_path: str


@dataclass(slots=True)
class ExternalIdentity:
    """Normalized external identity fields extracted from one inbound sender."""

    external_subject_id: str
    external_display_id: str


def _default_identity_db_path(node_root: Path | None = None) -> Path:
    """Return the default SQLite path used by the identity store."""
    return node_database_path("identity.db", node_root=node_root)


def load_identity_store_config(node_root: Path | None = None) -> IdentityStoreConfig:
    """Build deterministic identity storage for one Node root."""
    return IdentityStoreConfig(db_path=str(_default_identity_db_path(node_root)))


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
    fallback = (Path.cwd() / ".openppx" / "database" / db_path.name).resolve(strict=False)
    fallback.parent.mkdir(parents=True, exist_ok=True)
    return fallback


def _prepare_db_path(db_path: Path) -> Path:
    """Return a writable SQLite path, falling back to workspace-local storage."""
    candidate = db_path.expanduser().resolve(strict=False)
    try:
        candidate.parent.mkdir(parents=True, exist_ok=True)
        return candidate
    except PermissionError:
        return _workspace_fallback_db_path(candidate)


def _now_ms() -> int:
    """Return current wall-clock milliseconds."""
    return int(time.time() * 1000)


def _json_dumps(payload: dict[str, object]) -> str:
    """Serialize a JSON object for SQLite storage."""
    return json.dumps(payload, ensure_ascii=False, default=str)


def _json_loads(raw: str | None) -> dict[str, object]:
    """Deserialize stored JSON metadata into a dict."""
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _normalize_external_identity(*, source: str, sender_id: str) -> ExternalIdentity:
    """Normalize one inbound sender id into stable subject/display identifiers."""
    raw_sender = str(sender_id or "").strip()
    if source == "telegram" and "|@" in raw_sender:
        external_subject_id, username = raw_sender.split("|@", 1)
        stable_subject = external_subject_id.strip()
        display_id = f"@{username.strip()}" if username.strip() else raw_sender
        if stable_subject:
            return ExternalIdentity(
                external_subject_id=stable_subject,
                external_display_id=display_id,
            )
    if raw_sender:
        return ExternalIdentity(
            external_subject_id=raw_sender,
            external_display_id=raw_sender,
        )
    return ExternalIdentity(
        external_subject_id="unknown",
        external_display_id="unknown",
    )


def _human_principal_id(*, source: str, external_subject_id: str) -> str:
    """Build the default internal principal id for one source-scoped human."""
    normalized_source = str(source or "unknown").strip().lower() or "unknown"
    normalized_subject = str(external_subject_id or "unknown").strip() or "unknown"
    return f"human:{normalized_source}:{normalized_subject}"


class IdentityStore:
    """Resolve inbound human/service identities into persistent principal rows.

    The first implementation keeps the schema intentionally small:
    `principals` stores the canonical runtime identity, and
    `principal_external_identities` maps source-scoped external subjects onto
    those internal principal ids.
    """

    def __init__(self, *, db_path: str | Path | None = None):
        cfg = load_identity_store_config() if db_path is None else IdentityStoreConfig(db_path=str(db_path))
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
        """Create principal tables when missing."""
        with _connect(self._db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS principals (
                    principal_id TEXT PRIMARY KEY,
                    principal_type TEXT NOT NULL,
                    privilege_level TEXT NOT NULL,
                    account_kind TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    authenticated INTEGER NOT NULL,
                    memory_ingest_enabled INTEGER NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS principal_external_identities (
                    source TEXT NOT NULL,
                    external_subject_id TEXT NOT NULL,
                    principal_id TEXT NOT NULL,
                    external_display_id TEXT NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    updated_at_ms INTEGER NOT NULL,
                    PRIMARY KEY (source, external_subject_id),
                    FOREIGN KEY (principal_id) REFERENCES principals(principal_id)
                )
                """
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_principal_external_identities_principal "
                "ON principal_external_identities(principal_id)"
            )

    def _upsert_principal(self, conn: sqlite3.Connection, principal: ResolvedPrincipal) -> None:
        """Insert or update one principal row."""
        now_ms = _now_ms()
        conn.execute(
            """
            INSERT INTO principals (
                principal_id,
                principal_type,
                privilege_level,
                account_kind,
                display_name,
                authenticated,
                memory_ingest_enabled,
                metadata_json,
                created_at_ms,
                updated_at_ms
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(principal_id) DO UPDATE SET
                principal_type=excluded.principal_type,
                privilege_level=excluded.privilege_level,
                account_kind=excluded.account_kind,
                display_name=excluded.display_name,
                authenticated=excluded.authenticated,
                memory_ingest_enabled=excluded.memory_ingest_enabled,
                metadata_json=excluded.metadata_json,
                updated_at_ms=excluded.updated_at_ms
            """,
            (
                principal.principal_id,
                principal.principal_type,
                principal.privilege_level,
                principal.account_kind,
                principal.display_name,
                1 if principal.authenticated else 0,
                1 if principal.memory_ingest_enabled else 0,
                _json_dumps(dict(principal.metadata)),
                now_ms,
                now_ms,
            ),
        )

    @staticmethod
    def _principal_from_row(
        row: sqlite3.Row,
        *,
        external_subject_id: str | None = None,
        external_display_id: str | None = None,
    ) -> ResolvedPrincipal:
        """Project one joined SQLite row into a runtime principal object."""
        return ResolvedPrincipal(
            principal_id=str(row["principal_id"]),
            principal_type=str(row["principal_type"]),
            privilege_level=str(row["privilege_level"]),
            account_kind=str(row["account_kind"]),
            display_name=str(row["display_name"]),
            authenticated=bool(row["authenticated"]),
            memory_ingest_enabled=bool(row["memory_ingest_enabled"]),
            external_subject_id=external_subject_id,
            external_display_id=external_display_id,
            metadata=_json_loads(row["metadata_json"]),
        )

    def put_principal(self, principal: ResolvedPrincipal) -> ResolvedPrincipal:
        """Insert or update one principal explicitly."""
        with self._lock, _connect(self._db_path) as conn:
            self._upsert_principal(conn, principal)
        return principal

    def get_principal(self, principal_id: str) -> ResolvedPrincipal | None:
        """Return one principal by its internal id."""
        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    principal_id,
                    principal_type,
                    privilege_level,
                    account_kind,
                    display_name,
                    authenticated,
                    memory_ingest_enabled,
                    metadata_json
                FROM principals
                WHERE principal_id = ?
                """,
                (principal_id,),
            ).fetchone()
        if row is None:
            return None
        return self._principal_from_row(row)

    def list_principal_ids(self) -> list[str]:
        """Return all known principal ids ordered for deterministic iteration."""
        with self._lock, _connect(self._db_path) as conn:
            rows = conn.execute(
                """
                SELECT principal_id
                FROM principals
                ORDER BY principal_id ASC
                """
            ).fetchall()
        return [str(row["principal_id"]) for row in rows]

    def resolve_external_principal(self, *, source: str, sender_id: str) -> ResolvedPrincipal:
        """Resolve one external sender into a persisted human principal."""
        identity = _normalize_external_identity(source=source, sender_id=sender_id)
        source_key = str(source or "unknown").strip().lower() or "unknown"
        principal_id = _human_principal_id(
            source=source_key,
            external_subject_id=identity.external_subject_id,
        )

        with self._lock, _connect(self._db_path) as conn:
            row = conn.execute(
                """
                SELECT
                    p.principal_id,
                    p.principal_type,
                    p.privilege_level,
                    p.account_kind,
                    p.display_name,
                    p.authenticated,
                    p.memory_ingest_enabled,
                    p.metadata_json
                FROM principals AS p
                JOIN principal_external_identities AS e
                  ON e.principal_id = p.principal_id
                WHERE e.source = ? AND e.external_subject_id = ?
                """,
                (source_key, identity.external_subject_id),
            ).fetchone()

            if row is None:
                principal = ResolvedPrincipal(
                    principal_id=principal_id,
                    principal_type="human",
                    privilege_level="minimal",
                    account_kind="unknown",
                    display_name=identity.external_display_id or identity.external_subject_id,
                    authenticated=bool(identity.external_subject_id),
                    external_subject_id=identity.external_subject_id,
                    external_display_id=identity.external_display_id,
                    metadata={"source": source_key},
                )
                self._upsert_principal(conn, principal)
                now_ms = _now_ms()
                conn.execute(
                    """
                    INSERT INTO principal_external_identities (
                        source,
                        external_subject_id,
                        principal_id,
                        external_display_id,
                        created_at_ms,
                        updated_at_ms
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(source, external_subject_id) DO UPDATE SET
                        principal_id=excluded.principal_id,
                        external_display_id=excluded.external_display_id,
                        updated_at_ms=excluded.updated_at_ms
                    """,
                    (
                        source_key,
                        identity.external_subject_id,
                        principal.principal_id,
                        identity.external_display_id,
                        now_ms,
                        now_ms,
                    ),
                )
                return principal

            current_display_name = identity.external_display_id or identity.external_subject_id
            principal = self._principal_from_row(
                row,
                external_subject_id=identity.external_subject_id,
                external_display_id=identity.external_display_id,
            )
            if principal.display_name != current_display_name:
                principal.display_name = current_display_name
                self._upsert_principal(conn, principal)
            conn.execute(
                """
                UPDATE principal_external_identities
                SET external_display_id = ?, updated_at_ms = ?
                WHERE source = ? AND external_subject_id = ?
                """,
                (
                    identity.external_display_id,
                    _now_ms(),
                    source_key,
                    identity.external_subject_id,
                ),
            )
            return principal

    def resolve_service_principal(self, name: str) -> ResolvedPrincipal:
        """Resolve one built-in runtime service principal."""
        definition = get_system_principal(name)
        if definition is None:
            normalized = str(name or "").strip().lower() or "service"
            principal = ResolvedPrincipal(
                principal_id=normalized,
                principal_type="service",
                privilege_level="high",
                account_kind="internal",
                display_name=normalized,
                memory_ingest_enabled=False,
                metadata={"service_name": normalized},
            )
        else:
            principal = ResolvedPrincipal(
                principal_id=definition.principal_id,
                principal_type="service",
                privilege_level=definition.privilege_level,
                account_kind=definition.account_kind,
                display_name=definition.display_name,
                memory_ingest_enabled=definition.memory_ingest_enabled,
                metadata={"service_name": definition.principal_id},
            )

        with self._lock, _connect(self._db_path) as conn:
            self._upsert_principal(conn, principal)
        return principal


def create_identity_store(config: IdentityStoreConfig | None = None) -> IdentityStore:
    """Create the runtime identity resolver used by Node flows."""
    if config is None:
        return IdentityStore()
    return IdentityStore(db_path=config.db_path)
