from pathlib import Path

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
    assert SessionMetadataStore(db_path).get("session-1") == archived
    assert store.delete("session-1") is True
    assert store.get("session-1") is None
