from __future__ import annotations

import io
import json
from pathlib import Path
from unittest.mock import patch

from openppx.command.dispatch import dispatch
from openppx.command.parser import build_parser
from openppx.runtime.user_accounts import UserAccountService


def test_user_add_reads_secret_from_hidden_confirmed_prompt(tmp_path: Path, capsys) -> None:
    args = build_parser().parse_args(
        ["user", "add", "Jiang@Example.com", "--privilege", "high", "--node-root", str(tmp_path)]
    )

    with patch("openppx.command.user.getpass", side_effect=["correct horse battery staple", "correct horse battery staple"]):
        assert dispatch(args) == 0

    account = UserAccountService(db_path=tmp_path / "database" / "identity.db").list_users()[0]
    assert account.email == "jiang@example.com"
    assert account.privilege_level == "high"
    assert "correct horse" not in capsys.readouterr().out


def test_user_add_supports_secret_stdin_without_positional_secret(tmp_path: Path, capsys) -> None:
    args = build_parser().parse_args(
        [
            "user",
            "add",
            "jiang@example.com",
            "--privilege",
            "low",
            "--secret-stdin",
            "--node-root",
            str(tmp_path),
            "--json",
        ]
    )

    with patch("openppx.command.user.sys.stdin", io.StringIO("noninteractive-secret\n")):
        assert dispatch(args) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["email"] == "jiang@example.com"
    assert payload["privilegeLevel"] == "low"
    assert "secret" not in json.dumps(payload).lower()


def test_user_list_never_projects_secret_hashes(tmp_path: Path, capsys) -> None:
    service = UserAccountService(db_path=tmp_path / "database" / "identity.db")
    service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="root",
    )
    args = build_parser().parse_args(["user", "list", "--node-root", str(tmp_path), "--json"])

    assert dispatch(args) == 0

    output = capsys.readouterr().out
    assert json.loads(output)["items"][0]["status"] == "active"
    assert "secret" not in output.lower()
    assert "hash" not in output.lower()


def test_user_disable_requires_confirmation_and_revokes_account(tmp_path: Path, capsys) -> None:
    service = UserAccountService(db_path=tmp_path / "database" / "identity.db")
    service.add_user(
        email="jiang@example.com",
        secret="correct horse battery staple",
        privilege_level="medium",
    )
    unconfirmed = build_parser().parse_args(
        ["user", "disable", "jiang@example.com", "--node-root", str(tmp_path)]
    )
    confirmed = build_parser().parse_args(
        ["user", "disable", "jiang@example.com", "--node-root", str(tmp_path), "--yes"]
    )

    assert dispatch(unconfirmed) == 2
    assert "--yes" in capsys.readouterr().out
    assert service.list_users()[0].status == "active"
    assert dispatch(confirmed) == 0
    assert service.list_users()[0].status == "disabled"
