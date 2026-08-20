from __future__ import annotations

from types import SimpleNamespace
from typing import Any, Callable
from pathlib import Path

import pytest

from openppx.control_plane import build_control_plane
from openppx.control_plane.automation_actions import register_automation_actions
from openppx.runtime.client_api_auth import (
    ClientApiAuthPolicy,
    is_loopback_bind_host,
    resolve_client_api_access_token,
    validate_client_api_bind,
)
from openppx.runtime.client_api_service import ClientApiCoordinator, _ClientApiHandler
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


def _account_service(
    tmp_path: Path,
    *,
    clock_ms: Callable[[], int] | None = None,
) -> UserAccountService:
    service = UserAccountService(db_path=tmp_path / "identity.db", clock_ms=clock_ms)
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
    coordinator: object | None = None,
):
    handler = object.__new__(_ClientApiHandler)
    resolved_coordinator = coordinator or SimpleNamespace(
        user_accounts=service,
        login_rate_limiter=login_rate_limiter,
        action_input_declares_field=lambda action_id, field_name: (
            field_name == "userId"
            and action_id in {"goal.list", "automation.list"}
        ),
    )
    handler.server = SimpleNamespace(
        auth_policy=ClientApiAuthPolicy(access_token="deployment-token"),
        coordinator=resolved_coordinator,
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
                    },
                    "expiresAtMs": login.expires_at_ms,
                },
            },
            {},
        )
    ]


def test_explicit_activity_endpoint_advances_and_returns_the_idle_deadline(tmp_path: Path) -> None:
    now = [1_700_000_000_000]
    service = UserAccountService(
        db_path=tmp_path / "identity.db",
        clock_ms=lambda: now[0],
    )
    service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="high",
    )
    login = service.authenticate("jiang@example.com", "correct horse battery staple")
    now[0] += 45 * 60 * 1000
    handler, responses = _handler(path="/api/v1/auth/activity", service=service)
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}
    handler._read_json_body = lambda: {}

    handler.do_POST()

    assert responses == [
        (
            200,
            {"ok": True, "data": {"expiresAtMs": now[0] + 60 * 60 * 1000}},
            {},
        )
    ]


def test_response_feedback_route_binds_the_authenticated_user(tmp_path: Path) -> None:
    service = _account_service(tmp_path)
    login = service.authenticate("jiang@example.com", "correct horse battery staple")
    calls: list[dict[str, object]] = []
    coordinator = SimpleNamespace(
        user_accounts=service,
        login_rate_limiter=LoginRateLimiter(),
        set_response_feedback=lambda **kwargs: (
            calls.append(kwargs)
            or {
                "ok": True,
                "data": {
                    "session_id": kwargs["session_id"],
                    "response_id": kwargs["response_id"],
                    "rating": kwargs["rating"],
                },
            }
        ),
    )
    handler, responses = _handler(
        path="/api/v1/sessions/session-1/responses/run-1/feedback",
        service=service,
        coordinator=coordinator,
    )
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}
    handler._read_json_body = lambda: {
        "messageId": "message-1",
        "runId": "run-1",
        "rating": "down",
    }

    handler.do_POST()

    assert responses[0][0] == 200
    assert calls == [{
        "session_id": "session-1",
        "response_id": "run-1",
        "message_id": "message-1",
        "run_id": "run-1",
        "rating": "down",
        "user_id": login.account.user_id,
    }]


def test_response_feedback_route_rejects_a_deployment_token(tmp_path: Path) -> None:
    service = _account_service(tmp_path)
    handler, responses = _handler(
        path="/api/v1/sessions/session-1/responses/run-1/feedback",
        service=service,
    )
    handler.headers = {"Authorization": "Bearer deployment-token"}
    handler._read_json_body = lambda: pytest.fail("unauthenticated feedback body must not be used")

    handler.do_POST()

    assert responses[0][0] == 401
    assert responses[0][1]["error"]["code"] == "UNAUTHORIZED"


def test_logout_revokes_only_the_presented_user_session(tmp_path: Path) -> None:
    service = _account_service(tmp_path)
    login = service.authenticate("jiang@example.com", "correct horse battery staple")
    handler, responses = _handler(path="/api/v1/auth/logout", service=service)
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}
    handler._read_json_body = lambda: {}

    handler.do_POST()

    assert responses[0][:2] == (200, {"ok": True, "data": {"loggedOut": True}})
    assert service.resolve_session(login.access_token) is None


def test_logout_is_idempotent_for_an_expired_user_session(tmp_path: Path) -> None:
    now = [1_700_000_000_000]
    service = _account_service(tmp_path, clock_ms=lambda: now[0])
    login = service.authenticate("jiang@example.com", "correct horse battery staple")
    now[0] = login.expires_at_ms + 1
    handler, responses = _handler(path="/api/v1/auth/logout", service=service)
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}
    handler._read_json_body = lambda: {}

    handler.do_POST()

    assert responses[0][:2] == (200, {"ok": True, "data": {"loggedOut": True}})
    assert service.logout(login.access_token) is False


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
    ("action_id", "raw_input"),
    [
        ("automation.template.list", {}),
        ("automation.template.read", {"templateId": "morning-brief"}),
    ],
)
def test_authenticated_identity_binding_preserves_user_independent_automation_inputs(
    tmp_path: Path,
    action_id: str,
    raw_input: dict[str, object],
) -> None:
    service = _account_service(tmp_path)
    account = service.authenticate(
        "jiang@example.com",
        "correct horse battery staple",
    ).account
    handler, _responses = _handler(path="/api/v1/actions/invoke", service=service)

    assert handler._bind_action_identity(action_id, raw_input, account) == raw_input


def test_authenticated_identity_binding_keeps_user_owned_automation_scoped(
    tmp_path: Path,
) -> None:
    service = _account_service(tmp_path)
    account = service.authenticate(
        "jiang@example.com",
        "correct horse battery staple",
    ).account
    handler, _responses = _handler(path="/api/v1/actions/invoke", service=service)

    assert handler._bind_action_identity("automation.list", {}, account) == {
        "userId": account.user_id,
    }


def test_authenticated_template_list_runs_through_the_strict_action_schema(
    tmp_path: Path,
) -> None:
    service = _account_service(tmp_path)
    login = service.authenticate(
        "jiang@example.com",
        "correct horse battery staple",
    )
    control_plane = build_control_plane(tmp_path, product_version="test")
    register_automation_actions(
        control_plane.registry,
        SimpleNamespace(templates=lambda: ()),
    )
    coordinator = ClientApiCoordinator(
        data_dir=tmp_path,
        control_plane=control_plane,
        user_accounts=service,
    )
    handler, responses = _handler(
        path="/api/v1/actions/invoke",
        service=service,
        coordinator=coordinator,
    )
    handler.headers = {"Authorization": f"Bearer {login.access_token}"}
    handler._read_json_body = lambda: {
        "requestId": "req_template_list",
        "correlationId": "corr_template_list",
        "actionId": "automation.template.list",
        "input": {},
        "confirmed": False,
    }

    handler.do_POST()

    assert responses[0][0] == 200
    assert responses[0][1]["ok"] is True
    assert responses[0][1]["result"] == {"items": []}


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
