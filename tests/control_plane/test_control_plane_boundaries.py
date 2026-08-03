"""Dependency and legacy-parser guards for the Control Plane cutover."""

from __future__ import annotations

from pathlib import Path


def test_control_plane_has_no_transport_ui_or_adk_runtime_dependencies() -> None:
    root = Path(__file__).parents[2] / "openppx" / "control_plane"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))

    forbidden = (
        "argparse",
        "http.server",
        "runtime.client_api_service",
        "google.adk",
        "electron",
    )
    assert not [dependency for dependency in forbidden if dependency in source]


def test_client_api_transport_no_longer_parses_business_config_files() -> None:
    service = Path(__file__).parents[2] / "openppx" / "runtime" / "client_api_service.py"
    source = service.read_text(encoding="utf-8")

    assert "global_config.json" not in source
    assert "def list_enabled_agent_names" not in source
    assert "def build_agent_profile" not in source
    assert "def _read_json_file" not in source
    assert "load_or_create_node_identity" not in source
