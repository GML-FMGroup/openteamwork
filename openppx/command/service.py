"""User-service installation for the single OpenPPX Node process."""

from __future__ import annotations

import json
import shlex
import shutil
import sys
from pathlib import Path
from typing import Any

from openppx.runtime.node_service import (
    detect_service_manager,
    node_service_name,
    render_launchd_plist,
    render_systemd_unit,
)


def _manifest_path(manager: str, service_name: str) -> Path:
    """Return the current user's manifest path for one supported manager."""
    if manager == "launchd":
        return Path.home() / "Library" / "LaunchAgents" / f"ai.openppx.{service_name}.plist"
    if manager == "systemd":
        return Path.home() / ".config" / "systemd" / "user" / f"{service_name}.service"
    raise ValueError("OpenPPX Node service is supported only on macOS and Linux")


def install_node_service(args: Any) -> int:
    """Write one user-service manifest that runs the canonical Node entry point."""
    manager = detect_service_manager()
    if manager == "unsupported":
        print("Error: Node service installation is supported only on macOS and Linux")
        return 2
    name = node_service_name()
    manifest = _manifest_path(manager, name)
    if manifest.exists() and not args.force:
        print(f"Error: service manifest already exists: {manifest} (pass --force to replace it)")
        return 1
    program = shutil.which("ppx") or sys.executable
    command_args = ["node", "run", "--node-root", str(args.node_root)]
    if program == sys.executable:
        command_args = ["-m", "openppx.cli", *command_args]
    logs = args.node_root / "logs"
    if manager == "launchd":
        content = render_launchd_plist(
            label=f"ai.openppx.{name}",
            program=program,
            args=command_args,
            working_directory=args.node_root,
            stdout_path=logs / "node.out.log",
            stderr_path=logs / "node.err.log",
        )
    else:
        content = render_systemd_unit(
            description="OpenPPX Node",
            exec_start=shlex.join([program, *command_args]),
            working_directory=args.node_root,
        )
    manifest.parent.mkdir(parents=True, exist_ok=True)
    logs.mkdir(parents=True, exist_ok=True)
    manifest.write_text(content, encoding="utf-8")
    print(f"Node service manifest written: {manifest}")
    print("Enable it with launchctl (macOS) or systemctl --user (Linux) when ready.")
    return 0


def node_service_status(args: Any) -> int:
    """Report whether the canonical Node user-service manifest exists."""
    manager = detect_service_manager()
    payload: dict[str, object] = {"manager": manager, "supported": manager != "unsupported"}
    if manager != "unsupported":
        manifest = _manifest_path(manager, node_service_name())
        payload.update({"manifest": str(manifest), "installed": manifest.is_file()})
    if args.output_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload["supported"]:
        state = "installed" if payload.get("installed") else "not installed"
        print(f"OpenPPX Node service: {state}")
        print(f"Manifest: {payload.get('manifest')}")
    else:
        print("OpenPPX Node service: unsupported platform")
    return 0 if payload["supported"] else 2
