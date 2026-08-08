"""Tests for strict filesystem configuration reads and diagnostics."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from openppx.config import ConfigLoadError, FilesystemConfigRepository


def node_document(*, name: str = "local-node") -> dict[str, object]:
    """Return one valid NodeConfig JSON object."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "NodeConfig",
        "metadata": {"name": name},
        "spec": {
            "displayName": "Local Node",
            "enabledAgents": [],
            "clientApi": {
                "listenHost": "127.0.0.1",
                "port": 18765,
                "authentication": "required",
            },
        },
    }


def agent_document(name: str) -> dict[str, object]:
    """Return one valid AgentConfig JSON object."""
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "AgentConfig",
        "metadata": {"name": name},
        "spec": {
            "displayName": name,
            "workspace": f"workspace/{name}",
            "ownerPrincipalId": "local:owner",
            "privilegeLevel": "low",
            "controls": {},
        },
    }


def write_json(path: Path, payload: object, *, indent: int | None = None) -> None:
    """Write one JSON fixture below a temporary Node root."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=indent, ensure_ascii=False), encoding="utf-8")


def test_read_node_returns_typed_versioned_resource(tmp_path: Path) -> None:
    write_json(tmp_path / "node.json", node_document())
    repository = FilesystemConfigRepository(tmp_path)

    resource = repository.read_node()

    assert resource.resource_id == "node/local-node"
    assert resource.document.metadata.name == "local-node"
    assert resource.revision.startswith("sha256:")
    assert resource.source.kind == "node_file"
    assert resource.source.path == tmp_path / "node.json"


def test_read_missing_node_raises_structured_error(tmp_path: Path) -> None:
    repository = FilesystemConfigRepository(tmp_path)

    with pytest.raises(ConfigLoadError) as raised:
        repository.read_node()

    assert raised.value.kind == "not_found"
    assert raised.value.issues[0].code == "not_found"


def test_invalid_json_reports_line_and_column(tmp_path: Path) -> None:
    (tmp_path / "node.json").write_text('{"kind":\n', encoding="utf-8")
    repository = FilesystemConfigRepository(tmp_path)

    with pytest.raises(ConfigLoadError) as raised:
        repository.read_node()

    assert raised.value.kind == "invalid_json"
    assert raised.value.issues[0].line == 2
    assert raised.value.issues[0].column is not None


def test_invalid_utf8_is_not_silently_defaulted(tmp_path: Path) -> None:
    (tmp_path / "node.json").write_bytes(b"\xff\xfe")

    with pytest.raises(ConfigLoadError) as raised:
        FilesystemConfigRepository(tmp_path).read_node()

    assert raised.value.kind == "invalid_utf8"


def test_invalid_root_is_diagnostic(tmp_path: Path) -> None:
    write_json(tmp_path / "node.json", ["not", "an", "object"])

    diagnostics = FilesystemConfigRepository(tmp_path).diagnose_node()

    assert diagnostics.ok is False
    assert diagnostics.error_kind == "invalid_root"
    assert diagnostics.issues[0].path == ()


def test_schema_diagnostic_does_not_echo_input_value(tmp_path: Path) -> None:
    document = node_document()
    document["spec"]["secretValue"] = "sk-do-not-leak"  # type: ignore[index]
    write_json(tmp_path / "node.json", document)

    diagnostics = FilesystemConfigRepository(tmp_path).diagnose_node()
    rendered = str(diagnostics)

    assert diagnostics.error_kind == "invalid_schema"
    assert diagnostics.issues[0].code == "unknown_field"
    assert diagnostics.issues[0].path == ("spec", "secretValue")
    assert "sk-do-not-leak" not in rendered


def test_agent_metadata_name_must_match_path(tmp_path: Path) -> None:
    write_json(tmp_path / "agents" / "low-main" / "agent.json", agent_document("other-agent"))

    with pytest.raises(ConfigLoadError) as raised:
        FilesystemConfigRepository(tmp_path).read_agent("low-main")

    assert raised.value.kind == "name_mismatch"


@pytest.mark.parametrize("agent_id", ["../escape", "/absolute/escape"])
def test_agent_id_cannot_escape_node_root(tmp_path: Path, agent_id: str) -> None:
    with pytest.raises(ConfigLoadError) as raised:
        FilesystemConfigRepository(tmp_path).read_agent(agent_id)

    assert raised.value.kind == "path_outside_root"


def test_agent_symlink_cannot_escape_node_root(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside-agent"
    write_json(outside / "agent.json", agent_document("escape"))
    agents_dir = tmp_path / "agents"
    agents_dir.mkdir(parents=True)
    try:
        (agents_dir / "escape").symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    with pytest.raises(ConfigLoadError) as raised:
        FilesystemConfigRepository(tmp_path).read_agent("escape")

    assert raised.value.kind == "path_outside_root"


def test_list_agent_ids_is_validated_and_deterministic(tmp_path: Path) -> None:
    write_json(tmp_path / "agents" / "zeta" / "agent.json", agent_document("zeta"))
    write_json(tmp_path / "agents" / "alpha" / "agent.json", agent_document("alpha"))

    assert FilesystemConfigRepository(tmp_path).list_agent_ids() == ("alpha", "zeta")


def test_list_agent_ids_does_not_hide_invalid_resources(tmp_path: Path) -> None:
    write_json(tmp_path / "agents" / "valid" / "agent.json", agent_document("valid"))
    write_json(tmp_path / "agents" / "broken" / "agent.json", {"kind": "AgentConfig"})

    with pytest.raises(ConfigLoadError) as raised:
        FilesystemConfigRepository(tmp_path).list_agent_ids()

    assert raised.value.kind == "invalid_schema"


def test_repository_read_does_not_mutate_environment(tmp_path: Path) -> None:
    write_json(tmp_path / "node.json", node_document())
    before = dict(os.environ)

    FilesystemConfigRepository(tmp_path).read_node()

    assert dict(os.environ) == before


def test_revision_is_independent_of_json_formatting_and_key_order(tmp_path: Path) -> None:
    first = node_document()
    write_json(tmp_path / "node.json", first, indent=2)
    repository = FilesystemConfigRepository(tmp_path)
    first_revision = repository.read_node().revision

    reordered = {
        "spec": first["spec"],
        "metadata": first["metadata"],
        "kind": first["kind"],
        "apiVersion": first["apiVersion"],
    }
    write_json(tmp_path / "node.json", reordered)

    assert repository.read_node().revision == first_revision


def test_revision_changes_when_resource_changes(tmp_path: Path) -> None:
    first = node_document()
    write_json(tmp_path / "node.json", first)
    repository = FilesystemConfigRepository(tmp_path)
    first_revision = repository.read_node().revision

    first["spec"]["displayName"] = "Another Node"  # type: ignore[index]
    write_json(tmp_path / "node.json", first)

    assert repository.read_node().revision != first_revision
