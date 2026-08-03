"""Node service manifest helpers for launchd/systemd installation flows."""

from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Literal

ServiceManager = Literal["launchd", "systemd", "unsupported"]


def detect_service_manager(platform_name: str | None = None) -> ServiceManager:
    """Return the supported service manager for the current platform name."""
    import sys

    current = (platform_name or sys.platform).strip().lower()
    if current.startswith("darwin"):
        return "launchd"
    if current.startswith("linux"):
        return "systemd"
    return "unsupported"


def node_service_name(app_name: str = "openppx") -> str:
    """Return a normalized user-service name for one OpenPPX Node."""
    normalized = re.sub(r"[^a-zA-Z0-9._-]+", "-", app_name.strip()).strip("-.") or "openppx"
    return f"{normalized}-node"


def render_launchd_plist(
    *,
    label: str,
    program: str,
    args: list[str],
    working_directory: str | Path,
    env: dict[str, str] | None = None,
    stdout_path: str | Path | None = None,
    stderr_path: str | Path | None = None,
    keep_alive: bool = True,
) -> str:
    """Render one launchd plist for an OpenPPX Node process."""
    escaped_label = html.escape(str(label))
    escaped_workdir = html.escape(str(working_directory))
    arg_lines = "\n".join(
        f"      <string>{html.escape(str(item))}</string>" for item in [program, *args]
    )
    env_block = ""
    if env:
        env_lines = "\n".join(
            f"      <key>{html.escape(str(key))}</key><string>{html.escape(str(value))}</string>"
            for key, value in sorted(env.items())
        )
        env_block = f"\n    <key>EnvironmentVariables</key>\n    <dict>\n{env_lines}\n    </dict>"
    stdout_block = (
        f"\n    <key>StandardOutPath</key>\n    <string>{html.escape(str(stdout_path))}</string>"
        if stdout_path
        else ""
    )
    stderr_block = (
        f"\n    <key>StandardErrorPath</key>\n    <string>{html.escape(str(stderr_path))}</string>"
        if stderr_path
        else ""
    )
    keep_alive_value = "true" if keep_alive else "false"
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n<dict>\n'
        f"    <key>Label</key>\n    <string>{escaped_label}</string>\n"
        f"    <key>ProgramArguments</key>\n    <array>\n{arg_lines}\n    </array>\n"
        f"    <key>WorkingDirectory</key>\n    <string>{escaped_workdir}</string>\n"
        "    <key>RunAtLoad</key>\n    <true/>\n"
        f"    <key>KeepAlive</key>\n    <{keep_alive_value}/>{env_block}{stdout_block}{stderr_block}\n"
        "</dict>\n</plist>\n"
    )


def render_systemd_unit(
    *,
    description: str,
    exec_start: str,
    working_directory: str | Path,
    env: dict[str, str] | None = None,
    restart: str = "always",
    after_targets: tuple[str, ...] = ("network-online.target",),
) -> str:
    """Render one systemd user unit for an OpenPPX Node process."""
    after = " ".join(target.strip() for target in after_targets if target.strip()) or "default.target"
    env_lines = ""
    if env:
        rendered = []
        for key, value in sorted(env.items()):
            escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
            rendered.append(f'Environment="{key}={escaped}"')
        env_lines = "\n".join(rendered) + "\n"
    return (
        f"[Unit]\nDescription={description}\nAfter={after}\n\n"
        f"[Service]\nType=simple\nWorkingDirectory={working_directory}\n"
        f"ExecStart={exec_start}\nRestart={restart}\n{env_lines}\n"
        "[Install]\nWantedBy=default.target\n"
    )
