"""MCP binary result to ADK Artifact boundary tests."""

from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from google.adk.agents import LlmAgent
from google.adk.artifacts import FileArtifactService
from google.adk.models.base_llm import BaseLlm
from google.adk.models.llm_response import LlmResponse
from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.adk.tools.base_tool import BaseTool
from google.genai import types
from pydantic import PrivateAttr

from openppx.core.mcp_registry import build_mcp_toolsets
from openppx.runtime.mcp_artifacts import McpArtifactTool


_PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


class _ResultTool(BaseTool):
    """Return one predetermined MCP-shaped payload."""

    def __init__(self, payload: dict[str, Any]) -> None:
        super().__init__(name="render_pixel", description="Render a pixel")
        self.payload = payload
        self._raw_mcp_tool = SimpleNamespace(name=self.name, inputSchema={})

    @property
    def raw_mcp_tool(self) -> Any:
        return self._raw_mcp_tool

    async def run_async(self, *, args: dict[str, Any], tool_context: Any) -> Any:
        return self.payload


class _ArtifactContext:
    """Record ADK Artifact writes without replacing the ADK API."""

    function_call_id = "fc-image-1"

    def __init__(self) -> None:
        self.saved: list[dict[str, Any]] = []

    async def save_artifact(self, **kwargs: Any) -> int:
        self.saved.append(kwargs)
        return len(self.saved) - 1


class _McpImageLlm(BaseLlm):
    """Call the real prefixed MCP image Tool once, then finish."""

    _call_count: int = PrivateAttr(default=0)

    async def generate_content_async(self, llm_request, stream: bool = False):
        del llm_request, stream
        self._call_count += 1
        if self._call_count == 1:
            yield LlmResponse(
                content=types.Content(
                    role="model",
                    parts=[
                        types.Part(
                            function_call=types.FunctionCall(
                                id="fc-image",
                                name="mcp_content_render_pixel",
                                args={},
                            )
                        )
                    ],
                )
            )
            return
        yield LlmResponse(
            content=types.Content(
                role="model",
                parts=[types.Part.from_text(text="Image saved")],
            )
        )


def test_mcp_image_result_is_saved_and_base64_is_removed_from_model_result() -> None:
    encoded = base64.b64encode(_PNG).decode("ascii")
    tool = McpArtifactTool(
        wrapped_tool=_ResultTool(
            {"content": [{"type": "image", "data": encoded, "mimeType": "image/png"}]}
        ),
        server_name="content",
    )
    context = _ArtifactContext()

    result = asyncio.run(tool.run_async(args={}, tool_context=context))

    assert encoded not in str(result)
    assert result["content"][0]["type"] == "text"
    assert result["artifacts"][0]["mimeType"] == "image/png"
    assert result["artifacts"][0]["source"] == "mcp_tool_result"
    assert context.saved[0]["artifact"].inline_data.data == _PNG
    assert context.saved[0]["custom_metadata"]["mcp_server"] == "content"
    assert context.saved[0]["custom_metadata"]["mcp_tool"] == "render_pixel"


def test_mcp_binary_result_is_stripped_when_artifact_storage_is_unavailable() -> None:
    encoded = base64.b64encode(_PNG).decode("ascii")
    tool = McpArtifactTool(
        wrapped_tool=_ResultTool(
            {"content": [{"type": "image", "data": encoded, "mimeType": "image/png"}]}
        ),
        server_name="content",
    )

    result = asyncio.run(tool.run_async(args={}, tool_context=SimpleNamespace()))

    assert encoded not in str(result)
    assert result["artifacts"] == []
    assert "unavailable" in result["content"][0]["text"]


def test_mcp_binary_result_size_limit_is_enforced_before_decoding(monkeypatch) -> None:
    encoded = base64.b64encode(b"0123456789").decode("ascii")
    monkeypatch.setattr("openppx.runtime.mcp_artifacts.MAX_MCP_BINARY_BYTES", 8)
    tool = McpArtifactTool(
        wrapped_tool=_ResultTool(
            {"content": [{"type": "image", "data": encoded, "mimeType": "image/png"}]}
        ),
        server_name="content",
    )
    context = _ArtifactContext()

    result = asyncio.run(tool.run_async(args={}, tool_context=context))

    assert encoded not in str(result)
    assert result["artifacts"] == []
    assert "size limit" in result["content"][0]["text"]
    assert context.saved == []


def test_real_adk_runner_persists_stdio_mcp_image_without_base64(tmp_path: Path) -> None:
    fixture = Path("tests/eval/mock_mcp_content_server.py").resolve()
    toolset = build_mcp_toolsets(
        {
            "content": {
                "command": sys.executable,
                "args": [str(fixture)],
                "longTaskProxy": False,
            }
        },
        log_registered=False,
    )[0]

    async def _run() -> tuple[dict[str, Any], list[str], bytes]:
        sessions = InMemorySessionService()
        artifacts = FileArtifactService(root_dir=tmp_path / "artifacts")
        runner = Runner(
            app_name="mcp-artifact-test",
            agent=LlmAgent(
                name="mcp_artifact_agent",
                model=_McpImageLlm(model="mcp-image-fixture"),
                tools=[toolset],
            ),
            session_service=sessions,
            artifact_service=artifacts,
        )
        await sessions.create_session(
            app_name="mcp-artifact-test",
            user_id="owner",
            session_id="mcp-image-session",
        )
        function_response: dict[str, Any] | None = None
        try:
            async for event in runner.run_async(
                user_id="owner",
                session_id="mcp-image-session",
                new_message=types.Content(
                    role="user",
                    parts=[types.Part.from_text(text="Render one pixel")],
                ),
            ):
                for part in getattr(event.content, "parts", None) or []:
                    if part.function_response is not None:
                        function_response = dict(part.function_response.response or {})
            keys = await artifacts.list_artifact_keys(
                app_name="mcp-artifact-test",
                user_id="owner",
                session_id="mcp-image-session",
            )
            artifact = await artifacts.load_artifact(
                app_name="mcp-artifact-test",
                user_id="owner",
                session_id="mcp-image-session",
                filename=keys[0],
            )
            assert artifact is not None and artifact.inline_data is not None
            assert function_response is not None
            return function_response, keys, artifact.inline_data.data
        finally:
            await toolset.close()

    response, keys, data = asyncio.run(_run())

    assert base64.b64encode(_PNG).decode("ascii") not in str(response)
    assert response["artifacts"][0]["key"] == keys[0]
    assert response["artifacts"][0]["source"] == "mcp_tool_result"
    assert data == _PNG
