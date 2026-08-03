"""Transport-independent projection of visible ADK Session history."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .adk_utils import extract_text
from .session_rewind import visible_events_after_rewinds


def _strip_request_time_prefix(text: str) -> str:
    """Hide the internal request-time context injected before user messages."""
    stripped = text.strip()
    if not stripped.startswith("Current request time: "):
        return text.strip()
    lines = stripped.splitlines()
    if len(lines) < 2 or "Use this as the reference 'now'" not in lines[1]:
        return text.strip()
    return "\n".join(lines[2:]).strip()


def _iso_timestamp(value: Any) -> str | None:
    if not isinstance(value, (int, float)):
        return None
    return datetime.fromtimestamp(value, tz=timezone.utc).isoformat()


def project_visible_history(session: Any, *, limit: int) -> list[dict[str, object]]:
    """Return recent visible text events without tool payloads or thought content."""
    projected: list[dict[str, object]] = []
    visible = visible_events_after_rewinds(list(getattr(session, "events", []) or []))
    for event in visible:
        text = _strip_request_time_prefix(extract_text(getattr(event, "content", None)))
        if not text:
            continue
        author = str(getattr(event, "author", "") or "").strip().lower()
        role = "user" if author == "user" else "assistant"
        projected.append(
            {
                "role": role,
                "text": text,
                "invocationId": str(getattr(event, "invocation_id", "") or ""),
                "timestamp": _iso_timestamp(getattr(event, "timestamp", None)),
            }
        )
    return projected[-limit:]


__all__ = ["project_visible_history"]
