"""ADK-native tools for publishing explicit Agent deliverables."""

from __future__ import annotations

import datetime as dt
import hashlib
from pathlib import Path
from typing import Any

from google.genai import types

from openppx.runtime.attachment_service import (
    AttachmentValidationError,
    prepare_attachment,
)
from openppx.runtime.tool_execution_context import current_tool_execution_context


def _workspace_file(path: str) -> tuple[Path, Path]:
    """Resolve one existing regular file inside the active Agent Workspace.

    Artifact publication always enforces this boundary, even when broader file
    access is enabled for another tool. Publishing makes bytes downloadable to
    the Session owner, so it must never accept an arbitrary host path.
    """
    context = current_tool_execution_context()
    if context is None:
        raise PermissionError("Artifact publication requires an Agent-scoped runtime context.")
    workspace = context.workspace_root.expanduser().resolve(strict=False)
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    resolved = candidate.resolve(strict=True)
    try:
        relative = resolved.relative_to(workspace)
    except ValueError as exc:
        raise PermissionError("Only files inside the Agent Workspace can be published.") from exc
    if not resolved.is_file():
        raise ValueError("Only regular files can be published as Artifacts.")
    return resolved, relative


async def publish_artifact(
    path: str,
    artifact_name: str = "",
    tool_context: Any | None = None,
) -> dict[str, Any]:
    """Publish a final workspace file as a Session-scoped ADK Artifact.

    Use this only for files the user should receive, such as a generated Word,
    Excel, PowerPoint, PDF, image, CSV, or text deliverable. Source scripts,
    caches, logs, and intermediate files remain ordinary workspace files unless
    the user explicitly asks to receive them.

    Args:
        path: Absolute or workspace-relative path to an existing deliverable.
        artifact_name: Optional user-visible filename. Reusing a name creates a
            new Artifact version in the current Session.
        tool_context: Google ADK context injected for the active tool call.

    Returns:
        A structured success payload containing the Artifact key, stable id,
        version, MIME type, and size, or a bounded error payload.
    """
    try:
        if tool_context is None or not callable(getattr(tool_context, "save_artifact", None)):
            raise ValueError("Artifact storage is unavailable for this Run.")
        source, _relative = _workspace_file(path)
        resolved_name = str(artifact_name or source.name).strip()
        data = source.read_bytes()
        prepared = prepare_attachment(
            file_name=resolved_name,
            mime_type="application/octet-stream",
            data=data,
        )
        storage_key = f"outputs/{prepared.file_name}"
        artifact_id = "artifact_output_" + hashlib.sha256(storage_key.encode("utf-8")).hexdigest()[:16]
        created_at = dt.datetime.now(dt.timezone.utc).isoformat()
        version = await tool_context.save_artifact(
            filename=storage_key,
            artifact=types.Part.from_bytes(
                data=prepared.data,
                mime_type=prepared.mime_type,
            ),
            custom_metadata={
                "artifact_id": artifact_id,
                "source": "agent_output",
                "file_name": prepared.file_name,
                "size_bytes": len(prepared.data),
                "created_at": created_at,
                **prepared.metadata,
            },
        )
        return {
            "ok": True,
            "artifact": {
                "id": artifact_id,
                "key": storage_key,
                "fileName": prepared.file_name,
                "mimeType": prepared.mime_type,
                "sizeBytes": len(prepared.data),
                "version": int(version),
                "source": "agent_output",
                "createdAt": created_at,
            },
        }
    except (AttachmentValidationError, FileNotFoundError, PermissionError, ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}


__all__ = ["publish_artifact"]
