from __future__ import annotations

from openppx.runtime.node_service import (
    detect_service_manager,
    node_service_name,
    render_launchd_plist,
    render_systemd_unit,
)


def test_detect_service_manager_by_platform_name() -> None:
    assert detect_service_manager("darwin") == "launchd"
    assert detect_service_manager("linux") == "systemd"
    assert detect_service_manager("win32") == "unsupported"


def test_node_service_name_normalization() -> None:
    assert node_service_name() == "openteamwork-node"
    assert node_service_name("openppx") == "openppx-node"
    assert node_service_name("  openppx dev ") == "openppx-dev-node"


def test_render_launchd_plist_contains_node_command() -> None:
    content = render_launchd_plist(
        label="ai.openppx.node",
        program="/usr/local/bin/ppx",
        args=["node", "run", "--node-root", "/tmp/node"],
        working_directory="/tmp/openppx",
    )
    assert "<string>ai.openppx.node</string>" in content
    assert "<string>node</string>" in content
    assert "<string>run</string>" in content


def test_render_systemd_unit_contains_node_command() -> None:
    content = render_systemd_unit(
        description="OpenPPX Node",
        exec_start="/usr/local/bin/ppx node run --node-root /tmp/node",
        working_directory="/tmp/openppx",
    )
    assert "Description=OpenPPX Node" in content
    assert "ExecStart=/usr/local/bin/ppx node run" in content
    assert "WantedBy=default.target" in content
