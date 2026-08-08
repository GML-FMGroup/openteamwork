"""Fail-closed verification for Docker proxy-only network boundaries."""

from __future__ import annotations

import subprocess

from .validation import SandboxValidationError


def verify_docker_internal_network(*, docker_bin: str, network_name: str) -> None:
    """Require an existing Docker network whose ``Internal`` flag is true.

    OpenPPX never creates or weakens deployment networks during an Agent call.
    Operators provision the trusted proxy and its internal network separately.
    """

    try:
        completed = subprocess.run(
            [
                docker_bin,
                "network",
                "inspect",
                "--format",
                "{{.Internal}}",
                network_name,
            ],
            shell=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except Exception as exc:
        raise SandboxValidationError("proxy-only Docker network could not be verified") from exc
    if completed.returncode != 0 or completed.stdout.strip().lower() != "true":
        raise SandboxValidationError(
            "proxy-only Docker network must exist and be created with --internal"
        )


__all__ = ["verify_docker_internal_network"]
