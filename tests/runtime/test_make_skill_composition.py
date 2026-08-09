"""Full Node composition contract for conversation Skill authoring."""

from __future__ import annotations

from openppx.actions import ActionContext
from openppx.config import InMemorySecretStore
from openppx.runtime.node_host import build_node_composition


def test_node_composition_registers_make_skill_after_runtime_and_extensions(tmp_path) -> None:
    composition = build_node_composition(tmp_path / "node", secret_store=InMemorySecretStore())
    context = ActionContext(
        request_id="req-make-skill-catalog",
        correlation_id="corr-make-skill-catalog",
        actor_id="test",
        capabilities=frozenset({"system.read", "session.read", "extension.write"}),
        permissions=frozenset({"system.read", "session.read", "extension.write"}),
    )

    catalog = composition.control_plane.catalog(context, namespace="skill", projection="slash")

    assert catalog.ok is True
    assert catalog.data is not None
    items = catalog.data["items"]
    assert items[0]["actionId"] == "skill.draft.command"
    assert items[0]["slashCommands"][0]["command"] == "/make-skill"
    assert composition.control_plane.make_skill_service is not None
