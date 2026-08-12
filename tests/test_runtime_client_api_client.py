"""Tests for the thin Action-only Client API transport."""

from __future__ import annotations

import json
from unittest.mock import patch

from openppx.runtime.client_api_client import ClientApiClient


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def test_client_invokes_action_with_auth_and_wire_context() -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured.update({"request": req, "timeout": timeout})
        return _Response({"ok": True, "result": {"status": "ready"}})

    client = ClientApiClient(base_url="http://node.test:18765", access_token="secret")
    with patch("openppx.runtime.client_api_client.request.urlopen", side_effect=fake_urlopen):
        payload = client.invoke_action(
            "setup.status",
            {},
            request_id="req_1",
            correlation_id="corr_1",
        )

    assert payload["ok"] is True
    request = captured["request"]
    assert request.full_url == "http://node.test:18765/api/v1/actions/invoke"
    assert request.headers["Authorization"] == "Bearer secret"
    assert json.loads(request.data) == {
        "actionId": "setup.status",
        "input": {},
        "confirmed": False,
        "requestId": "req_1",
        "correlationId": "corr_1",
    }


def test_client_reads_filtered_action_catalog() -> None:
    captured = {}

    def fake_urlopen(req, timeout):
        captured["request"] = req
        return _Response({"ok": True, "result": {"items": []}})

    client = ClientApiClient(base_url="http://node.test:18765")
    with patch("openppx.runtime.client_api_client.request.urlopen", side_effect=fake_urlopen):
        payload = client.action_catalog(namespace="operations", projection="slash", agent_id="writer")

    assert payload["ok"] is True
    assert captured["request"].full_url.endswith(
        "/api/v1/actions?namespace=operations&projection=slash&agent_id=writer"
    )
