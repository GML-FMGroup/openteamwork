"""Memory service factory for ADK runner."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from google.adk.memory import InMemoryMemoryService

from .markdown_memory_service import MarkdownMemoryService
from .sqlite_memory_service import SQLiteMemoryService
from .paths import resolve_node_root


@dataclass(slots=True)
class MemoryConfig:
    """Runtime memory configuration for openppx.

    Attributes:
        enabled: Whether long-term memory is enabled for the runner.
        backend: Memory backend name. Supported values:
            - ``sqlite`` (default)
            - ``markdown`` (legacy local backend)
            - ``in_memory`` (debug fallback)
        markdown_dir: Root directory for markdown memory files.
        sqlite_db_path: SQLite database path for the primary backend.
    """

    enabled: bool
    backend: str
    markdown_dir: str
    sqlite_db_path: str = ""


def _default_markdown_dir(node_root: Path | None = None) -> Path:
    """Resolve default markdown memory directory.

    By default memory files are colocated with agent bootstrap files so the
    runtime consistently uses ``<agent_home>/memory/{MEMORY.md,HISTORY.md}``.
    """
    return resolve_node_root(node_root) / "memory"


def _default_sqlite_db_path(node_root: Path | None = None) -> Path:
    """Resolve default SQLite memory database path."""
    return resolve_node_root(node_root) / "database" / "memory.db"


def load_memory_config(node_root: Path | None = None) -> MemoryConfig:
    """Build deterministic SQLite memory configuration for one Node root."""
    return MemoryConfig(
        enabled=True,
        backend="sqlite",
        markdown_dir=str(_default_markdown_dir(node_root)),
        sqlite_db_path=str(_default_sqlite_db_path(node_root)),
    )


def create_memory_service(config: MemoryConfig | None = None) -> Any | None:
    """Create an ADK memory service instance from runtime config.

    Fallback behavior is intentionally conservative:
    - If memory is disabled, returns ``None``.
    - Unknown backends fall back to in-memory to keep the agent runnable.
    """
    cfg = config or load_memory_config()
    if not cfg.enabled:
        return None

    if cfg.backend == "sqlite":
        return SQLiteMemoryService(db_path=cfg.sqlite_db_path or str(_default_sqlite_db_path()))
    if cfg.backend == "markdown":
        return MarkdownMemoryService(root_dir=cfg.markdown_dir)
    return InMemoryMemoryService()
