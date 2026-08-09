"""Convert supported MCP binary Tool results into ADK Artifacts."""

from __future__ import annotations

import base64
import binascii
import copy
import hashlib
import re
from dataclasses import dataclass
from typing import Any

from google.adk.tools.base_tool import BaseTool

from .artifact_persistence import save_prepared_artifact
from .attachment_service import (
    MAX_ATTACHMENT_BYTES,
    AttachmentValidationError,
    prepare_attachment,
)


MAX_MCP_BINARY_BYTES = MAX_ATTACHMENT_BYTES
_MIME_EXTENSIONS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": ".xlsx",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "text/csv": ".csv",
}


@dataclass(frozen=True, slots=True)
class _BinaryContent:
    """One explicitly recognized MCP binary content item."""

    encoded: str
    mime_type: str


class McpArtifactTool(BaseTool):
    """ADK Tool wrapper that removes MCP base64 after Artifact persistence."""

    _openppx_mcp_artifacts = True

    def __init__(self, *, wrapped_tool: BaseTool, server_name: str) -> None:
        self._wrapped_tool = wrapped_tool
        self._server_name = str(server_name or "").strip() or "unknown"
        super().__init__(
            name=str(getattr(wrapped_tool, "name", "") or ""),
            description=str(getattr(wrapped_tool, "description", "") or ""),
            is_long_running=bool(getattr(wrapped_tool, "is_long_running", False)),
            custom_metadata=copy.deepcopy(getattr(wrapped_tool, "custom_metadata", None)),
        )

    @property
    def wrapped_tool(self) -> BaseTool:
        """Return the underlying Google ADK MCP Tool."""
        return self._wrapped_tool

    @property
    def raw_mcp_tool(self) -> Any:
        """Preserve raw MCP metadata for other runtime adapters."""
        return getattr(self._wrapped_tool, "raw_mcp_tool", None)

    def __copy__(self) -> "McpArtifactTool":
        """Return a safe copy for ADK's tool-prefix projection."""
        return type(self)(
            wrapped_tool=self._wrapped_tool,
            server_name=self._server_name,
        )

    def __getattr__(self, name: str) -> Any:
        """Delegate MCP-specific behavior to the wrapped Tool."""
        return getattr(self._wrapped_tool, name)

    def _get_declaration(self) -> Any:
        """Return the wrapped Tool's original function declaration."""
        return self._wrapped_tool._get_declaration()  # pylint: disable=protected-access

    async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
        """Run the MCP Tool and replace recognized binary items with references."""
        result = await self._wrapped_tool.run_async(args=args, tool_context=tool_context)
        if not isinstance(result, dict) or not isinstance(result.get("content"), list):
            return result
        transformed = dict(result)
        transformed_content: list[Any] = []
        artifacts = list(result["artifacts"]) if isinstance(result.get("artifacts"), list) else []
        for index, item in enumerate(result["content"]):
            candidate = _binary_content(item)
            if candidate is None:
                transformed_content.append(item)
                continue
            replacement, artifact = await self._persist_binary(
                candidate,
                index=index,
                tool_context=tool_context,
            )
            transformed_content.append(replacement)
            if artifact is not None:
                artifacts.append(artifact)
        transformed["content"] = transformed_content
        transformed["artifacts"] = artifacts
        return transformed

    async def _persist_binary(
        self,
        candidate: _BinaryContent,
        *,
        index: int,
        tool_context: Any,
    ) -> tuple[dict[str, str], dict[str, Any] | None]:
        """Validate and persist one binary item without returning its base64."""
        try:
            data = _decode_bounded(candidate.encoded)
            extension = _MIME_EXTENSIONS.get(candidate.mime_type)
            if extension is None:
                raise AttachmentValidationError(
                    "The MCP binary MIME type is not supported."
                )
            identity = hashlib.sha256(
                f"{self._server_name}:{self.name}:{index}:".encode("utf-8") + data
            ).hexdigest()[:16]
            safe_server = _safe_stem(self._server_name)
            safe_tool = _safe_stem(self.name)
            file_name = f"{safe_tool}-{identity}{extension}"
            prepared = prepare_attachment(
                file_name=file_name,
                mime_type=candidate.mime_type,
                data=data,
            )
            artifact = await save_prepared_artifact(
                tool_context=tool_context,
                prepared=prepared,
                storage_key=f"mcp/{safe_server}/{file_name}",
                source="mcp_tool_result",
                artifact_id_prefix="artifact_mcp_",
                metadata={
                    "mcp_server": self._server_name,
                    "mcp_tool": self.name,
                    "content_index": index,
                },
            )
            return (
                {
                    "type": "text",
                    "text": (
                        "MCP binary content was stored as Artifact "
                        f"'{artifact['fileName']}' ({artifact['key']})."
                    ),
                },
                artifact,
            )
        except _McpBinarySizeError:
            return _omitted("MCP binary content exceeds the size limit."), None
        except binascii.Error:
            return _omitted("MCP binary content is not valid base64."), None
        except AttachmentValidationError as exc:
            return _omitted(str(exc)), None
        except Exception:  # Artifact services are an external runtime boundary.
            return _omitted("MCP binary Artifact storage is unavailable."), None


class _McpBinarySizeError(ValueError):
    """Raised before allocating decoded bytes for an oversized MCP payload."""


def _decode_bounded(encoded: str) -> bytes:
    """Decode strict base64 after enforcing an encoded upper bound."""
    if not isinstance(encoded, str):
        raise binascii.Error("base64 payload must be text")
    maximum_encoded = ((MAX_MCP_BINARY_BYTES + 2) // 3) * 4
    if len(encoded) > maximum_encoded:
        raise _McpBinarySizeError
    data = base64.b64decode(encoded, validate=True)
    if len(data) > MAX_MCP_BINARY_BYTES:
        raise _McpBinarySizeError
    return data


def _binary_content(item: Any) -> _BinaryContent | None:
    """Recognize protocol-defined ImageContent and embedded blob content."""
    if not isinstance(item, dict):
        return None
    if item.get("type") in {"image", "audio"} and "data" in item:
        return _BinaryContent(
            encoded=item.get("data"),
            mime_type=_normalized_mime(item.get("mimeType")),
        )
    if item.get("type") == "resource" and isinstance(item.get("resource"), dict):
        resource = item["resource"]
        if "blob" in resource:
            return _BinaryContent(
                encoded=resource.get("blob"),
                mime_type=_normalized_mime(resource.get("mimeType")),
            )
    return None


def _normalized_mime(value: Any) -> str:
    """Return a bounded lower-case MCP MIME value."""
    rendered = str(value or "application/octet-stream").split(";", 1)[0].strip().lower()
    return rendered[:127]


def _safe_stem(value: str) -> str:
    """Create one short storage component from untrusted MCP identifiers."""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return (safe or "mcp")[:80]


def _omitted(message: str) -> dict[str, str]:
    """Return a bounded model-facing replacement for rejected binary data."""
    return {"type": "text", "text": f"MCP binary content omitted: {message[:512]}"}


def wrap_mcp_tool_artifacts(tool: Any, *, server_name: str) -> Any:
    """Wrap only real MCP Tools and keep Resource loader Tools unchanged."""
    if getattr(tool, "_openppx_mcp_artifacts", False):
        return tool
    if not isinstance(tool, BaseTool) or getattr(tool, "raw_mcp_tool", None) is None:
        return tool
    return McpArtifactTool(wrapped_tool=tool, server_name=server_name)


__all__ = [
    "MAX_MCP_BINARY_BYTES",
    "McpArtifactTool",
    "wrap_mcp_tool_artifacts",
]
