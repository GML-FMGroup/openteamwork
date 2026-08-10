from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from openppx.runtime.client_api_auth import (
    ClientApiAuthPolicy,
    is_loopback_bind_host,
    resolve_client_api_access_token,
    validate_client_api_bind,
)
from openppx.runtime.client_api_service import _ClientApiHandler


def test_loopback_detection_covers_ipv4_ipv6_and_localhost() -> None:
    assert is_loopback_bind_host("127.0.0.1") is True
    assert is_loopback_bind_host("::1") is True
    assert is_loopback_bind_host("localhost") is True
    assert is_loopback_bind_host("0.0.0.0") is False
    assert is_loopback_bind_host("192.168.1.20") is False


def test_non_loopback_bind_requires_a_token() -> None:
    with pytest.raises(ValueError, match="Refusing non-loopback"):
        validate_client_api_bind(host="0.0.0.0", access_token="")

    validate_client_api_bind(host="0.0.0.0", access_token="secret")
    validate_client_api_bind(host="127.0.0.1", access_token="")


def test_token_resolution_rejects_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENTEAMWORK_CLIENT_API_TOKEN", "bad token")

    with pytest.raises(ValueError, match="must not contain whitespace"):
        resolve_client_api_access_token()


def test_bearer_policy_requires_an_exact_constant_time_candidate() -> None:
    policy = ClientApiAuthPolicy(access_token="correct-secret")

    assert policy.authorizes("Bearer correct-secret") is True
    assert policy.authorizes("bearer correct-secret") is True
    assert policy.authorizes("Bearer wrong-secret") is False
    assert policy.authorizes("correct-secret") is False
    assert policy.authorizes(None) is False
    assert ClientApiAuthPolicy().authorizes(None) is True


@pytest.mark.parametrize(
    ("method_name", "path"),
    [
        ("do_GET", "/api/v1/agents"),
        ("do_GET", "/api/v1/runs/run_1/events"),
        ("do_POST", "/api/v1/agents/writer/sessions"),
        ("do_DELETE", "/api/v1/agents/writer/access/memberships/alice"),
    ],
)
def test_handler_returns_401_before_protected_routes(method_name: str, path: str) -> None:
    handler = object.__new__(_ClientApiHandler)
    handler.server = SimpleNamespace(auth_policy=ClientApiAuthPolicy(access_token="secret"))
    handler.client_address = ("192.168.1.20", 54321)
    handler.path = path
    handler.headers = {}
    responses: list[tuple[int, dict[str, Any], dict[str, str]]] = []
    handler._send_json = lambda status, payload, **kwargs: responses.append(
        (status, payload, kwargs.get("extra_headers") or {})
    )

    getattr(handler, method_name)()

    assert responses == [
        (
            401,
            {
                "ok": False,
                "error": {
                    "code": "UNAUTHORIZED",
                    "message": "A valid Client API bearer token is required.",
                    "details": {},
                },
            },
            {"WWW-Authenticate": 'Bearer realm="openppx-client-api"'},
        )
    ]


def test_handler_keeps_health_public_and_minimal() -> None:
    handler = object.__new__(_ClientApiHandler)
    coordinator = SimpleNamespace(health=lambda *, public: {"ok": True, "data": {"public": public}})
    handler.server = SimpleNamespace(
        auth_policy=ClientApiAuthPolicy(access_token="secret"),
        coordinator=coordinator,
    )
    handler.client_address = ("192.168.1.20", 54321)
    handler.path = "/api/v1/health"
    handler.headers = {}
    responses: list[tuple[int, dict[str, Any]]] = []
    handler._send_json = lambda status, payload, **_kwargs: responses.append((status, payload))

    handler.do_GET()

    assert responses == [(200, {"ok": True, "data": {"public": True}})]


def test_handler_returns_full_health_to_an_authenticated_client() -> None:
    handler = object.__new__(_ClientApiHandler)
    coordinator = SimpleNamespace(health=lambda *, public: {"ok": True, "data": {"public": public}})
    handler.server = SimpleNamespace(
        auth_policy=ClientApiAuthPolicy(access_token="secret"),
        coordinator=coordinator,
    )
    handler.client_address = ("192.168.1.20", 54321)
    handler.path = "/api/v1/health"
    handler.headers = {"Authorization": "Bearer secret"}
    responses: list[tuple[int, dict[str, Any]]] = []
    handler._send_json = lambda status, payload, **_kwargs: responses.append((status, payload))

    handler.do_GET()

    assert responses == [(200, {"ok": True, "data": {"public": False}})]
