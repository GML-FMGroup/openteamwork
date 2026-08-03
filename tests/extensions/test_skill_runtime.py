"""Immutable Skill snapshot integration tests."""

from __future__ import annotations

from pathlib import Path

from openppx.extensions import ExtensionSourceRef, SkillManager


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
