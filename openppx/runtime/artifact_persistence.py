"""Shared ADK Artifact persistence for validated in-memory bytes."""

from __future__ import annotations

import datetime as dt
import hashlib
from typing import Any, Mapping

from google.genai import types

from .attachment_service import PreparedAttachment


async def save_prepared_artifact(
    *,
    tool_context: Any,
    prepared: PreparedAttachment,
    storage_key: str,
    source: str,
    artifact_id_prefix: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Save validated bytes through the active ADK ToolContext.

    The ToolContext supplies the authoritative app/user/session scope. Callers
    choose only a bounded storage key and non-sensitive provenance metadata.
    """
    if not callable(getattr(tool_context, "save_artifact", None)):
        raise ValueError("Artifact storage is unavailable for this Run.")
    key = str(storage_key or "").strip()
    if not key or key.startswith("/") or ".." in key.split("/"):
        raise ValueError("Artifact storage key is invalid.")
    source_name = str(source or "").strip()
    if not source_name:
        raise ValueError("Artifact source is required.")
    artifact_id = artifact_id_prefix + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
    created_at = dt.datetime.now(dt.timezone.utc).isoformat()
    custom_metadata = {
        **dict(metadata or {}),
        **prepared.metadata,
        "artifact_id": artifact_id,
        "source": source_name,
        "file_name": prepared.file_name,
        "size_bytes": len(prepared.data),
        "created_at": created_at,
    }
    version = await tool_context.save_artifact(
        filename=key,
        artifact=types.Part.from_bytes(
            data=prepared.data,
            mime_type=prepared.mime_type,
        ),
        custom_metadata=custom_metadata,
    )
    return {
        "id": artifact_id,
        "key": key,
        "fileName": prepared.file_name,
        "mimeType": prepared.mime_type,
        "sizeBytes": len(prepared.data),
        "version": int(version),
        "source": source_name,
        "createdAt": created_at,
    }


__all__ = ["save_prepared_artifact"]
