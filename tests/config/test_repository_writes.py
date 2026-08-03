"""Tests for revision-safe atomic Config Repository writes."""

from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

import pytest
from filelock import FileLock

from openppx.config import (
    AgentConfig,
    ConfigLoadError,
    ConfigRevisionConflict,
    ConfigWriteError,
    FilesystemConfigRepository,
    NodeConfig,
)


def node_document(*, display_name: str = "Local Node") -> dict[str, object]:
    """Return one valid Node resource payload."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "NodeConfig",
        "metadata": {"name": "local-node"},
        "spec": {
            "displayName": display_name,
            "enabledAgents": ["low-main"],
            "clientApi": {
                "listenHost": "127.0.0.1",
                "port": 18765,
                "authentication": "required",
            },
        },
    }


def agent_document(*, name: str = "low-main") -> dict[str, object]:
    """Return one valid Agent resource payload."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "AgentConfig",
        "metadata": {"name": name},
        "spec": {
            "displayName": "Low Main",
            "workspace": "workspace/low-main",
            "ownerPrincipalId": "local:owner",
            "privilegeLevel": "low",
            "permissionOverrides": {},
        },
    }


def test_write_node_create_is_atomic_private_and_readable(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path)
    document = NodeConfig.model_validate(node_document())

    written = repository.write_node(document, expected_revision=None)

    path = tmp_path / "node.json"
    assert written.resource_id == "node/local-node"
    assert repository.read_node().revision == written.revision
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text(encoding="utf-8"))["apiVersion"] == "openppx.io/v1alpha1"
    assert not tuple(tmp_path.glob(".node.json.*.tmp"))


def test_create_existing_resource_is_revision_conflict(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path)
    document = NodeConfig.model_validate(node_document())
    current = repository.write_node(document, expected_revision=None)

    with pytest.raises(ConfigRevisionConflict) as raised:
        repository.write_node(document, expected_revision=None)

    assert raised.value.kind == "revision_conflict"
    assert raised.value.expected_revision is None
    assert raised.value.actual_revision == current.revision


def test_write_node_updates_only_matching_revision(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path)
    current = repository.write_node(NodeConfig.model_validate(node_document()), expected_revision=None)
    updated_document = NodeConfig.model_validate(node_document(display_name="Updated Node"))

    updated = repository.write_node(updated_document, expected_revision=current.revision)

    assert updated.revision != current.revision
    assert repository.read_node().document.spec.display_name == "Updated Node"


def test_write_missing_resource_with_revision_is_conflict(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path)

    with pytest.raises(ConfigRevisionConflict) as raised:
        repository.write_node(
            NodeConfig.model_validate(node_document()),
            expected_revision="sha256:" + "0" * 64,
        )

    assert raised.value.actual_revision is None
    assert not (tmp_path / "node.json").exists()


def test_stale_revision_does_not_change_existing_bytes(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path)
    current = repository.write_node(NodeConfig.model_validate(node_document()), expected_revision=None)
    path = tmp_path / "node.json"
    before = path.read_bytes()

    with pytest.raises(ConfigRevisionConflict):
        repository.write_node(
            NodeConfig.model_validate(node_document(display_name="Stale")),
            expected_revision="sha256:" + "f" * 64,
        )

    assert path.read_bytes() == before
    assert repository.read_node().revision == current.revision


def test_atomic_replace_failure_preserves_old_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FilesystemConfigRepository(tmp_path)
    current = repository.write_node(NodeConfig.model_validate(node_document()), expected_revision=None)
    path = tmp_path / "node.json"
    before = path.read_bytes()

    def fail_replace(source: str | os.PathLike[str], target: str | os.PathLike[str]) -> None:
        del source, target
        raise OSError("simulated replace failure")

    monkeypatch.setattr("openppx.config.atomic.os.replace", fail_replace)

    with pytest.raises(ConfigWriteError) as raised:
        repository.write_node(
            NodeConfig.model_validate(node_document(display_name="Never Written")),
            expected_revision=current.revision,
        )

    assert raised.value.kind == "write_failed"
    assert path.read_bytes() == before
    assert "simulated replace failure" not in str(raised.value)
    assert not tuple(tmp_path.glob(".node.json.*.tmp"))


def test_file_fsync_failure_preserves_old_file_and_cleans_temp(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repository = FilesystemConfigRepository(tmp_path)
    current = repository.write_node(NodeConfig.model_validate(node_document()), expected_revision=None)
    path = tmp_path / "node.json"
    before = path.read_bytes()

    def fail_fsync(descriptor: int) -> None:
        del descriptor
        raise OSError("simulated fsync failure")

    monkeypatch.setattr("openppx.config.atomic.os.fsync", fail_fsync)

    with pytest.raises(ConfigWriteError):
        repository.write_node(
            NodeConfig.model_validate(node_document(display_name="Never Synced")),
            expected_revision=current.revision,
        )

    assert path.read_bytes() == before
    assert not tuple(tmp_path.glob(".node.json.*.tmp"))


def test_two_writers_with_same_revision_have_one_winner(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path, lock_timeout=2.0)
    current = repository.write_node(NodeConfig.model_validate(node_document()), expected_revision=None)
    candidates = (
        NodeConfig.model_validate(node_document(display_name="Writer A")),
        NodeConfig.model_validate(node_document(display_name="Writer B")),
    )

    def write(candidate: NodeConfig) -> str:
        try:
            return repository.write_node(candidate, expected_revision=current.revision).document.spec.display_name
        except ConfigRevisionConflict:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = tuple(executor.map(write, candidates))

    assert results.count("conflict") == 1
    assert repository.read_node().document.spec.display_name in {"Writer A", "Writer B"}


def test_lock_timeout_is_distinct_from_revision_conflict(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path, lock_timeout=0.01)
    current = repository.write_node(NodeConfig.model_validate(node_document()), expected_revision=None)
    lock_path = tmp_path / "node.json.lock"

    with FileLock(lock_path, timeout=1.0, mode=0o600):
        with pytest.raises(ConfigWriteError) as raised:
            repository.write_node(
                NodeConfig.model_validate(node_document(display_name="Blocked")),
                expected_revision=current.revision,
            )

    assert raised.value.kind == "lock_timeout"


def test_write_agent_requires_path_identity_before_creating_file(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path)
    wrong = AgentConfig.model_validate(agent_document(name="other-agent"))

    with pytest.raises(ConfigLoadError) as raised:
        repository.write_agent("low-main", wrong, expected_revision=None)

    assert raised.value.kind == "name_mismatch"
    assert not (tmp_path / "agents" / "low-main" / "agent.json").exists()


def test_write_agent_roundtrip_and_update(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path)
    document = AgentConfig.model_validate(agent_document())
    created = repository.write_agent("low-main", document, expected_revision=None)
    payload = deepcopy(agent_document())
    payload["spec"]["displayName"] = "Updated Agent"  # type: ignore[index]

    updated = repository.write_agent(
        "low-main",
        AgentConfig.model_validate(payload),
        expected_revision=created.revision,
    )

    assert updated.document.spec.display_name == "Updated Agent"
    assert updated.revision != created.revision
