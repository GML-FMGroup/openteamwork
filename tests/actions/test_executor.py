"""Action Executor authorization, validation, and error-integrity tests."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, StrictInt

from openppx.actions import ActionContext, ActionExecutor, ActionRegistry, ActionSpec


class CountInput(BaseModel):
    """Strict sample input."""

    model_config = ConfigDict(extra="forbid", strict=True)
    count: StrictInt


def build_executor(*, confirmation: str = "never", availability=None) -> ActionExecutor:
    """Return an executor with one deterministic Action."""
    registry = ActionRegistry()
    registry.register(
        ActionSpec(
            action_id="system.count",
            namespace="system",
            title="Count",
            description="Echo one count.",
            input_model=CountInput,
            scope="node",
            required_capabilities=frozenset({"system.read"}),
            permission="system.read",
            confirmation=confirmation,
            projections=("cli",),
        ),
        lambda _context, input_data: {"count": input_data.count},
        availability=availability,
    )
    return ActionExecutor(registry)


def context(
    *,
    capabilities: frozenset[str] = frozenset({"system.read"}),
    permissions: frozenset[str] = frozenset({"system.read"}),
    confirmed: bool = False,
) -> ActionContext:
    """Return one Action caller context."""
    return ActionContext(
        request_id="req_executor",
        correlation_id="corr_executor",
        actor_id="local:test",
        capabilities=capabilities,
        permissions=permissions,
        confirmed=confirmed,
    )


def test_executor_validates_and_returns_structured_success() -> None:
    result = build_executor().execute("system.count", {"count": 3}, context())

    assert result.ok is True
    assert result.data == {"count": 3}
    assert result.error is None


def test_executor_rejects_capability_permission_confirmation_and_input() -> None:
    missing_capability = build_executor().execute(
        "system.count",
        {"count": 1},
        context(capabilities=frozenset()),
    )
    denied = build_executor().execute(
        "system.count",
        {"count": 1},
        context(permissions=frozenset()),
    )
    confirmation = build_executor(confirmation="required").execute(
        "system.count",
        {"count": 1},
        context(),
    )
    invalid = build_executor().execute("system.count", {"count": "3", "secret": "sk-leak"}, context())

    assert missing_capability.error is not None
    assert missing_capability.error.code == "capability_required"
    assert denied.error is not None
    assert denied.error.code == "permission_denied"
    assert confirmation.error is not None
    assert confirmation.error.code == "confirmation_required"
    assert invalid.error is not None
    assert invalid.error.code == "invalid_action_input"
    assert "sk-leak" not in str(invalid)


def test_executor_rejects_unavailable_and_unknown_actions() -> None:
    unavailable = build_executor(availability=lambda _context: "node_not_ready").execute(
        "system.count", {"count": 1}, context()
    )
    missing = build_executor().execute("system.missing", {}, context())

    assert unavailable.error is not None
    assert unavailable.error.code == "action_unavailable"
    assert unavailable.error.details == {"reason": "node_not_ready"}
    assert missing.error is not None
    assert missing.error.code == "action_not_found"


def test_executor_does_not_leak_unexpected_handler_exception() -> None:
    registry = ActionRegistry()
    registry.register(
        ActionSpec(
            action_id="system.fail",
            namespace="system",
            title="Fail",
            description="Fail safely.",
            input_model=CountInput,
            scope="node",
            required_capabilities=frozenset(),
            permission="system.read",
        ),
        lambda _context, _input: (_ for _ in ()).throw(RuntimeError("secret backend /private/path")),
    )

    result = ActionExecutor(registry).execute("system.fail", {"count": 1}, context())

    assert result.error is not None
    assert result.error.code == "internal_error"
    assert "secret backend" not in str(result)
    assert "/private/path" not in str(result)


def test_action_spec_requires_confirmation_for_high_risk_actions() -> None:
    import pytest

    with pytest.raises(ValueError, match="high-risk"):
        ActionSpec(
            action_id="system.danger",
            namespace="system",
            title="Danger",
            description="A dangerous operation.",
            input_model=CountInput,
            scope="node",
            required_capabilities=frozenset({"system.write"}),
            permission="system.write",
            risk="high",
        )
