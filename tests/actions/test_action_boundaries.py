"""Dependency-direction guards for the Action domain kernel."""

from __future__ import annotations

from pathlib import Path


def test_action_kernel_has_no_transport_ui_or_runtime_dependencies() -> None:
    root = Path(__file__).parents[2] / "openppx" / "actions"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))

    forbidden = (
        "argparse",
        "http.server",
        "runtime.client_api",
        "google.adk",
        "electron",
        "openppx.control_plane",
    )
    assert not [dependency for dependency in forbidden if dependency in source]
