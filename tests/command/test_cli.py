from __future__ import annotations

from argparse import Namespace
from unittest.mock import patch

import pytest

from openppx import cli
from openppx.command.dispatch import dispatch
from openppx.command.parser import build_parser


def test_help_exposes_only_converged_product_groups(capsys) -> None:
    with pytest.raises(SystemExit) as raised:
        cli.main(["--help"])
    assert raised.value.code == 0
    output = capsys.readouterr().out
    for command in ("setup", "node", "action", "command", "goal", "config", "model", "extension", "operations"):
        assert command in output
    assert "client-api" not in output
    assert "gateway" not in output


def test_main_dispatches_one_parsed_command() -> None:
    with patch("openppx.cli.dispatch", return_value=0) as mocked:
        with pytest.raises(SystemExit) as raised:
            cli.main(["operations", "status"])
    assert raised.value.code == 0
    assert mocked.call_args.args[0].operations_command == "status"


def test_action_invoke_requires_json_object(capsys) -> None:
    args = build_parser().parse_args(["action", "invoke", "system.status", "--input-json", "[]"])
    assert dispatch(args) == 2
    assert "must contain one JSON object" in capsys.readouterr().out


def test_config_read_routes_to_node_action() -> None:
    args = build_parser().parse_args(["config", "read"])
    with patch("openppx.command.dispatch.invoke_action", return_value=0) as invoke:
        assert dispatch(args) == 0
    invoke.assert_called_once_with(args, "config.node.read", {})


def test_config_agent_read_routes_to_agent_action() -> None:
    args = build_parser().parse_args(["config", "read", "--agent", "main"])
    with patch("openppx.command.dispatch.invoke_action", return_value=0) as invoke:
        assert dispatch(args) == 0
    invoke.assert_called_once_with(args, "config.agent.read", {"agentId": "main"})


def test_cron_disable_uses_revisionless_node_action() -> None:
    args = build_parser().parse_args(["operations", "cron", "disable", "job-1", "--yes"])
    with patch("openppx.command.dispatch.invoke_action", return_value=0) as invoke:
        assert dispatch(args) == 0
    invoke.assert_called_once_with(
        args,
        "operations.cron.enable",
        {"jobId": "job-1", "enabled": False},
        confirmed=True,
    )


def test_goal_create_routes_to_formal_goal_action() -> None:
    args = build_parser().parse_args([
        "goal",
        "create",
        "Ship the release",
        "--agent",
        "main",
        "--session",
        "session-1",
        "--criterion",
        "All checks pass",
        "--constraint",
        "Do not publish",
    ])
    with patch("openppx.command.dispatch.invoke_action", return_value=0) as invoke:
        assert dispatch(args) == 0
    invoke.assert_called_once_with(
        args,
        "goal.create",
        {
            "userId": "ppx-client-user",
            "agentId": "main",
            "sessionId": "session-1",
            "objective": "Ship the release",
            "completionCriteria": ["All checks pass"],
            "constraints": ["Do not publish"],
            "workspaceRef": "",
            "budgetPolicy": {},
        },
    )


def test_goal_complete_requires_json_array_evidence(capsys) -> None:
    args = build_parser().parse_args([
        "goal",
        "complete",
        "goal-1",
        "3",
        "--evidence-json",
        "{}",
    ])
    assert dispatch(args) == 2
    assert "must contain one JSON array" in capsys.readouterr().out


def test_node_run_passes_explicit_root_and_transport() -> None:
    args = Namespace(
        command="node",
        node_command="run",
        node_root="/tmp/node",
        host="127.0.0.1",
        port=18765,
        access_token="token",
    )
    with patch("openppx.command.dispatch.run_node") as run:
        assert dispatch(args) == 0
    run.assert_called_once_with("/tmp/node", host="127.0.0.1", port=18765, access_token="token")
