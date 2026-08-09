"""Action-backed `/make-skill` command over conversation Skill authoring."""

from __future__ import annotations

from typing import Any, cast

from openppx.actions import (
    ActionError,
    ActionFailure,
    ActionRegistry,
    ActionSpec,
    SlashCommandArgumentSpec,
    SlashCommandError,
    SlashCommandSpec,
    SlashInvocationContext,
)
from openppx.skill_authoring import MakeSkillError, MakeSkillService

from .input_models import MakeSkillCommandInput


def register_make_skill_actions(registry: ActionRegistry, service: MakeSkillService | Any) -> None:
    """Register explicit current-Session drafting and publication commands."""
    registry.register(
        ActionSpec(
            action_id="skill.draft.command",
            namespace="skill",
            title="Save conversation as Skill",
            description="Draft, review, and publish one Skill from visible Session messages.",
            input_model=MakeSkillCommandInput,
            scope="session",
            required_capabilities=frozenset({"session.read", "extension.write"}),
            permission="extension.write",
            risk="medium",
            operation="mutation",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/make-skill",
                    title="Save as Skill",
                    description="Create a reviewable Skill draft from this Session.",
                    icon="sparkles",
                    arg_hint="[focus|approve|revise ...|cancel]",
                    arguments=(
                        SlashCommandArgumentSpec(
                            name="request",
                            value_type="text",
                            description="Optional focus or review operation.",
                        ),
                    ),
                    order=81,
                ),
            ),
        ),
        lambda _context, value: _dispatch(service, cast(MakeSkillCommandInput, value)),
        slash_input=_make_skill_slash_input,
    )


def _make_skill_slash_input(
    _command: SlashCommandSpec,
    args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    """Project the compact review grammar into one strict Action input."""
    agent_id = _required_context(context.agent_id, "Agent")
    session_id = _required_context(context.session_id, "Session")
    request = args.strip()
    normalized = request.lower()
    operation = "create"
    focus = request
    revision_notes: str | None = None
    if normalized == "approve":
        operation = "approve"
        focus = ""
    elif normalized == "cancel":
        operation = "cancel"
        focus = ""
    elif normalized == "revise":
        raise SlashCommandError(
            "skill_draft_revision_required",
            "Usage: /make-skill revise <what should change>",
        )
    elif normalized.startswith("revise "):
        operation = "revise"
        focus = ""
        revision_notes = request[7:].strip()
    return {
        "operation": operation,
        "agentId": agent_id,
        "userId": context.user_id,
        "sessionId": session_id,
        "focus": focus,
        "revisionNotes": revision_notes,
    }


def _dispatch(service: MakeSkillService | Any, value: MakeSkillCommandInput) -> dict[str, object]:
    """Call the selected lifecycle method and retain a stable operation projection."""
    common = {
        "agent_id": value.agent_id,
        "user_id": value.user_id,
        "session_id": value.session_id,
    }
    try:
        if value.operation == "create":
            result = service.create(**common, focus=value.focus)
            return {"operation": "draft", "draft": _payload(result)}
        if value.operation == "revise":
            result = service.revise(**common, revision_notes=value.revision_notes or "")
            return {"operation": "draft", "draft": _payload(result)}
        if value.operation == "approve":
            result = service.approve(**common)
            return {"operation": "published", "skill": _payload(result)}
        result = service.cancel(**common)
        return {"operation": "cancelled", **_payload(result)}
    except MakeSkillError as exc:
        raise ActionFailure(ActionError(exc.code, exc.message, details=exc.details)) from exc


def _payload(value: Any) -> dict[str, object]:
    if isinstance(value, dict):
        return value
    method = getattr(value, "to_payload", None)
    if callable(method):
        return method()
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        return dump(mode="json", by_alias=True)
    raise TypeError("Skill authoring service returned an unsupported result.")


def _required_context(value: str | None, field: str) -> str:
    if value:
        return value
    raise SlashCommandError("command_context_required", f"The /make-skill command requires {field} context.")


__all__ = ["register_make_skill_actions"]
