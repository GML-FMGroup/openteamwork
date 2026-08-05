"""Plugin Hook trust and bounded execution tests."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from openppx.extensions.plugin_hooks import (
    PluginHookExecutor,
    PluginHookSnapshot,
    PluginHookSnapshotEntry,
    PluginHookRejected,
    parse_plugin_hooks,
)


def _snapshot(tmp_path: Path, command: str) -> PluginHookSnapshot:
    hooks = parse_plugin_hooks(
        [
            {
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "shell|write_file",
                            "hooks": [{"type": "command", "command": command, "timeout": 2}],
                        }
                    ]
                }
            }
        ]
    )
    entry = PluginHookSnapshotEntry(
        plugin_id="fixture",
        plugin_digest="sha256:" + "1" * 64,
        content_root=tmp_path / "content",
        data_root=tmp_path / "data",
        hooks=hooks,
    )
    entry.content_root.mkdir()
    return PluginHookSnapshot("sha256:" + "2" * 64, (entry,))


def test_trusted_hook_receives_standard_plugin_paths_and_json(tmp_path: Path) -> None:
    snapshot = _snapshot(
        tmp_path,
        "read payload; printf '%s' \"$payload\" > \"$PLUGIN_DATA/input.json\"; "
        "printf '%s' \"$PLUGIN_ROOT\" > \"$PLUGIN_DATA/root.txt\"",
    )
    results = asyncio.run(
        PluginHookExecutor(snapshot).emit(
            "PreToolUse",
            {"tool_name": "shell", "tool_input": {"command": "pwd"}},
            match_value="shell",
            cwd=tmp_path,
        )
    )

    assert results[0].exit_code == 0
    assert '"hook_event_name": "PreToolUse"' in (tmp_path / "data" / "input.json").read_text()
    assert (tmp_path / "data" / "root.txt").read_text() == str(tmp_path / "content")


def test_blocking_hook_nonzero_exit_rejects_tool(tmp_path: Path) -> None:
    snapshot = _snapshot(tmp_path, "exit 7")
    with pytest.raises(PluginHookRejected, match="exit 7"):
        asyncio.run(
            PluginHookExecutor(snapshot).emit(
                "PreToolUse",
                {"tool_name": "write_file"},
                match_value="write_file",
                cwd=tmp_path,
            )
        )


def test_async_and_prompt_handlers_are_parsed_but_not_executed(tmp_path: Path) -> None:
    hooks = parse_plugin_hooks(
        [
            {
                "Stop": [
                    {
                        "hooks": [
                            {"type": "command", "command": "exit 9", "async": True},
                            {"type": "prompt", "prompt": "Review the result"},
                        ]
                    }
                ]
            }
        ]
    )
    assert hooks.handler_count == 2
    assert hooks.executable_count == 0
