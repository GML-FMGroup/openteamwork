"""Action Registry identity, catalog, and availability tests."""

from __future__ import annotations

import pytest
from pydantic import BaseModel, ConfigDict

from openppx.actions import (
    ActionContext,
    ActionRegistrationError,
    ActionRegistry,
    ActionSpec,
    SlashCommandError,
    SlashCommandArgumentSpec,
    SlashCommandSpec,
    SlashInvocationContext,
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


def test_registry_owns_unique_slash_commands_and_projection_filtering() -> None:
    registry = ActionRegistry()
    command_spec = ActionSpec(
        action_id="system.status",
        namespace="system",
        title="Status",
        description="Return Node status.",
        input_model=EmptyInput,
        scope="node",
        required_capabilities=frozenset({"system.read"}),
        permission="system.read",
        projections=("cli", "slash", "desktop"),
        slash_commands=(
            SlashCommandSpec(
                command="/status",
                title="Show status",
                description="Display Node readiness.",
                icon="activity",
            ),
        ),
    )
    registry.register(
        command_spec,
        lambda _context, _input: {},
        slash_input=lambda _command, _args, _context: {},
    )
    registry.register(spec("system.doctor"), lambda _context, _input: {})

    resolved = registry.resolve_slash("/STATUS@openppx")
    slash_catalog = registry.catalog(context(), projection="slash")

    assert resolved.registered.spec.action_id == "system.status"
    assert resolved.command.command == "/status"
    assert [item.spec.action_id for item in slash_catalog] == ["system.status"]

    with pytest.raises(ActionRegistrationError, match="already registered"):
        registry.register(
            ActionSpec(
                action_id="system.health",
                namespace="system",
                title="Health",
                description="Return health.",
                input_model=EmptyInput,
                scope="node",
                required_capabilities=frozenset({"system.read"}),
                permission="system.read",
                projections=("slash",),
                slash_commands=command_spec.slash_commands,
            ),
            lambda _context, _input: {},
            slash_input=lambda _command, _args, _context: {},
        )


def test_slash_command_accepts_one_plugin_namespace_and_rejects_ambiguous_forms() -> None:
    command = SlashCommandSpec(
        command="/github:review",
        title="Review with GitHub",
        description="Invoke one explicitly declared Plugin Skill command.",
        icon="sparkles",
    )

    assert command.command == "/github:review"
    with pytest.raises(ValueError, match="namespace"):
        SlashCommandSpec(
            command="/github:pull:review",
            title="Invalid",
            description="Invalid nested command namespace.",
            icon="sparkles",
        )


def test_registry_rejects_slash_arguments_without_mutating_action_input() -> None:
    registry = ActionRegistry()
    command_spec = ActionSpec(
        action_id="system.help",
        namespace="system",
        title="Help",
        description="Return commands.",
        input_model=EmptyInput,
        scope="node",
        required_capabilities=frozenset({"system.read"}),
        permission="system.read",
        projections=("slash",),
        slash_commands=(
            SlashCommandSpec(
                command="/help",
                title="Help",
                description="Return commands.",
                icon="circle-help",
            ),
        ),
    )
    registry.register(
        command_spec,
        lambda _context, _input: {},
        slash_input=lambda _command, _args, _context: {},
    )

    with pytest.raises(SlashCommandError) as exc_info:
        registry.resolve_slash("/help unexpected")

    assert exc_info.value.code == "command_arguments_not_allowed"
    assert SlashInvocationContext(user_id="local:user").session_id is None


def test_registry_validates_typed_slash_arguments_and_reports_stable_usage() -> None:
    registry = ActionRegistry()
    command_spec = ActionSpec(
        action_id="session.history",
        namespace="session",
        title="History",
        description="Return recent messages.",
        input_model=EmptyInput,
        scope="session",
        required_capabilities=frozenset({"system.read"}),
        permission="system.read",
        projections=("slash",),
        slash_commands=(
            SlashCommandSpec(
                command="/history",
                title="History",
                description="Return recent messages.",
                icon="history",
                arguments=(
                    SlashCommandArgumentSpec(
                        name="limit",
                        value_type="integer",
                        description="Maximum number of messages.",
                        required=True,
                    ),
                ),
                no_args_behavior="show_usage",
            ),
        ),
    )
    registry.register(
        command_spec,
        lambda _context, _input: {},
        slash_input=lambda _command, _args, _context: {},
    )

    resolved = registry.resolve_slash("/history 25")

    assert resolved.parsed_args == (25,)
    assert resolved.command.usage == "/history <limit>"
    with pytest.raises(SlashCommandError) as missing:
        registry.resolve_slash("/history")
    assert missing.value.code == "command_usage_required"
    assert str(missing.value) == "Usage: /history <limit>"
    with pytest.raises(SlashCommandError) as wrong_type:
        registry.resolve_slash("/history many")
    assert wrong_type.value.code == "command_argument_type_invalid"
    assert str(wrong_type.value) == "Usage: /history <limit>"


def test_slash_argument_contract_rejects_ambiguous_schema() -> None:
    with pytest.raises(ValueError, match="required arguments"):
        SlashCommandSpec(
            command="/task",
            title="Task",
            description="Inspect a Task.",
            icon="list-checks",
            arguments=(
                SlashCommandArgumentSpec(
                    name="task_id",
                    value_type="resource_id",
                    description="Task identity.",
                    required=False,
                ),
                SlashCommandArgumentSpec(
                    name="operation",
                    value_type="enum",
                    description="Requested operation.",
                    required=True,
                    choices=("show", "pause"),
                ),
            ),
        )

    with pytest.raises(ValueError, match="text slash arguments"):
        SlashCommandSpec(
            command="/goal",
            title="Goal",
            description="Create a Goal.",
            icon="target",
            arguments=(
                SlashCommandArgumentSpec(
                    name="objective",
                    value_type="text",
                    description="Goal objective.",
                ),
                SlashCommandArgumentSpec(
                    name="extra",
                    value_type="string",
                    description="Invalid trailing argument.",
                ),
            ),
        )


def test_registry_preserves_multiword_text_as_one_typed_argument() -> None:
    registry = ActionRegistry()
    command_spec = ActionSpec(
        action_id="goal.command",
        namespace="goal",
        title="Goal",
        description="Manage the current Goal.",
        input_model=EmptyInput,
        scope="goal",
        required_capabilities=frozenset({"system.read"}),
        permission="system.read",
        projections=("slash",),
        slash_commands=(
            SlashCommandSpec(
                command="/goal",
                title="Goal",
                description="Create a Goal.",
                icon="target",
                arguments=(
                    SlashCommandArgumentSpec(
                        name="objective",
                        value_type="text",
                        description="Goal objective.",
                    ),
                ),
            ),
        ),
    )
    registry.register(command_spec, lambda _context, _input: {}, slash_input=lambda _command, _args, _context: {})

    resolved = registry.resolve_slash('/goal "Ship the release" with evidence')

    assert resolved.parsed_args == ("Ship the release with evidence",)
