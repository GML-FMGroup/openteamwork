"""Tests for transport-independent visible Session history projection."""

from __future__ import annotations

import types as pytypes

from google.genai import types

from openppx.runtime.session_history import project_visible_history


def _event(author: str, text: str, invocation_id: str, *, thought: bool = False):
    return pytypes.SimpleNamespace(
        author=author,
        invocation_id=invocation_id,
        timestamp=1_700_000_000.0,
        actions=None,
        content=types.Content(parts=[types.Part(text=text, thought=thought)]),
    )


def test_history_projects_recent_visible_text_and_hides_internal_prefix() -> None:
    session = pytypes.SimpleNamespace(
        events=[
            _event(
                "user",
                "Current request time: now\nUse this as the reference 'now' for relative time expressions.\n\nhello",
                "inv-1",
            ),
            _event("agent", "internal", "inv-1", thought=True),
            _event("agent", "reply", "inv-1"),
            _event("user", "next", "inv-2"),
        ]
    )

    items = project_visible_history(session, limit=2)

    assert [(item["role"], item["text"]) for item in items] == [
        ("assistant", "reply"),
        ("user", "next"),
    ]
    assert items[0]["invocationId"] == "inv-1"
    assert items[0]["timestamp"] == "2023-11-14T22:13:20+00:00"
