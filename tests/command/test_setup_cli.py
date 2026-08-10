from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from openppx.command.parser import build_parser
from openppx.command.setup import _node_apply_input, run_setup
from openppx.config import ConfigLoadError, FilesystemConfigRepository
from openppx.modeling import ModelProfileRepository


def test_setup_defaults_to_node_only_without_model_credential(tmp_path, capsys) -> None:
    args = build_parser().parse_args(
        [
            "setup",
            "--node-root",
            str(tmp_path),
            "--node-name",
            "Team Node",
            "--listen-host",
            "127.0.0.1",
            "--listen-port",
            "18765",
            "--authentication",
            "required",
        ]
    )

    with patch(
        "openppx.command.setup.getpass.getpass",
        side_effect=AssertionError("Node-only setup must not request an LLM credential."),
    ):
        assert run_setup(args) == 0

    repository = FilesystemConfigRepository(tmp_path)
    node = repository.read_node().document
    assert node.spec.display_name == "Team Node"
    assert node.spec.enabled_agents == []
    assert node.spec.client_api.listen_host == "127.0.0.1"
    assert node.spec.client_api.port == 18765
    assert node.spec.client_api.authentication == "required"
    with pytest.raises(ConfigLoadError):
        repository.read_agent("main")
    with pytest.raises(ConfigLoadError):
        ModelProfileRepository(tmp_path).read_profile("primary")

    output = capsys.readouterr().out
    assert "Node setup: complete" in output
    assert "Model configuration: deferred" in output
    assert "Model:" not in output


def test_setup_with_agent_is_an_explicit_opt_in() -> None:
    parser = build_parser()

    default_args = parser.parse_args(["setup"])
    assert default_args.with_agent is False
    assert default_args.authentication == "required"
    assert parser.parse_args(["setup", "--with-agent"]).with_agent is True


def test_node_only_setup_preserves_existing_agents_and_node_settings() -> None:
    args = build_parser().parse_args(
        [
            "setup",
            "--node-name",
            "Renamed Node",
            "--listen-port",
            "19000",
        ]
    )
    status = {
        "revisions": {"node": "sha256:node"},
        "current": {
            "node": {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "NodeConfig",
                "metadata": {"name": "existing-node"},
                "spec": {
                    "displayName": "Existing Node",
                    "enabledAgents": ["main", "research"],
                    "clientApi": {
                        "listenHost": "127.0.0.1",
                        "port": 18765,
                        "authentication": "required",
                    },
                    "operations": {"cronEnabled": False},
                },
            }
        },
    }

    payload = _node_apply_input(args, status)

    assert payload["expectedRevision"] == "sha256:node"
    candidate = payload["candidate"]
    assert candidate["metadata"]["name"] == "existing-node"
    assert candidate["spec"]["displayName"] == "Renamed Node"
    assert candidate["spec"]["enabledAgents"] == ["main", "research"]
    assert candidate["spec"]["operations"] == {"cronEnabled": False}
    assert candidate["spec"]["clientApi"] == {
        "listenHost": "127.0.0.1",
        "port": 19000,
        "authentication": "required",
    }


def test_setup_with_agent_keeps_the_complete_bootstrap_flow(tmp_path, capsys) -> None:
    args = build_parser().parse_args(
        [
            "setup",
            "--node-root",
            str(tmp_path),
            "--with-agent",
            "--api-key",
            "test-api-key",
            "--no-hello",
        ]
    )
    before = {
        "state": "needs_configuration",
        "revisions": {"node": None, "agent": None, "profile": None},
        "recommendedWorkspace": str(tmp_path / "workspace"),
        "providers": [{
            "id": "google",
            "displayName": "Google Gemini",
            "credentialMode": "api_key",
            "defaultModel": "gemini-test",
        }],
    }
    after = {**before, "state": "configured"}
    status_calls = 0

    def invoke(action_id: str, raw_input: dict[str, object]) -> dict[str, object]:
        nonlocal status_calls
        if action_id == "setup.status":
            status_calls += 1
            return {"ok": True, "result": before if status_calls == 1 else after}
        if action_id == "setup.apply":
            request = raw_input["request"]
            assert request["secret"]["value"] == "test-api-key"
            return {
                "ok": True,
                "result": {
                    "state": "configured",
                    "revisions": {"node": "node", "agent": "agent", "profile": "profile"},
                    "secretState": "available",
                    "restartRequired": False,
                },
            }
        raise AssertionError(f"Unexpected Action: {action_id}")

    composition = SimpleNamespace(
        runtime_supervisor=SimpleNamespace(close=Mock()),
        control_plane=SimpleNamespace(close=Mock()),
    )
    with (
        patch("openppx.command.setup.build_node_composition", return_value=composition),
        patch("openppx.command.setup._local_invoker", return_value=invoke),
    ):
        assert run_setup(args) == 0

    assert status_calls == 2
    assert "Model: google/gemini-test" in capsys.readouterr().out
