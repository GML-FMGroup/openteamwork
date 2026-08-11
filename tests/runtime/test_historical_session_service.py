from __future__ import annotations

import asyncio
from pathlib import Path

from google.adk.events.event import Event
from google.genai import types

from openppx.runtime.adk_identity import adk_app_name_for_agent_id
from openppx.runtime.agent_access_store import AgentAccessStore, AgentRecord
from openppx.runtime.historical_session_service import HistoricalSessionService
from openppx.runtime.identity_models import ResolvedPrincipal
from openppx.runtime.identity_store import IdentityStore
from openppx.runtime.session_metadata_store import SessionMetadataStore
from openppx.runtime.session_service import SessionConfig, create_session_service


def _principal(principal_id: str, level: str) -> ResolvedPrincipal:
    return ResolvedPrincipal(
        principal_id=principal_id,
        principal_type="human",
        privilege_level=level,
        account_kind="product_user",
        display_name=f"{principal_id}@example.com",
        authenticated=True,
    )


def _service(tmp_path: Path):
    identity_db = tmp_path / "identity.db"
    identity = IdentityStore(db_path=identity_db)
    access = AgentAccessStore(db_path=identity_db)
    metadata = SessionMetadataStore(tmp_path / "metadata.db")
    sessions = create_session_service(
        SessionConfig(db_url=f"sqlite+aiosqlite:///{tmp_path / 'adk-sessions.db'}")
    )
    service = HistoricalSessionService(
        session_service=sessions,
        identity_store=identity,
        agent_access_store=access,
        session_metadata=metadata,
    )
    return service, sessions, identity, access, metadata


async def _seed_session(
    sessions,
    *,
    agent_id: str,
    user_id: str,
    session_id: str,
    messages: list[tuple[str, str, float, dict[str, object] | None]],
) -> None:
    session = await sessions.create_session(
        app_name=adk_app_name_for_agent_id(agent_id),
        user_id=user_id,
        session_id=session_id,
    )
    for index, (author, text, timestamp, custom_metadata) in enumerate(messages):
        await sessions.append_event(
            session=session,
            event=Event(
                id=f"event-{session_id}-{index}",
                invocation_id=f"inv-{session_id}-{index}",
                author=author,
                timestamp=timestamp,
                content=types.Content(
                    role="user" if author == "user" else "model",
                    parts=[types.Part.from_text(text=text)],
                ),
                custom_metadata=custom_metadata,
            ),
        )


def _seed_identity(identity: IdentityStore, access: AgentAccessStore) -> None:
    for user_id, level in (("manager", "high"), ("member", "medium"), ("root-user", "root")):
        identity.put_principal(_principal(user_id, level))
    access.upsert_agent_record(
        AgentRecord("manager-high", "Manager", "high", "manager")
    )
    access.upsert_agent_record(
        AgentRecord("member-low", "Larry", "low", "member")
    )
    access.upsert_agent_record(
        AgentRecord("root-low", "Root private", "low", "root-user")
    )


def test_search_matches_chinese_substrings_and_attachment_markers_without_attachment_body(
    tmp_path: Path,
) -> None:
    service, sessions, identity, access, metadata = _service(tmp_path)
    _seed_identity(identity, access)
    asyncio.run(
        _seed_session(
            sessions,
            agent_id="member-low",
            user_id="member",
            session_id="removed-session",
            messages=[
                ("user", "请总结一下大模型创业政策趋势", 1_786_380_000.0, None),
                (
                    "user",
                    "[Attachment: policy.docx]\nFormat: Word document\n\n机密附件正文不应被检索\n[End attachment]",
                    1_786_380_010.0,
                    {
                        "clientAttachments": [
                            {"contentPartIndex": 0, "fileName": "policy.docx"}
                        ]
                    },
                ),
                ("assistant", "我会给出简明结论。", 1_786_380_020.0, None),
            ],
        )
    )
    metadata.update(
        session_id="removed-session",
        agent_id="member-low",
        principal_id="member",
        title="Policy",
        removed=True,
    )

    phrase = asyncio.run(
        service.search(
            source_user_id="manager",
            source_agent_id="manager-high",
            target_agent_id="member-low",
            query="大模型创业",
        )
    )
    marker = asyncio.run(
        service.search(
            source_user_id="manager",
            source_agent_id="manager-high",
            target_agent_id="member-low",
            query="policy.docx",
        )
    )
    hidden_body = asyncio.run(
        service.search(
            source_user_id="manager",
            source_agent_id="manager-high",
            target_agent_id="member-low",
            query="机密附件正文",
        )
    )

    assert phrase["ok"] is True
    assert phrase["items"][0]["citation"]["sessionId"] == "removed-session"
    assert phrase["items"][0]["sessionState"] == "removed"
    assert marker["items"][0]["text"] == "[Attachment: policy.docx]"
    assert hidden_body["items"] == []


