"""Unified PolicyContext, audit, redaction, and fault semantics."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict

from openppx.actions import ActionContext, ActionExecutor, ActionRegistry, ActionSpec
from openppx.governance import ActionAuditStore, AuditQuery, PolicyContext, redact


class Payload(BaseModel):
    """Strict Action input containing one scoped resource and test Secret."""

    model_config = ConfigDict(extra="forbid", strict=True)
    agent_id: str
    api_key: str


def _context(*, confirmed: bool = False, agent_id: str | None = None) -> ActionContext:
    return ActionContext(
        request_id="req_governance",
        correlation_id="corr_governance",
        actor_id="principal:owner",
        client_id="desktop",
        device_id="device-a",
        capabilities=frozenset({"extension.write"}),
        permissions=frozenset({"extension.write"}),
        confirmed=confirmed,
        node_id="node-a",
        agent_id=agent_id,
    )


def _executor(db_path: Path, *, handler=None) -> ActionExecutor:
    registry = ActionRegistry()
    registry.register(
        ActionSpec(
            action_id="extension.install",
            namespace="extension",
            title="Install",
            description="Install one extension.",
            input_model=Payload,
            scope="extension",
            required_capabilities=frozenset({"extension.write"}),
            permission="extension.write",
            risk="high",
            confirmation="required",
        ),
        handler or (lambda _context, value: {"installed": value.agent_id}),
    )
    return ActionExecutor(registry, audit=ActionAuditStore(db_path))


def test_policy_context_contains_explicit_scope_without_action_payload() -> None:
    spec = _executor(Path("unused.db")).registry.resolve("extension.install").spec  # type: ignore[union-attr]
    context = PolicyContext.from_action(
        spec,
        _context(agent_id="agent-a"),
        {"agentId": "agent-a", "extensionId": "demo", "apiKey": "secret"},
    )

    assert context.actor_id == "principal:owner"
    assert context.client_id == "desktop"
    assert context.device_id == "device-a"
    assert context.agent_id == "agent-a"
    assert context.extension_id == "demo"
    assert not hasattr(context, "raw_input")


def test_policy_denials_have_stable_codes_and_are_audited(tmp_path: Path) -> None:
    store = ActionAuditStore(tmp_path / "audit.db")
    executor = _executor(tmp_path / "audit.db")

    confirmation = executor.execute(
        "extension.install",
        {"agent_id": "agent-a", "api_key": "canary-secret"},
        _context(),
    )
    mismatch = executor.execute(
        "extension.install",
        {"agent_id": "agent-b", "api_key": "canary-secret"},
        _context(confirmed=True, agent_id="agent-a"),
    )

    assert confirmation.error is not None
    assert confirmation.error.code == "confirmation_required"
    assert mismatch.error is not None
    assert mismatch.error.code == "scope_mismatch"
    rows = store.list(AuditQuery(limit=10))
    assert {row["outcomeCode"] for row in rows} == {"confirmation_required", "scope_mismatch"}
    assert b"canary-secret" not in (tmp_path / "audit.db").read_bytes()


def test_successful_high_risk_action_is_one_completed_audit_fact(tmp_path: Path) -> None:
    db_path = tmp_path / "audit.db"
    executor = _executor(db_path)

    outcome = executor.execute(
        "extension.install",
        {"agent_id": "agent-a", "api_key": "canary-secret"},
        _context(confirmed=True),
    )

    assert outcome.ok is True
    rows = ActionAuditStore(db_path).list(AuditQuery(action_id="extension.install"))
    assert len(rows) == 1
    assert rows[0]["decisionCode"] == "allowed"
    assert rows[0]["outcomeCode"] == "success"
    assert rows[0]["ok"] is True
    assert b"canary-secret" not in db_path.read_bytes()


class _FailingAudit:
    def begin(self, _context, _decision):
        raise OSError("audit disk unavailable /private/canary")

    def complete(self, _audit_id, _outcome):
        raise AssertionError("complete must not run")


def test_high_risk_action_fails_closed_when_audit_is_unavailable() -> None:
    called = False

    def handler(_context, _input):
        nonlocal called
        called = True
        return {"installed": True}

    registry = ActionRegistry()
    registry.register(
        ActionSpec(
            action_id="extension.install",
            namespace="extension",
            title="Install",
            description="Install one extension.",
            input_model=Payload,
            scope="extension",
            required_capabilities=frozenset({"extension.write"}),
            permission="extension.write",
            risk="high",
            confirmation="required",
        ),
        handler,
    )

    outcome = ActionExecutor(registry, audit=_FailingAudit()).execute(
        "extension.install",
        {"agent_id": "agent-a", "api_key": "secret"},
        _context(confirmed=True),
    )

    assert outcome.error is not None
    assert outcome.error.code == "audit_unavailable"
    assert called is False


def test_redaction_handles_sensitive_keys_patterns_and_canaries() -> None:
    payload = redact(
        {
            "authorization": "Bearer abcdefghijklmnop",
            "nested": {"apiKey": "plain-value", "message": "token=sk-abcdefghijklmnop"},
            "text": "prefix exact-canary suffix",
        },
        canaries=("exact-canary",),
    )

    assert payload["authorization"] == "<redacted>"  # type: ignore[index]
    assert payload["nested"]["apiKey"] == "<redacted>"  # type: ignore[index]
    assert "sk-abcdefghijklmnop" not in str(payload)
    assert "exact-canary" not in str(payload)
