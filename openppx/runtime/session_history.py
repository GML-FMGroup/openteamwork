"""Transport-independent projection of visible ADK Session history."""

from __future__ import annotations

from datetime import datetime, timezone
import re
from typing import Any

from .adk_utils import extract_text
from .session_rewind import visible_events_after_rewinds


_LEGACY_ATTACHMENT_PATTERN = re.compile(
    r"\A\[Attachment: (?P<file_name>[^\r\n]+)\]\n"
    r"Format: [^\r\n]+\n\n.+\n\[End attachment\]\Z",
    re.DOTALL,
)


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


def _attachment_descriptors(event: Any) -> dict[int, str]:
    """Return safe display-only attachment names keyed by content Part index."""
    metadata = getattr(event, "custom_metadata", None)
    if not isinstance(metadata, dict):
        return {}
    raw_descriptors = metadata.get("clientAttachments")
    if not isinstance(raw_descriptors, list):
        raw_descriptors = metadata.get("client_attachments")
    if not isinstance(raw_descriptors, list):
        return {}
    descriptors: dict[int, str] = {}
    for raw in raw_descriptors:
        if not isinstance(raw, dict):
            continue
        index = raw.get("contentPartIndex")
        name = str(raw.get("fileName") or "").strip()
        if (
            isinstance(index, int)
            and not isinstance(index, bool)
            and index >= 0
            and 0 < len(name) <= 255
            and "/" not in name
            and "\\" not in name
            and not any(ord(character) < 32 for character in name)
        ):
            descriptors[index] = name
    return descriptors


def _legacy_attachment_name(text: str, *, part_index: int) -> str | None:
    """Recognize legacy generated attachment envelopes without trusting Part zero."""
    if part_index == 0:
        return None
    matched = _LEGACY_ATTACHMENT_PATTERN.fullmatch(text.replace("\r\n", "\n"))
    if matched is None:
        return None
    name = matched.group("file_name").strip()
    if not name or len(name) > 255 or "/" in name or "\\" in name:
        return None
    return name


def project_searchable_history(session: Any) -> list[dict[str, object]]:
    """Project searchable user/assistant text and attachment markers only.

    Attachment bodies, tool payloads, thought Parts, and Artifacts are excluded.
    Each result carries a stable persisted event citation when one is available.
    """
    projected: list[dict[str, object]] = []
    visible = visible_events_after_rewinds(list(getattr(session, "events", []) or []))
    for event_index, event in enumerate(visible):
        author = str(getattr(event, "author", "") or "").strip().lower()
        if author not in {"user", "assistant", "agent", "model"}:
            continue
        role = "user" if author == "user" else "assistant"
        descriptors = _attachment_descriptors(event) if role == "user" else {}
        content = getattr(event, "content", None)
        parts = list(getattr(content, "parts", None) or [])
        text_parts: list[str] = []
        attachment_names: list[str] = []
        for part_index, part in enumerate(parts):
            if bool(getattr(part, "thought", False)):
                continue
            raw_text = getattr(part, "text", None)
            if isinstance(raw_text, str) and raw_text.strip():
                text = _strip_request_time_prefix(raw_text)
                attachment_name = descriptors.get(part_index)
                if attachment_name is None and role == "user":
                    attachment_name = _legacy_attachment_name(text, part_index=part_index)
                if attachment_name is not None:
                    text_parts.append(f"[Attachment: {attachment_name}]")
                    attachment_names.append(attachment_name)
                elif text.strip():
                    text_parts.append(text.strip())
            inline_data = getattr(part, "inline_data", None)
            if inline_data is not None:
                display_name = str(getattr(inline_data, "display_name", "") or "Attachment").strip()
                marker = f"[Attachment: {display_name}]"
                text_parts.append(marker)
                attachment_names.append(display_name)
        text = "\n".join(text_parts).strip()
        if not text:
            continue
        event_id = str(getattr(event, "id", "") or "").strip()
        if not event_id:
            invocation_id = str(getattr(event, "invocation_id", "") or "").strip()
            event_id = f"{invocation_id or 'event'}:{event_index}"
        projected.append(
            {
                "messageId": event_id,
                "role": role,
                "text": text,
                "timestamp": _iso_timestamp(getattr(event, "timestamp", None)),
                "attachmentNames": attachment_names,
            }
        )
    return projected


__all__ = ["project_searchable_history", "project_visible_history"]
