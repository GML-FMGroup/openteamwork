"""SQLite session service factory for ADK runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.sessions import DatabaseSessionService
from .adk_storage_meta import ensure_adk_storage_meta
from .adk_storage_meta import ensure_adk_storage_meta_for_db_url
from .paths import resolve_node_root


@dataclass(slots=True)
class SessionConfig:
    """Runtime session storage configuration (SQLite only)."""

    db_url: str


def _default_sqlite_db_url(node_root: Path | None = None) -> str:
    data_dir = resolve_node_root(node_root)
    ensure_adk_storage_meta(data_dir)
    db_path = data_dir / "database" / "sessions.db"
    return f"sqlite+aiosqlite:///{db_path}"


def load_session_config(node_root: Path | None = None) -> SessionConfig:
    """Build deterministic SQLite session configuration for one Node root."""
    return SessionConfig(db_url=_default_sqlite_db_url(node_root))


def create_session_service(config: SessionConfig | None = None) -> Any:
    """Create ADK SQLite session service."""
    cfg = config or load_session_config()
    ensure_adk_storage_meta_for_db_url(cfg.db_url)
    return DatabaseSessionService(cfg.db_url)
