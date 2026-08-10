"""Local deployment-administrator commands for product user accounts."""

from __future__ import annotations

import json
import sys
from getpass import getpass
from pathlib import Path
from typing import Any

from openppx.runtime.user_accounts import UserAccount, UserAccountError, UserAccountService


def _service(node_root: Path) -> UserAccountService:
    """Return the account service scoped to one explicit Node root."""

    return UserAccountService(db_path=node_root / "database" / "identity.db")


def _projection(account: UserAccount) -> dict[str, object]:
    """Project one account without credential or authentication-session fields."""

    return {
        "userId": account.user_id,
        "email": account.email,
        "privilegeLevel": account.privilege_level,
        "status": account.status,
        "createdAtMs": account.created_at_ms,
        "updatedAtMs": account.updated_at_ms,
    }


def _read_secret(*, from_stdin: bool) -> str:
    """Read a secret without accepting it as a process-list-visible argument."""

    if from_stdin:
        return sys.stdin.readline().rstrip("\r\n")
    first = getpass("Secret: ")
    confirmed = getpass("Confirm secret: ")
    if first != confirmed:
        raise UserAccountError("secret_mismatch", "Secret confirmation does not match.")
    return first


def run_user_command(args: Any) -> int:
    """Execute one local-only user administration command."""

    service = _service(Path(args.node_root).expanduser().resolve(strict=False))
    try:
        if args.user_command == "add":
            result: dict[str, object] = _projection(
                service.add_user(
                    email=args.email,
                    secret=_read_secret(from_stdin=bool(args.secret_stdin)),
                    privilege_level=args.privilege_level,
                )
            )
        elif args.user_command == "list":
            result = {"items": [_projection(account) for account in service.list_users()]}
        elif args.user_command == "disable":
            if not args.yes:
                print("Error: disabling a user is permanent in this MVP; pass --yes to confirm.")
                return 2
            result = _projection(service.disable_user(args.email))
        else:  # pragma: no cover - argparse owns the command choices
            raise UserAccountError("unsupported_user_command", "The user command is not supported.")
    except UserAccountError as exc:
        print(f"Error: {exc}")
        return 2

    if bool(args.output_json):
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    elif args.user_command == "list":
        items = result["items"]
        if not items:
            print("No users configured.")
        else:
            for item in items:  # type: ignore[assignment]
                print(f"{item['email']}\t{item['privilegeLevel']}\t{item['status']}\t{item['userId']}")
    else:
        print(
            f"{result['email']}\t{result['privilegeLevel']}\t"
            f"{result['status']}\t{result['userId']}"
        )
    return 0


__all__ = ["run_user_command"]
