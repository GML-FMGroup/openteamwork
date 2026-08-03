"""Regression tests that remove silent corruption fallback from legacy reads."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from openppx.config import ConfigLoadError
from openppx.core.config import bootstrap_env_from_config, load_config, load_runtime_config


def test_legacy_load_config_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"agent":', encoding="utf-8")

    with pytest.raises(ConfigLoadError) as raised:
        load_config(path)

    assert raised.value.kind == "invalid_json"


def test_legacy_load_config_rejects_non_object_root(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    path.write_text("[]", encoding="utf-8")

    with pytest.raises(ConfigLoadError) as raised:
        load_config(path)

    assert raised.value.kind == "invalid_root"


def test_legacy_runtime_config_rejects_invalid_json(tmp_path: Path) -> None:
    path = tmp_path / "runtime.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ConfigLoadError) as raised:
        load_runtime_config(path)

    assert raised.value.kind == "invalid_json"


def test_bootstrap_invalid_schema_does_not_activate_config_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "agent" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"agent":{"role":"assistant"}}', encoding="utf-8")
    context_keys = (
        "OPENPPX_CONFIG_FILE",
        "OPENPPX_RUNTIME_CONFIG_FILE",
        "OPENPPX_DATA_DIR",
        "OPENPPX_AGENT_HOME",
    )
    for key in context_keys:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ConfigLoadError) as raised:
        bootstrap_env_from_config(path)

    assert raised.value.kind == "invalid_schema"
    assert all(key not in os.environ for key in context_keys)


def test_bootstrap_invalid_runtime_does_not_activate_config_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path = tmp_path / "agent" / "config.json"
    path.parent.mkdir(parents=True)
    path.write_text('{"agent":{"privilegeLevel":"low"}}', encoding="utf-8")
    path.with_name("runtime.json").write_text("{", encoding="utf-8")
    context_keys = (
        "OPENPPX_CONFIG_FILE",
        "OPENPPX_RUNTIME_CONFIG_FILE",
        "OPENPPX_DATA_DIR",
        "OPENPPX_AGENT_HOME",
    )
    for key in context_keys:
        monkeypatch.delenv(key, raising=False)

    with pytest.raises(ConfigLoadError) as raised:
        bootstrap_env_from_config(path)

    assert raised.value.kind == "invalid_json"
    assert all(key not in os.environ for key in context_keys)
