"""Stable, redacted Extension Platform failures."""

from __future__ import annotations

from typing import Any


class ExtensionError(ValueError):
    """One client-safe Extension failure with stable machine semantics."""

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = dict(details or {})
        super().__init__(message)


__all__ = ["ExtensionError"]
