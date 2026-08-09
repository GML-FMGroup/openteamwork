"""Action and slash-command contract tests for conversation Skill authoring."""

from __future__ import annotations

from openppx.actions import ActionRegistry, SlashInvocationContext
from openppx.control_plane.make_skill_actions import register_make_skill_actions


class _Service:
    def create(self, **kwargs):
        return {"operation": "draft", "kwargs": kwargs}

    def revise(self, **kwargs):
        return {"operation": "revise", "kwargs": kwargs}

    def approve(self, **kwargs):
        return {"operation": "published", "kwargs": kwargs}

    def cancel(self, **kwargs):
        return {"operation": "cancelled", "kwargs": kwargs}


def _input(raw: str) -> tuple[object, dict[str, object]]:
    registry = ActionRegistry()
    register_make_skill_actions(registry, _Service())
    resolved = registry.resolve_slash(raw)
    adapter = resolved.registered.slash_input
    assert adapter is not None
    value = adapter(
        resolved.command,
        resolved.args,
        SlashInvocationContext(user_id="owner", agent_id="main", session_id="session-1"),
    )
    return resolved, value


def test_make_skill_command_projects_focus_and_review_operations() -> None:
    resolved, create = _input("/make-skill focus on the weekly report")
    assert resolved.command.lifecycle == "side_channel"
    assert create == {
        "operation": "create",
        "agentId": "main",
        "userId": "owner",
        "sessionId": "session-1",
        "focus": "focus on the weekly report",
        "revisionNotes": None,
    }

    assert _input("/make-skill approve")[1]["operation"] == "approve"
    assert _input("/make-skill cancel")[1]["operation"] == "cancel"
    revise = _input("/make-skill revise make the output clearer")[1]
    assert revise["operation"] == "revise"
    assert revise["revisionNotes"] == "make the output clearer"


def test_make_skill_action_requires_session_read_and_extension_write() -> None:
    registry = ActionRegistry()
    register_make_skill_actions(registry, _Service())
    action = registry.resolve_slash("/make-skill").registered.spec

    assert action.action_id == "skill.draft.command"
    assert action.required_capabilities == frozenset({"session.read", "extension.write"})
    assert action.permission == "extension.write"
    assert action.operation == "mutation"
