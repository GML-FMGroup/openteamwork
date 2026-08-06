"""Tests for standards-first local extension authoring."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from openppx.extensions.authoring import (
    ExtensionAuthoringError,
    package_extension,
    run_adk_evaluation,
    scaffold_extension,
    validate_adk_evalset,
    validate_extension_source,
)


@pytest.mark.parametrize("kind", ["skill", "plugin", "app"])
def test_scaffolded_extension_passes_production_validation(tmp_path: Path, kind: str) -> None:
    result = scaffold_extension(
        kind,  # type: ignore[arg-type]
        f"demo-{kind}",
        tmp_path,
        description=f"A bounded {kind} authoring example.",
    )

    validation = validate_extension_source(kind, Path(result["path"]))  # type: ignore[arg-type]

    assert validation["valid"] is True
    assert validation["name"] == f"demo-{kind}"


def test_skill_package_is_deterministic_and_contains_source_root(tmp_path: Path) -> None:
    source = Path(
        scaffold_extension(
            "skill",
            "demo-skill",
            tmp_path / "sources",
            description="A deterministic skill package.",
        )["path"]
    )
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"

    package_extension("skill", source, first)
    package_extension("skill", source, second)

    assert first.read_bytes() == second.read_bytes()
    import zipfile

    with zipfile.ZipFile(first) as archive:
        assert archive.namelist() == ["SKILL.md"]


def test_package_rejects_output_inside_source(tmp_path: Path) -> None:
    source = Path(
        scaffold_extension(
            "plugin",
            "demo-plugin",
            tmp_path,
            description="A safe plugin package.",
        )["path"]
    )

    with pytest.raises(ExtensionAuthoringError, match="outside"):
        package_extension("plugin", source, source / "plugin.zip")


def test_evalset_validation_and_official_evaluator_delegation(tmp_path: Path, monkeypatch) -> None:
    evalset = tmp_path / "skill.evalset.json"
    evalset.write_text(
        json.dumps(
            {
                "eval_set_id": "skill_eval",
                "name": "Skill eval",
                "description": "One ADK-native extension eval.",
                "eval_cases": [
                    {
                        "eval_id": "case-1",
                        "conversation": [
                            {
                                "invocation_id": "invocation-1",
                                "user_content": {"role": "user", "parts": [{"text": "Say ready"}]},
                                "final_response": {"role": "model", "parts": [{"text": "ready"}]},
                                "intermediate_data": {
                                    "tool_uses": [],
                                    "tool_responses": [],
                                    "intermediate_responses": [],
                                },
                            }
                        ],
                        "session_input": {"app_name": "openppx", "user_id": "eval-user", "state": {}},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    calls: list[dict[str, object]] = []

    async def fake_evaluate(**kwargs: object) -> None:
        calls.append(dict(kwargs))

    monkeypatch.setattr("openppx.extensions.authoring.AgentEvaluator.evaluate", fake_evaluate)

    assert validate_adk_evalset(evalset) == {
        "valid": True,
        "evalSetId": "skill_eval",
        "cases": 1,
        "turns": 1,
    }
    result = run_adk_evaluation("tests/eval/openppx", evalset, num_runs=2)

    assert result["evaluated"] is True
    assert calls[0]["agent_module"] == "tests/eval/openppx"
    assert calls[0]["num_runs"] == 2


def test_scaffold_rejects_nonportable_identity(tmp_path: Path) -> None:
    with pytest.raises(ExtensionAuthoringError, match="lowercase"):
        scaffold_extension("skill", "Not Portable", tmp_path, description="Invalid identity")
