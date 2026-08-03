"""Skill manifest, registry, lifecycle, and rollback tests."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from openppx.extensions import ExtensionError, ExtensionSourceRef, SkillManager


def _skill(
    root: Path,
    *,
    name: str = "demo",
    version: str = "1.0.0",
    risk: str = "low",
    executable: str | None = None,
) -> Path:
    dependencies = ""
    if executable:
        dependencies = f"\n    dependencies:\n      executables: [{executable}]"
    root.mkdir(parents=True, exist_ok=True)
    (root / "SKILL.md").write_text(
        (
            "---\n"
            f"name: {name}\n"
            "description: A deterministic fixture skill.\n"
            "metadata:\n"
            "  openppx:\n"
            f"    version: {version}\n"
            f"    risk: {risk}"
            f"{dependencies}\n"
            "---\n\n"
            "# Fixture\n"
        ),
        encoding="utf-8",
    )
    return root


def test_stage_preview_install_enable_disable_and_remove(tmp_path: Path) -> None:
    source = _skill(tmp_path / "source")
    manager = SkillManager(tmp_path / "node", executable_resolver=lambda _name: "/bin/tool")

    staged = manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
    preview = manager.preview(staged)
    installed = manager.install(staged, expected_revision=None)

    assert preview.skill_id == "demo"
    assert preview.version == "1.0.0"
    assert preview.risk == "low"
    assert installed.record.metadata.name == "demo"
    assert installed.status == "disabled"
    assert installed.record.spec.enabled_agent_ids == []
    assert not str(installed.content_root).startswith(str(source))
    shutil.rmtree(source)
    assert installed.content_root.joinpath("SKILL.md").is_file()

    enabled = manager.enable("demo", "writer", expected_revision=installed.revision)
    assert enabled.record.spec.enabled_agent_ids == ["writer"]
    assert enabled.status == "enabled"
    snapshot = manager.snapshot_for_agent("writer")
    assert snapshot.names == ("demo",)
    assert "A deterministic fixture skill" in snapshot.build_summary()
    assert "# Fixture" in snapshot.read_skill("demo")

    with pytest.raises(ExtensionError) as in_use:
        manager.remove("demo", expected_revision=enabled.revision)
    assert in_use.value.code == "extension_in_use"

    disabled = manager.disable("demo", "writer", expected_revision=enabled.revision)
    manager.remove("demo", expected_revision=disabled.revision)
    assert manager.list() == ()


def test_high_risk_and_missing_dependency_block_enable(tmp_path: Path) -> None:
    high = _skill(tmp_path / "high", name="high-risk", risk="high")
    missing = _skill(tmp_path / "missing", name="missing-dep", executable="not-installed")
    manager = SkillManager(tmp_path / "node", executable_resolver=lambda _name: None)
    high_record = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(high))),
        expected_revision=None,
    )
    missing_record = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(missing))),
        expected_revision=None,
    )

    with pytest.raises(ExtensionError) as confirmation:
        manager.enable("high-risk", "writer", expected_revision=high_record.revision)
    assert confirmation.value.code == "confirmation_required"
    enabled = manager.enable(
        "high-risk",
        "writer",
        expected_revision=high_record.revision,
        confirmed=True,
    )
    assert enabled.record.spec.enabled_agent_ids == ["writer"]

    with pytest.raises(ExtensionError) as dependency:
        manager.enable("missing-dep", "writer", expected_revision=missing_record.revision)
    assert dependency.value.code == "dependency_missing"


def test_revision_conflict_and_failed_update_preserve_active_record(tmp_path: Path) -> None:
    first = _skill(tmp_path / "first", version="1.0.0")
    second = _skill(tmp_path / "second", version="2.0.0")
    manager = SkillManager(tmp_path / "node")
    installed = manager.install(
        manager.stage(ExtensionSourceRef(type="local_directory", locator=str(first))),
        expected_revision=None,
    )

    with pytest.raises(ExtensionError) as conflict:
        manager.update(
            manager.stage(ExtensionSourceRef(type="local_directory", locator=str(second))),
            expected_revision="sha256:" + "0" * 64,
        )
    assert conflict.value.code == "revision_conflict"
    current = manager.get("demo")
    assert current.revision == installed.revision
    assert current.record.spec.version == "1.0.0"

    invalid = _skill(tmp_path / "invalid", name="different", version="3.0.0")
    with pytest.raises(ExtensionError) as mismatch:
        manager.install(
            manager.stage(ExtensionSourceRef(type="local_directory", locator=str(invalid))),
            expected_revision=current.revision,
            extension_id="demo",
        )
    assert mismatch.value.code == "invalid_manifest"
    assert manager.get("demo").record.spec.version == "1.0.0"


def test_builtin_identity_is_explicit_and_cannot_be_shadowed(tmp_path: Path) -> None:
    builtin = _skill(tmp_path / "builtin")
    local = _skill(tmp_path / "local")
    manager = SkillManager(tmp_path / "node", builtin_skills={"demo": builtin})

    items = manager.list()
    assert len(items) == 1
    assert items[0].status == "builtin"
    assert items[0].record.spec.source.type == "builtin"
    assert manager.snapshot_for_agent("any-agent").names == ("demo",)

    with pytest.raises(ExtensionError) as conflict:
        manager.install(
            manager.stage(ExtensionSourceRef(type="local_directory", locator=str(local))),
            expected_revision=None,
        )
    assert conflict.value.code == "extension_conflict"
