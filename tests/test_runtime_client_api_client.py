from __future__ import annotations

import json
from unittest.mock import patch

from openppx.runtime.client_api_client import ClientApiClient


class _FakeHttpResponse:
    def __init__(self, payload: dict[str, object]) -> None:
        self._raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None


def test_client_api_client_get_agent_access_builds_get_request() -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout: float):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data
        captured["timeout"] = timeout
        return _FakeHttpResponse({"ok": True, "data": {"agent": {"id": "writer"}}})

    client = ClientApiClient(base_url="http://127.0.0.1:9999", timeout_seconds=3.5)
    with patch("openppx.runtime.client_api_client.request.urlopen", side_effect=_fake_urlopen):
        payload = client.get_agent_access("writer", user_id="owner")

    assert payload["ok"] is True
    assert captured["method"] == "GET"
    assert captured["timeout"] == 3.5
    assert captured["data"] is None
    assert captured["url"] == "http://127.0.0.1:9999/api/v1/agents/writer/access?user_id=owner"


def test_client_api_client_list_memory_audit_builds_get_request() -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout: float):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data
        return _FakeHttpResponse({"ok": True, "data": {"items": []}})

    client = ClientApiClient()
    with patch("openppx.runtime.client_api_client.request.urlopen", side_effect=_fake_urlopen):
        payload = client.list_memory_audit("writer", user_id="owner", limit=25)

    assert payload["ok"] is True
    assert captured["method"] == "GET"
    assert captured["data"] is None
    assert captured["url"] == "http://127.0.0.1:8765/api/v1/agents/writer/memory/audit?user_id=owner&limit=25"


def test_client_api_client_list_access_audit_builds_get_request() -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout: float):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data
        return _FakeHttpResponse({"ok": True, "data": {"items": []}})

    client = ClientApiClient()
    with patch("openppx.runtime.client_api_client.request.urlopen", side_effect=_fake_urlopen):
        payload = client.list_access_audit("writer", user_id="owner", limit=10, category="mutation")

    assert payload["ok"] is True
    assert captured["method"] == "GET"
    assert captured["data"] is None
    assert captured["url"] == "http://127.0.0.1:8765/api/v1/agents/writer/access/audit?user_id=owner&limit=10&category=mutation"


def test_client_api_client_posts_owner_update() -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout: float):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data
        return _FakeHttpResponse({"ok": True, "data": {"agent": {"owner_principal_id": "root-user"}}})

    client = ClientApiClient()
    with patch("openppx.runtime.client_api_client.request.urlopen", side_effect=_fake_urlopen):
        payload = client.set_agent_owner("writer", "root-user", user_id="admin")

    assert payload["ok"] is True
    assert captured["method"] == "POST"
    assert captured["url"] == "http://127.0.0.1:8765/api/v1/agents/writer/access/owner"
    assert json.loads(captured["data"].decode("utf-8")) == {
        "user_id": "admin",
        "owner_principal_id": "root-user",
    }


def test_client_api_client_invokes_action_with_auth_and_wire_context() -> None:
    captured: dict[str, object] = {}

    def _fake_urlopen(req, timeout: float):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["data"] = req.data
        captured["authorization"] = req.get_header("Authorization")
        return _FakeHttpResponse({"ok": True, "actionId": "extension.list", "result": {"items": []}})

    client = ClientApiClient(base_url="http://10.0.0.8:8765", access_token="secret-token")
    with patch("openppx.runtime.client_api_client.request.urlopen", side_effect=_fake_urlopen):
        payload = client.invoke_action(
            "extension.list",
            {"kind": "skill", "agentId": None},
            confirmed=False,
            request_id="req_cli_test",
        )

    assert payload["ok"] is True
    assert captured["method"] == "POST"
    assert captured["url"] == "http://10.0.0.8:8765/api/v1/actions/invoke"
    assert captured["authorization"] == "Bearer secret-token"
    assert json.loads(captured["data"].decode("utf-8")) == {
        "actionId": "extension.list",
        "input": {"kind": "skill", "agentId": None},
        "confirmed": False,
        "requestId": "req_cli_test",
        "correlationId": "req_cli_test",
    }


def test_client_api_client_membership_mutations_cover_post_and_delete() -> None:
    calls: list[tuple[str, str, bytes | None]] = []

    def _fake_urlopen(req, timeout: float):
        calls.append((req.get_method(), req.full_url, req.data))
        return _FakeHttpResponse({"ok": True, "data": {}})

    client = ClientApiClient()
    with patch("openppx.runtime.client_api_client.request.urlopen", side_effect=_fake_urlopen):
        add_payload = client.upsert_agent_membership("writer", "alice", relation="participant", user_id="owner")
        remove_payload = client.delete_agent_membership("writer", "alice", user_id="owner")

    assert add_payload["ok"] is True
    assert remove_payload["ok"] is True
    assert calls[0][0] == "POST"
    assert calls[0][1] == "http://127.0.0.1:8765/api/v1/agents/writer/access/memberships"
    assert json.loads(calls[0][2].decode("utf-8")) == {
        "user_id": "owner",
        "principal_id": "alice",
        "relation": "participant",
    }
    assert calls[1] == (
        "DELETE",
        "http://127.0.0.1:8765/api/v1/agents/writer/access/memberships/alice?user_id=owner",
        None,
    )


def test_client_api_client_batch_membership_mutations_use_batch_endpoint() -> None:
    calls: list[tuple[str, str, bytes | None]] = []

    def _fake_urlopen(req, timeout: float):
        calls.append((req.get_method(), req.full_url, req.data))
        return _FakeHttpResponse({"ok": True, "data": {}})

    client = ClientApiClient()
    with patch("openppx.runtime.client_api_client.request.urlopen", side_effect=_fake_urlopen):
        add_payload = client.batch_add_participants("writer", ["alice", "bob"], user_id="owner", dry_run=True)
        remove_payload = client.batch_remove_participants("writer", ["alice"], user_id="owner")
        sync_payload = client.sync_participants("writer", ["alice", "carol"], user_id="owner")

    assert add_payload["ok"] is True
    assert remove_payload["ok"] is True
    assert sync_payload["ok"] is True
    assert all(call[0] == "POST" for call in calls)
    assert all(call[1] == "http://127.0.0.1:8765/api/v1/agents/writer/access/memberships/batch" for call in calls)
    assert json.loads(calls[0][2].decode("utf-8")) == {
        "user_id": "owner",
        "operation": "add",
        "principal_ids": ["alice", "bob"],
        "dry_run": True,
    }
    assert json.loads(calls[1][2].decode("utf-8")) == {
        "user_id": "owner",
        "operation": "remove",
        "principal_ids": ["alice"],
        "dry_run": False,
    }
    assert json.loads(calls[2][2].decode("utf-8")) == {
        "user_id": "owner",
        "operation": "sync",
        "principal_ids": ["alice", "carol"],
        "dry_run": False,
    }
