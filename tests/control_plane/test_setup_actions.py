from __future__ import annotations

import json
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


def workspace_readiness_context() -> ActionContext:
    """Return the capability set available to an ordinary product user."""

    permissions = frozenset({"system.read"})
    return ActionContext(
        request_id="req-workspace-readiness",
        correlation_id="corr-workspace-readiness",
        actor_id="principal:product-user",
        capabilities=permissions,
        permissions=permissions,
        principal_id="user-product",
        privilege_level="high",
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


def test_setup_status_action_preserves_invalid_profile_diagnostic(tmp_path: Path) -> None:
    application = build_control_plane(
        tmp_path,
        secret_store=InMemorySecretStore(),
        product_version="test",
    )
    applied = application.invoke("setup.apply", {"request": setup_payload(tmp_path)}, setup_context())
    assert applied.ok
    profile_path = tmp_path / "model-profiles" / "primary" / "profile.json"
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    del payload["spec"]["displayName"]
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    result = application.invoke("setup.status", {}, setup_context())

    assert result.ok
    assert result.data["state"] == "needs_configuration"
    assert result.data["steps"]["model"] == "invalid"
    assert result.data["diagnostic"]["component"] == "model"
    assert result.data["diagnostic"]["errorKind"] == "invalid_schema"
    assert str(tmp_path) not in repr(result.data["diagnostic"])


def test_ordinary_user_reads_only_sanitized_workspace_readiness(tmp_path: Path) -> None:
    application = build_control_plane(
        tmp_path,
        secret_store=InMemorySecretStore(),
        product_version="test",
    )
    applied = application.invoke("setup.apply", {"request": setup_payload(tmp_path)}, setup_context())
    assert applied.ok

    readiness = application.invoke("setup.readiness", {}, workspace_readiness_context())
    rich_status = application.invoke("setup.status", {}, workspace_readiness_context())

    assert readiness.ok
    assert readiness.data == {
        "state": "configured",
        "workspaceReady": True,
        "steps": {
            "node": "complete",
            "agent": "complete",
            "model": "complete",
            "credential": "available",
        },
    }
    assert rich_status.ok is False
    assert rich_status.error is not None
    assert rich_status.error.code == "capability_required"
    assert str(tmp_path) not in repr(readiness.data)


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


def test_setup_hello_preserves_safe_codex_authentication_failure(tmp_path: Path) -> None:
    application = build_control_plane(
        tmp_path,
        secret_store=InMemorySecretStore(),
        product_version="test",
    )
    application.invoke("setup.apply", {"request": setup_payload(tmp_path)}, setup_context())

    class FailedRuntime(FakeSetupRuntime):
        def hello_sync(self, agent_id: str, text: str, *, user_id: str, session_id: str) -> str:
            raise RuntimeError(
                "CODEX_ERROR: Codex authentication failed on this Node. "
                "Reconnect the Codex login and try again."
            )

    application.attach_runtime(FailedRuntime())
    result = application.invoke(
        "setup.hello",
        {"agentId": "main", "userId": "ppx-client-user", "text": "Hello OpenPPX"},
        setup_context(),
    )

    assert result.error is not None
    assert result.error.code == "provider_authentication_failed"
    assert result.error.message == (
        "Codex authentication failed on this Node. Reconnect the Codex login and try again."
    )


def test_setup_hello_preserves_safe_codex_model_access_failure(tmp_path: Path) -> None:
    application = build_control_plane(
        tmp_path,
        secret_store=InMemorySecretStore(),
        product_version="test",
    )
    application.invoke("setup.apply", {"request": setup_payload(tmp_path)}, setup_context())

    class FailedRuntime(FakeSetupRuntime):
        def hello_sync(self, agent_id: str, text: str, *, user_id: str, session_id: str) -> str:
            raise RuntimeError("CODEX_ERROR: This Codex account is not allowed to use the selected model.")

    application.attach_runtime(FailedRuntime())
    result = application.invoke(
        "setup.hello",
        {"agentId": "main", "userId": "ppx-client-user", "text": "Hello OpenPPX"},
        setup_context(),
    )

    assert result.error is not None
    assert result.error.code == "provider_access_denied"
    assert result.error.message == "This Codex account is not allowed to use the selected model."


def test_setup_hello_reports_runtime_initialization_phase_without_leaking_details(tmp_path: Path) -> None:
    application = build_control_plane(
        tmp_path,
        secret_store=InMemorySecretStore(),
        product_version="test",
    )
    application.invoke("setup.apply", {"request": setup_payload(tmp_path)}, setup_context())

    class FailedSessionRuntime(FakeSetupRuntime):
        def create_session_sync(self, agent_id: str, *, user_id: str):
            raise RuntimeError("sensitive-internal-session-detail")

        def hello_sync(self, agent_id: str, text: str, *, user_id: str, session_id: str) -> str:
            raise AssertionError("The model turn must not start when Session initialization fails.")

    application.attach_runtime(FailedSessionRuntime())
    result = application.invoke(
        "setup.hello",
        {"agentId": "main", "userId": "ppx-client-user", "text": "Hello OpenPPX"},
        setup_context(),
    )

    assert result.error is not None
    assert result.error.code == "runtime_initialization_failed"
    assert result.error.message == "The Agent runtime could not create the first Session. Restart the Node and retry."
    assert result.error.details == {"phase": "session_initialization"}
    assert result.error.retryable is True
    assert "sensitive-internal-session-detail" not in repr(result)
