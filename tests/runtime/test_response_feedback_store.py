from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from openppx.runtime.response_feedback_store import ResponseFeedbackStore


def test_feedback_upsert_switch_and_clear_preserve_one_user_response_record(tmp_path: Path) -> None:
    now = [1_700_000_000_000]
    store = ResponseFeedbackStore(
        tmp_path / "sessions.db",
        clock_ms=lambda: now[0],
    )

    created = store.set(
        principal_id="user-1",
        agent_id="writer",
        session_id="session-1",
        response_id="run-1",
        run_id="run-1",
        message_id="message-live",
        rating="up",
    )
    now[0] += 1_000
    switched = store.set(
        principal_id="user-1",
        agent_id="writer",
        session_id="session-1",
        response_id="run-1",
        run_id="run-1",
        message_id="message-persisted",
        rating="down",
    )

    assert switched is not None
    assert created is not None
    assert switched.feedback_id == created.feedback_id
    assert switched.created_at_ms == created.created_at_ms
    assert switched.updated_at_ms == now[0]
    assert switched.rating == "down"
    assert switched.message_id == "message-persisted"
    assert store.get("user-1", "session-1", "run-1") == switched

    assert store.set(
        principal_id="user-1",
        agent_id="writer",
        session_id="session-1",
        response_id="run-1",
        run_id="run-1",
        message_id="message-persisted",
        rating=None,
    ) is None
    assert store.get("user-1", "session-1", "run-1") is None


def test_feedback_is_unique_per_principal_and_response(tmp_path: Path) -> None:
    store = ResponseFeedbackStore(tmp_path / "sessions.db")

    for principal_id, rating in (("user-1", "up"), ("user-2", "down")):
        store.set(
            principal_id=principal_id,
            agent_id="writer",
            session_id="session-1",
            response_id="run-1",
            run_id="run-1",
            message_id="message-1",
            rating=rating,
        )

    assert store.list_for_session("user-1", "session-1") == {"run-1": "up"}
    assert store.list_for_session("user-2", "session-1") == {"run-1": "down"}
    with sqlite3.connect(tmp_path / "sessions.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM response_feedback").fetchone()[0] == 2


@pytest.mark.parametrize("rating", ["like", "dislike", "", "UP"])
def test_feedback_rejects_unknown_ratings(tmp_path: Path, rating: str) -> None:
    store = ResponseFeedbackStore(tmp_path / "sessions.db")

    with pytest.raises(ValueError, match="up or down"):
        store.set(
            principal_id="user-1",
            agent_id="writer",
            session_id="session-1",
            response_id="run-1",
            run_id="run-1",
            message_id="message-1",
            rating=rating,
        )
