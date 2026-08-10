from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from pathlib import Path

import pytest

from openppx.runtime.client_api_auth import (
    ClientApiAuthPolicy,
    is_loopback_bind_host,
    resolve_client_api_access_token,
    validate_client_api_bind,
)
from openppx.runtime.client_api_service import _ClientApiHandler
from openppx.runtime.user_accounts import LoginRateLimiter, UserAccountService


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


def _account_service(tmp_path: Path) -> UserAccountService:
    service = UserAccountService(db_path=tmp_path / "identity.db")
    service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="high",
    )
    return service


def _handler(
    *,
    path: str,
    service: UserAccountService,
    address: str = "127.0.0.1",
    login_rate_limiter: LoginRateLimiter | None = None,
):
    handler = object.__new__(_ClientApiHandler)
    handler.server = SimpleNamespace(
        auth_policy=ClientApiAuthPolicy(access_token="deployment-token"),
        coordinator=SimpleNamespace(
            user_accounts=service,
            login_rate_limiter=login_rate_limiter,
        ),
    )
    handler.client_address = (address, 54321)
    handler.path = path
    handler.headers = {}
    responses: list[tuple[int, dict[str, Any], dict[str, str]]] = []
    handler._send_json = lambda status, payload, **kwargs: responses.append(
        (status, payload, kwargs.get("extra_headers") or {})
    )
    return handler, responses


def test_loopback_login_issues_user_session_without_deployment_token(tmp_path: Path) -> None:
    handler, responses = _handler(path="/api/v1/auth/login", service=_account_service(tmp_path))
    handler._read_json_body = lambda: {
        "email": "JIANG@example.com",
        "secret": "correct horse battery staple",
    }

    handler.do_POST()

    assert responses[0][0] == 200
    data = responses[0][1]["data"]
    assert data["accessToken"].startswith("otw_session_")
    assert data["user"]["email"] == "jiang@example.com"
    assert data["user"]["privilegeLevel"] == "high"
    assert "secret" not in str(responses).lower()


def test_direct_remote_plaintext_login_is_rejected_before_reading_secret(tmp_path: Path) -> None:
    handler, responses = _handler(
        path="/api/v1/auth/login",
        service=_account_service(tmp_path),
        address="192.168.1.20",
    )
    handler._read_json_body = lambda: pytest.fail("plaintext login body must not be read")

    handler.do_POST()

    assert responses[0][0] == 426
    assert responses[0][1]["error"]["code"] == "HTTPS_REQUIRED"


def test_current_user_requires_and_resolves_user_session_token(tmp_path: Path) -> None:
    service = _account_service(tmp_path)
    login = service.authenticate("jiang@example.com", "correct horse battery staple")
    handler, responses = _handler(path="/api/v1/auth/me", service=service)
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}

    handler.do_GET()

    assert responses == [
        (
            200,
            {
                "ok": True,
                "data": {
                    "user": {
                        "userId": login.account.user_id,
                        "email": "jiang@example.com",
                        "privilegeLevel": "high",
                        "status": "active",
                    }
                },
            },
            {},
        )
    ]


def test_logout_revokes_only_the_presented_user_session(tmp_path: Path) -> None:
    service = _account_service(tmp_path)
    login = service.authenticate("jiang@example.com", "correct horse battery staple")
    handler, responses = _handler(path="/api/v1/auth/logout", service=service)
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}
    handler._read_json_body = lambda: {}

    handler.do_POST()

    assert responses[0][:2] == (200, {"ok": True, "data": {"loggedOut": True}})
    assert service.resolve_session(login.access_token) is None


def test_login_rate_limit_is_generic_and_success_clears_failures(tmp_path: Path) -> None:
    limiter = LoginRateLimiter(max_failures=2, window_seconds=60)
    handler, responses = _handler(
        path="/api/v1/auth/login",
        service=_account_service(tmp_path),
        login_rate_limiter=limiter,
    )
    credentials = {"email": "jiang@example.com", "secret": "incorrect secret"}
    handler._read_json_body = lambda: credentials

    handler.do_POST()
    handler.do_POST()
    handler.do_POST()

    assert [status for status, _payload, _headers in responses] == [401, 401, 429]
    assert responses[-1][1]["error"]["code"] == "LOGIN_RATE_LIMITED"
    assert responses[-1][2]["Retry-After"] == "60"
    assert "jiang@example.com" not in str(responses[-1])

    limiter.clear(handler._login_rate_key("jiang@example.com"))
    handler._read_json_body = lambda: {
        "email": "jiang@example.com",
        "secret": "correct horse battery staple",
    }
    handler.do_POST()

    assert responses[-1][0] == 200


def test_authenticated_action_rejects_a_caller_supplied_foreign_user_id(tmp_path: Path) -> None:
    service = _account_service(tmp_path)
    login = service.authenticate("jiang@example.com", "correct horse battery staple")
    handler, responses = _handler(
        path="/api/v1/actions/invoke",
        service=service,
    )
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}
    handler._read_json_body = lambda: {
        "requestId": "req_identity",
        "correlationId": "corr_identity",
        "actionId": "goal.list",
        "input": {"userId": "another-user"},
        "confirmed": False,
    }

    handler.do_POST()

    assert responses == [
        (
            403,
            {
                "ok": False,
                "error": {
                    "code": "IDENTITY_MISMATCH",
                    "message": "The request user ID does not match the authenticated user.",
                    "details": {},
                },
            },
            {},
        )
    ]


def test_root_user_can_select_a_resource_subject_but_not_spoof_agent_ownership(tmp_path: Path) -> None:
    service = UserAccountService(db_path=tmp_path / "identity.db")
    root = service.add_user(
        email="root@example.com",
        secret="root secret value",
        privilege_level="root",
    )
    login = service.authenticate("root@example.com", "root secret value")
    handler, responses = _handler(path="/api/v1/actions/invoke", service=service)
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}

    assert handler._request_user_id("target-user") == "target-user"
    assert handler._bind_action_identity(
        "goal.list",
        {"userId": "target-user"},
        root,
    ) == {"userId": "target-user"}
    assert handler._bind_action_identity(
        "agent.create",
        {"ownerPrincipalId": "target-user"},
        root,
    ) is None
    assert responses[-1][0] == 403


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/agents/writer/access/owner",
        "/api/v1/agents/writer/access/memberships",
        "/api/v1/agents/writer/access/memberships/batch",
    ],
)
def test_product_sessions_cannot_change_agent_ownership_or_memberships(
    tmp_path: Path,
    path: str,
) -> None:
    service = _account_service(tmp_path)
    login = service.authenticate("jiang@example.com", "correct horse battery staple")
    handler, responses = _handler(path=path, service=service)
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}
    handler._read_json_body = lambda: {}

    handler.do_POST()

    assert responses == [
        (
            403,
            {
                "ok": False,
                "error": {
                    "code": "AGENT_SHARING_UNSUPPORTED",
                    "message": "Changing Agent ownership or memberships is not supported for App users.",
                    "details": {},
                },
            },
            {},
        )
    ]
