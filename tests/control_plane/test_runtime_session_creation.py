"""Session creation ownership guarantees at the control-plane boundary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from openppx.actions import ActionFailure
from openppx.control_plane.input_models import SessionNewInput
from openppx.control_plane.runtime_actions import _new_session
from openppx.runtime.session_metadata_store import SessionMetadataStore


class _Supervisor:
    """Minimal synchronous Session boundary used by ownership tests."""

    def __init__(self) -> None:
        self.deleted: list[tuple[str, str, str]] = []

    @staticmethod
    def create_session_sync(agent_id: str, *, user_id: str) -> SimpleNamespace:
        assert agent_id == "research"
        assert user_id == "local:user"
        return SimpleNamespace(id="session-owned", last_update_time=1_700_000_000.0)

    def delete_session_sync(
        self,
        agent_id: str,
        *,
        user_id: str,
        session_id: str,
    ) -> None:
        self.deleted.append((agent_id, user_id, session_id))


def test_new_session_persists_immutable_agent_ownership(tmp_path) -> None:
    supervisor = _Supervisor()
    metadata = SessionMetadataStore(tmp_path / "session_metadata.db")

    result = _new_session(
        supervisor,  # type: ignore[arg-type]
        metadata,
        SessionNewInput(agentId="research", userId="local:user"),
    )

    stored = metadata.get("session-owned")
    assert stored is not None
    assert stored.agent_id == "research"
    assert stored.principal_id == "local:user"
    assert result["session"]["agentId"] == "research"  # type: ignore[index]
    assert supervisor.deleted == []


def test_new_session_rolls_back_when_ownership_cannot_be_persisted(tmp_path) -> None:
    supervisor = _Supervisor()

    class _FailingMetadataStore(SessionMetadataStore):
        def update(self, **_kwargs):  # type: ignore[no-untyped-def, override]
            raise OSError("metadata storage unavailable")

    metadata = _FailingMetadataStore(tmp_path / "session_metadata.db")

    with pytest.raises(ActionFailure) as captured:
        _new_session(
            supervisor,  # type: ignore[arg-type]
            metadata,
            SessionNewInput(agentId="research", userId="local:user"),
        )

    assert captured.value.error.code == "session_metadata_unavailable"
    assert supervisor.deleted == [("research", "local:user", "session-owned")]
