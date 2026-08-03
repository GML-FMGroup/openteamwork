"""Action Registry identity, catalog, and availability tests."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from openppx.actions import (
    ActionContext,
    ActionRegistrationError,
    ActionRegistry,
    ActionSpec,
)


class EmptyInput(BaseModel):
    """Strict no-argument Action input."""

    model_config = ConfigDict(extra="forbid")


def spec(action_id: str = "system.status", *, namespace: str = "system") -> ActionSpec:
    """Return one minimal core Action spec."""
    return ActionSpec(
        action_id=action_id,
        namespace=namespace,
        title="Status",
        description="Return Node status.",
        input_model=EmptyInput,
        scope="node",
        required_capabilities=frozenset({"system.read"}),
        permission="system.read",
        projections=("cli", "desktop", "mobile"),
    )


def context(*, capabilities: frozenset[str] = frozenset({"system.read"})) -> ActionContext:
    """Return a deterministic caller context."""
    return ActionContext(
        request_id="req_registry",
        correlation_id="corr_registry",
        actor_id="local:test",
        capabilities=capabilities,
        permissions=frozenset({"system.read"}),
    )


def test_registry_rejects_duplicate_action_ids() -> None:
    registry = ActionRegistry()
    registry.register(spec(), lambda _context, _input: {"state": "ready"})

    with pytest.raises(ActionRegistrationError, match="already registered"):
        registry.register(spec(), lambda _context, _input: {"state": "ready"})


def test_spec_rejects_namespace_mismatch_and_invalid_ids() -> None:
    with pytest.raises(ValueError, match="namespace"):
        spec(action_id="model.status", namespace="system")
    with pytest.raises(ValueError, match="action_id"):
        spec(action_id="System.Status")


def test_catalog_is_sorted_filterable_and_reports_availability() -> None:
    registry = ActionRegistry()
    registry.register(
        spec("system.status"),
        lambda _context, _input: {"state": "ready"},
    )
    registry.register(
        spec("system.doctor"),
        lambda _context, _input: {},
        availability=lambda _context: "doctor_not_configured",
    )

    items = registry.catalog(context(), namespace="system")

    assert [item.spec.action_id for item in items] == ["system.doctor", "system.status"]
    assert items[0].available is False
    assert items[0].availability_reason == "doctor_not_configured"
    assert items[1].available is True


def test_catalog_marks_missing_capability_without_hiding_action() -> None:
    registry = ActionRegistry()
    registry.register(spec(), lambda _context, _input: {})

    item = registry.catalog(context(capabilities=frozenset()))[0]

    assert item.available is False
    assert item.availability_reason == "capability_required"


def test_catalog_marks_missing_permission_without_client_side_rules() -> None:
    registry = ActionRegistry()
    registry.register(spec(), lambda _context, _input: {})
    caller = ActionContext(
        request_id="req_permission",
        correlation_id="corr_permission",
        actor_id="local:test",
        capabilities=frozenset({"system.read"}),
        permissions=frozenset(),
    )

    item = registry.catalog(caller)[0]

    assert item.available is False
    assert item.availability_reason == "permission_denied"
