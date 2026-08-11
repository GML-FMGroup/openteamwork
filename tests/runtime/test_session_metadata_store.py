from pathlib import Path
import sqlite3

from openppx.runtime.session_metadata_store import SessionMetadataStore


def test_session_metadata_persists_title_and_archive_state(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    store = SessionMetadataStore(db_path)

    renamed = store.update(
        session_id="session-1",
        agent_id="main",
        principal_id="ppx-client-user",
        title="Planning notes",
    )
    archived = store.update(
        session_id="session-1",
        agent_id="main",
        principal_id="ppx-client-user",
        archived=True,
    )

    assert renamed.title == "Planning notes"
    assert archived.title == "Planning notes"
    assert archived.archived is True
    assert archived.removed is False
    assert SessionMetadataStore(db_path).get("session-1") == archived
    assert store.delete("session-1") is True
    assert store.get("session-1") is None


def test_session_metadata_marks_a_session_removed_without_deleting_its_row(tmp_path: Path) -> None:
    store = SessionMetadataStore(tmp_path / "sessions.db")
    store.update(
        session_id="session-removed",
        agent_id="main",
        principal_id="owner",
        title="Retained history",
        archived=True,
    )

    removed = store.update(
        session_id="session-removed",
        agent_id="main",
        principal_id="owner",
        removed=True,
    )

    assert removed.title == "Retained history"
    assert removed.archived is False
    assert removed.removed is True
    assert SessionMetadataStore(store.db_path).get("session-removed") == removed


def test_session_metadata_migrates_existing_rows_to_active_not_removed(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    with sqlite3.connect(db_path) as connection:
        connection.execute(
            """
            CREATE TABLE session_metadata (
                session_id TEXT PRIMARY KEY,
                agent_id TEXT NOT NULL,
                principal_id TEXT NOT NULL,
                title TEXT,
                archived INTEGER NOT NULL DEFAULT 0,
                model_profile_id TEXT,
                model_selection_revision INTEGER NOT NULL DEFAULT 0,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.execute(
            "INSERT INTO session_metadata VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("legacy", "main", "owner", "Legacy", 1, None, 0, "2026-08-11T00:00:00+00:00"),
        )

    migrated = SessionMetadataStore(db_path).get("legacy")

    assert migrated is not None
    assert migrated.archived is True
    assert migrated.removed is False


def test_session_metadata_persists_model_profile_selection_and_revision(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    store = SessionMetadataStore(db_path)

    selected = store.update_model_profile(
        session_id="session-1",
        agent_id="main",
        principal_id="ppx-client-user",
        model_profile_id="reasoning",
    )

    assert selected.model_profile_id == "reasoning"
    assert selected.model_selection_revision == 1
    assert SessionMetadataStore(db_path).get("session-1") == selected

    reset = store.update_model_profile(
        session_id="session-1",
        agent_id="main",
        principal_id="ppx-client-user",
        model_profile_id=None,
        expected_revision=1,
    )

    assert reset.model_profile_id is None
    assert reset.model_selection_revision == 2
