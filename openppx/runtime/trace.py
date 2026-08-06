"""Small, safe helpers for OpenPPX runtime trace projections."""

from __future__ import annotations

import re
from typing import Any


_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{12,}"),
    re.compile(r"AIza[0-9A-Za-z_-]{12,}"),
    re.compile(r"(?i)(api[_-]?key|authorization|password|secret|token)\s*[:=]\s*['\"]?([^\s'\",;]+)"),
)


def redact_trace_text(value: Any, *, limit: int = 1_000) -> str:
    """Return bounded diagnostic text with common credential forms removed."""
    text = str(value or "")
    for pattern in _SECRET_PATTERNS:
        if pattern.groups >= 2:
            text = pattern.sub(lambda match: f"{match.group(1)}=<redacted>", text)
        else:
            text = pattern.sub("<redacted>", text)
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


def structured_runtime_error(error: BaseException) -> dict[str, Any]:
    """Project an exception into a stable, redacted, user-actionable error fact."""
    error_type = type(error).__name__
    normalized = error_type.lower()
    retryable = any(marker in normalized for marker in ("timeout", "connection", "temporary", "rate"))
    if "timeout" in normalized:
        user_action = "Retry, increase the time budget, or narrow the work."
    elif "permission" in normalized or "auth" in normalized:
        user_action = "Review access and authentication, then retry."
    elif retryable:
        user_action = "Check the dependency and retry."
    else:
        user_action = "Open the related Activity details and review the failing step."
    return {
        "code": _error_code(error_type),
        "rootCause": error_type,
        "message": redact_trace_text(error),
        "retryable": retryable,
        "userAction": user_action,
    }


def _error_code(error_type: str) -> str:
    """Convert one exception class name to a stable snake-case diagnostic code."""
    separated = re.sub(r"(?<!^)(?=[A-Z])", "_", error_type).lower()
    return separated.removesuffix("_error") or "runtime_failure"
