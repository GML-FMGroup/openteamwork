"""Filesystem locations for one explicitly selected OpenPPX Node root."""

from __future__ import annotations

from pathlib import Path

from openppx.product import PRODUCT

_configured_node_root: Path | None = None


def configure_node_root(node_root: Path | None) -> Path | None:
    """Bind the single Node root owned by this process and return its prior value.

    OpenPPX intentionally runs one Node composition per process. Runtime helpers
    that cannot receive a service explicitly use this binding instead of
    consulting process environment variables.
    """
    global _configured_node_root
    previous = _configured_node_root
    _configured_node_root = (
        node_root.expanduser().resolve(strict=False) if node_root is not None else None
    )
    return previous


def default_node_root() -> Path:
    """Return the conventional Node root used only when no root is supplied."""
    if _configured_node_root is not None:
        return _configured_node_root
    return Path.home() / PRODUCT.node_root_directory


def resolve_node_root(node_root: Path | None = None) -> Path:
    """Resolve an explicit Node root or the conventional local default."""
    root = node_root if node_root is not None else default_node_root()
    return root.expanduser().resolve(strict=False)


def node_database_path(name: str, *, node_root: Path | None = None) -> Path:
    """Return one database path below the selected Node root."""
    if not name or Path(name).name != name:
        raise ValueError("database name must be one plain filename")
    return resolve_node_root(node_root) / "database" / name
