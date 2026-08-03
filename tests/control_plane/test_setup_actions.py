from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from openppx.actions import ActionContext
from openppx.config import InMemorySecretStore
from openppx.control_plane import build_control_plane

from tests.setup.test_setup_service import setup_payload


def setup_context(*, confirmed: bool = False) -> ActionContext:
    permissions = frozenset(
        {
            "setup.read",
            "setup.write",
            "secret.read",
            "secret.write",
            "model.read",
            "model.write",
            "run.start",
        }
    )
    return ActionContext(
        request_id="req-setup",
        correlation_id="corr-setup",
        actor_id="principal:setup-test",
        capabilities=permissions,
        permissions=permissions,
        confirmed=confirmed,
    )


class FakeSetupRuntime:
    def create_session_sync(self, agent_id: str, *, user_id: str):
        assert agent_id == "main"
        assert user_id == "ppx-client-user"
        return SimpleNamespace(id="session-first-hello")

    def hello_sync(self, agent_id: str, text: str, *, user_id: str, session_id: str) -> str:
        assert (agent_id, text, user_id, session_id) == (
            "main",
            "Hello OpenPPX",
            "ppx-client-user",
            "session-first-hello",
        )
        return "Hello from the real Runtime boundary."


def test_setup_actions_apply_empty_root_and_complete_first_hello(tmp_path: Path) -> None:
    application = build_control_plane(
        tmp_path,
        secret_store=InMemorySecretStore(),
        product_version="test",
    )
    before = application.invoke("setup.status", {}, setup_context())
    applied = application.invoke(
        "setup.apply",
        {"request": setup_payload(tmp_path)},
        setup_context(),
    )
    application.attach_runtime(FakeSetupRuntime())
    hello = application.invoke(
        "setup.hello",
        {"agentId": "main", "userId": "ppx-client-user", "text": "Hello OpenPPX"},
        setup_context(),
    )

    assert before.ok and before.data["state"] == "needs_configuration"
    assert applied.ok and applied.data["state"] == "configured"
    assert set(applied.data["revisions"]) == {"node", "agent", "profile"}
    assert hello.ok and hello.data == {
        "sessionId": "session-first-hello",
        "reply": "Hello from the real Runtime boundary.",
        "state": "ready",
    }
    ready = application.invoke("setup.status", {}, setup_context())
    assert ready.ok and ready.data["state"] == "ready"


def test_secret_action_never_returns_or_reports_value(tmp_path: Path) -> None:
    application = build_control_plane(
        tmp_path,
        secret_store=InMemorySecretStore(),
        product_version="test",
    )

    saved = application.invoke(
        "secret.put",
        {"ref": {"store": "system", "name": "test-key"}, "value": "secret-action-canary"},
        setup_context(),
    )
    status = application.invoke(
        "secret.status",
        {"ref": {"store": "system", "name": "test-key"}},
        setup_context(),
    )
    unconfirmed = application.invoke(
        "secret.delete",
        {"ref": {"store": "system", "name": "test-key"}},
        setup_context(),
    )
    deleted = application.invoke(
        "secret.delete",
        {"ref": {"store": "system", "name": "test-key"}},
        setup_context(confirmed=True),
    )

    assert saved.ok and saved.data["state"] == "available"
    assert status.ok and status.data["state"] == "available"
    assert unconfirmed.error is not None and unconfirmed.error.code == "confirmation_required"
    assert deleted.ok and deleted.data["state"] == "missing"
    assert "secret-action-canary" not in repr((saved, status, unconfirmed, deleted))


def test_setup_hello_fails_closed_before_apply(tmp_path: Path) -> None:
    application = build_control_plane(
        tmp_path,
        secret_store=InMemorySecretStore(),
        product_version="test",
    )
    application.attach_runtime(FakeSetupRuntime())

    result = application.invoke(
        "setup.hello",
        {"agentId": "main", "userId": "ppx-client-user"},
        setup_context(),
    )

    assert result.error is not None
    assert result.error.code == "setup_incomplete"
