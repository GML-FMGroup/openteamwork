"""Immutable Skill snapshot integration tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from openppx.extensions import ExtensionError, ExtensionSourceRef, SkillManager


def _skill(root: Path, name: str, description: str) -> Path:
    root.mkdir(parents=True)
    (root / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n\n# {name}\n",
        encoding="utf-8",
    )
    return root


def test_agent_skill_snapshot_is_immutable_across_lifecycle_changes(tmp_path: Path) -> None:
    manager = SkillManager(tmp_path / "node")
    source = _skill(tmp_path / "source", "demo", "First version.")
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=None,
    )
    enabled = manager.enable("demo", "writer", expected_revision=installed.revision)
    first = manager.snapshot_for_agent("writer")

    (source / "SKILL.md").write_text(
        "---\nname: demo\ndescription: Second version.\n---\n\n# changed\n",
        encoding="utf-8",
    )
    updated = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source))),
        expected_revision=enabled.revision,
    )
    manager.enable("demo", "writer", expected_revision=updated.revision)
    second = manager.snapshot_for_agent("writer")

    assert first.revision != second.revision
    assert "First version" in first.build_summary()
    assert "# demo" in first.read_skill("demo")
    assert "Second version" in second.build_summary()
    assert "# changed" in second.read_skill("demo")


def test_workspace_skill_snapshot_is_scoped_to_one_agent_workspace(tmp_path: Path) -> None:
    manager = SkillManager(tmp_path / "node")
    first_workspace = tmp_path / "users" / "first" / "workspace"
    second_workspace = tmp_path / "users" / "second" / "workspace"
    _skill(first_workspace / "skills" / "demo", "demo", "First user's Skill.")
    _skill(second_workspace / "skills" / "other", "other", "Second user's Skill.")

    first = manager.snapshot_for_workspace(first_workspace)
    second = manager.snapshot_for_workspace(second_workspace)

    assert first.names == ("demo",)
    assert second.names == ("other",)
    assert first.skills[0].content_root == (first_workspace / "skills" / "demo").resolve()


def test_invalid_workspace_override_fails_closed(tmp_path: Path) -> None:
    manager = SkillManager(tmp_path / "node")
    workspace = tmp_path / "workspace"
    _skill(workspace / "skills" / "demo", "different-name", "Broken override.")

    with pytest.raises(ExtensionError, match="folder name does not match"):
        manager.snapshot_for_workspace(workspace)