def test_read_is_cursor_bounded_and_returns_stable_message_citations(tmp_path: Path) -> None:
    service, sessions, identity, access, _metadata = _service(tmp_path)
    _seed_identity(identity, access)
    asyncio.run(
        _seed_session(
            sessions,
            agent_id="member-low",
            user_id="member",
            session_id="session-page",
            messages=[
                ("user", "first", 1_786_380_000.0, None),
                ("assistant", "second", 1_786_380_010.0, None),
            ],
        )
    )

    first = asyncio.run(
        service.read(
            source_user_id="manager",
            source_agent_id="manager-high",
            target_agent_id="member-low",
            session_id="session-page",
            limit=1,
        )
    )
    second = asyncio.run(
        service.read(
            source_user_id="manager",
            source_agent_id="manager-high",
            target_agent_id="member-low",
            session_id="session-page",
            limit=1,
            cursor=first["nextCursor"],
        )
    )

    assert [item["text"] for item in first["items"]] == ["first"]
    assert [item["text"] for item in second["items"]] == ["second"]
    assert first["items"][0]["citation"] == {
        "agentId": "member-low",
        "ownerPrincipalId": "member",
        "sessionId": "session-page",
        "messageId": "event-session-page-0",
    }
    assert second["nextCursor"] is None


def test_list_sessions_obeys_time_range_and_includes_all_retained_lifecycle_states(
    tmp_path: Path,
) -> None:
    service, sessions, identity, access, metadata = _service(tmp_path)
    _seed_identity(identity, access)
    for session_id, timestamp in (
        ("active-session", 1_786_380_000.0),
        ("archived-session", 1_786_380_100.0),
        ("removed-session", 1_786_380_200.0),
    ):
        asyncio.run(
            _seed_session(
                sessions,
                agent_id="member-low",
                user_id="member",
                session_id=session_id,
                messages=[("user", session_id, timestamp, None)],
            )
        )
    metadata.update(
        session_id="archived-session",
        agent_id="member-low",
        principal_id="member",
        archived=True,
    )
    metadata.update(
        session_id="removed-session",
        agent_id="member-low",
        principal_id="member",
        removed=True,
    )

    result = asyncio.run(
        service.list_sessions(
            source_user_id="manager",
            source_agent_id="manager-high",
            target_agent_id="member-low",
            start_time="2026-08-10T16:00:00+00:00",
            end_time="2026-08-10T17:00:00+00:00",
        )
    )

    assert {item["state"] for item in result["items"]} == {"active", "archived", "removed"}


def test_cross_agent_access_is_audited_and_cross_user_root_history_is_denied(
    tmp_path: Path,
) -> None:
    service, sessions, identity, access, _metadata = _service(tmp_path)
    _seed_identity(identity, access)
    asyncio.run(
        _seed_session(
            sessions,
            agent_id="member-low",
            user_id="member",
            session_id="member-session",
            messages=[("user", "hello", 1_786_380_000.0, None)],
        )
    )

    allowed = asyncio.run(
        service.search(
            source_user_id="manager",
            source_agent_id="manager-high",
            target_agent_id="member-low",
            query="hello",
        )
    )
    denied = asyncio.run(
        service.search(
            source_user_id="manager",
            source_agent_id="manager-high",
            target_agent_id="root-low",
            query="hello",
        )
    )

    assert allowed["ok"] is True
    assert denied["ok"] is False
    assert denied["error"]["code"] == "access_denied"
    audit = access.list_audit(agent_id="member-low", actions=("history.search",))
    assert audit[0].actor_principal_id == "manager"
    assert audit[0].details["returnedCitations"][0]["sessionId"] == "member-session"


def test_denied_name_resolution_is_terminal_non_disclosing_and_audited(tmp_path: Path) -> None:
    service, _sessions, identity, access, _metadata = _service(tmp_path)
    _seed_identity(identity, access)
    access.upsert_agent_record(
        AgentRecord("member-medium", "my-medium", "medium", "member")
    )

    result = service.resolve_agent(
        source_user_id="member",
        source_agent_id="member-low",
        display_name="my-medium",
        source_agent_privilege_level="low",
    )

    assert result == {
        "ok": False,
        "status": "not_found",
        "agentId": None,
        "ownerPrincipalId": None,
        "candidates": [],
        "terminal": True,
        "guidance": (
            "A denial is terminal for this target. Do not retry through shell, files, "
            "Skills, APIs, memory, or alternate tools. Explain the permission limit or "
            "ask the user to use an Agent with sufficient privilege."
        ),
    }
    audit = access.list_audit(agent_id="member-medium", actions=("history.resolve",))
    assert len(audit) == 1
    assert audit[0].actor_principal_id == "member"
    assert audit[0].details == {
        "allowed": False,
        "reason": "source_agent_privilege_too_low",
        "sourceAgentId": "member-low",
        "query": {"displayNameFingerprint": audit[0].details["query"]["displayNameFingerprint"]},
        "returnedCitations": [],
    }
    assert len(audit[0].details["query"]["displayNameFingerprint"]) == 24


def test_cross_agent_access_fails_closed_when_audit_cannot_be_persisted(
    tmp_path: Path,
) -> None:
    service, sessions, identity, access, _metadata = _service(tmp_path)
    _seed_identity(identity, access)
    asyncio.run(
        _seed_session(
            sessions,
            agent_id="member-low",
            user_id="member",
            session_id="member-session",
            messages=[("user", "hello", 1_786_380_000.0, None)],
        )
    )

    def fail_audit(**_kwargs: object) -> None:
        raise OSError("audit database unavailable")

    access.record_audit = fail_audit  # type: ignore[method-assign]

    result = asyncio.run(
        service.search(
            source_user_id="manager",
            source_agent_id="manager-high",
            target_agent_id="member-low",
            query="hello",
        )
    )

    assert result["ok"] is False
    assert result["error"]["code"] == "history_audit_unavailable"
