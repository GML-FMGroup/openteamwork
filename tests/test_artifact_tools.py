"""Tests for explicit ADK Artifact publication by Agents."""

from __future__ import annotations

import asyncio
import io
import zipfile
from typing import Any

from openppx.runtime.tool_execution_context import ToolExecutionContext, bind_tool_callable
from openppx.tooling.artifact_tools import publish_artifact


def _docx(text: str = "OpenPPX report") -> bytes:
    """Return a minimal valid Word document containing one paragraph."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Override PartName="/word/document.xml" '
            'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
            "</Types>",
        )
        archive.writestr(
            "word/document.xml",
            '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buffer.getvalue()


class _ArtifactToolContext:
    """Small ADK ToolContext stand-in that records Artifact writes."""

    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def save_artifact(self, **kwargs: Any) -> int:
        self.saved.append(kwargs)
        return len(self.saved) - 1


def test_publish_artifact_saves_word_deliverable_through_adk(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report = workspace / "report.docx"
    report.write_bytes(_docx())
    adk_context = _ArtifactToolContext()
    runtime_context = ToolExecutionContext.for_agent(
        agent_id="writer",
        workspace_root=workspace,
    )
    bound_publish = bind_tool_callable(publish_artifact, runtime_context)

    first = asyncio.run(bound_publish("report.docx", tool_context=adk_context))
    second = asyncio.run(bound_publish(str(report), tool_context=adk_context))

    assert first["ok"] is True
    assert first["artifact"]["key"] == "outputs/report.docx"
    assert first["artifact"]["fileName"] == "report.docx"
    assert first["artifact"]["mimeType"].endswith("wordprocessingml.document")
    assert first["artifact"]["source"] == "agent_output"
    assert first["artifact"]["version"] == 0
    assert second["artifact"]["version"] == 1
    assert second["artifact"]["id"] == first["artifact"]["id"]

    saved = adk_context.saved[0]
    assert saved["filename"] == "outputs/report.docx"
    assert saved["artifact"].inline_data.data == report.read_bytes()
    assert saved["artifact"].inline_data.mime_type.endswith("wordprocessingml.document")
    assert saved["custom_metadata"]["artifact_id"] == first["artifact"]["id"]
    assert saved["custom_metadata"]["file_name"] == "report.docx"
    assert saved["custom_metadata"]["source"] == "agent_output"


def test_publish_artifact_rejects_file_outside_agent_workspace(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "private.txt"
    outside.write_text("secret", encoding="utf-8")
    adk_context = _ArtifactToolContext()
    bound_publish = bind_tool_callable(
        publish_artifact,
        ToolExecutionContext.for_agent(agent_id="writer", workspace_root=workspace),
    )

    result = asyncio.run(bound_publish(str(outside), tool_context=adk_context))

    assert result["ok"] is False
    assert "Agent Workspace" in result["error"]
    assert adk_context.saved == []


def test_publish_artifact_requires_adk_artifact_storage(tmp_path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("deliverable", encoding="utf-8")
    bound_publish = bind_tool_callable(
        publish_artifact,
        ToolExecutionContext.for_agent(agent_id="writer", workspace_root=workspace),
    )

    result = asyncio.run(bound_publish("notes.txt"))

    assert result == {"ok": False, "error": "Artifact storage is unavailable for this Run."}
