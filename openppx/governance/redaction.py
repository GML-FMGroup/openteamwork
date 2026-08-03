"""Recursive outer-boundary redaction for diagnostics and persisted facts."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any


REDACTED = "<redacted>"
_SENSITIVE_KEY = re.compile(
    r"(?:authorization|api[_-]?key|access[_-]?token|refresh[_-]?token|token|secret|password|credential|cookie|private[_-]?key)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]{6,}")
_COMMON_SECRET = re.compile(r"\b(?:sk|ghp|xox[baprs]|AIza)[-_A-Za-z0-9]{8,}\b")


def redact(value: object, *, canaries: Sequence[str] = ()) -> object:
    """Return a JSON-safe copy with sensitive keys and secret-shaped text removed."""
    normalized_canaries = tuple(item for item in canaries if item)
    return _redact(value, canaries=normalized_canaries, seen=set())


def _redact(value: object, *, canaries: tuple[str, ...], seen: set[int]) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        result = value
        for canary in canaries:
            result = result.replace(canary, REDACTED)
        result = _BEARER.sub(REDACTED, result)
        return _COMMON_SECRET.sub(REDACTED, result)
    identity = id(value)
    if identity in seen:
        return "<recursive>"
    if isinstance(value, Mapping):
        seen.add(identity)
        output: dict[str, Any] = {}
        for key, item in value.items():
            text_key = str(key)
            output[text_key] = REDACTED if _SENSITIVE_KEY.search(text_key) else _redact(
                item,
                canaries=canaries,
                seen=seen,
            )
        seen.remove(identity)
        return output
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        seen.add(identity)
        output = [_redact(item, canaries=canaries, seen=seen) for item in value]
        seen.remove(identity)
        return output
    return _redact(str(value), canaries=canaries, seen=seen)


__all__ = ["REDACTED", "redact"]
