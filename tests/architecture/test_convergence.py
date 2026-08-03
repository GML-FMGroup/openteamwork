"""Forbidden-dependency gate for the first long-term OpenPPX baseline."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "openppx"
DESKTOP = ROOT / "apps" / "desktop"
WORKSPACE_TEMPLATE = ROOT / "workspace"


def _production_text() -> str:
    files = [*PACKAGE.rglob("*.py"), *DESKTOP.joinpath("app", "src").rglob("*.ts*")]
    files.extend(DESKTOP.joinpath("electron").rglob("*.ts"))
    return "\n".join(path.read_text(encoding="utf-8") for path in files if path.is_file())


def test_removed_architecture_paths_do_not_return() -> None:
    forbidden = (
        PACKAGE / "app" / "gateway.py",
        PACKAGE / "app" / "cli.py",
        PACKAGE / "channels",
        PACKAGE / "bus",
        PACKAGE / "bridge",
        PACKAGE / "core" / "config.py",
        PACKAGE / "core" / "doctor_rules.py",
        DESKTOP / "electron" / "main" / "legacy-bridge-client.ts",
        DESKTOP / "electron" / "main" / "development-modes.ts",
        DESKTOP / "app" / "src" / "lib" / "mock-client.ts",
        DESKTOP / "scripts" / "openppx_bridge.py",
    )
    def source_exists(path: Path) -> bool:
        return path.is_file() or (path.is_dir() and any(path.rglob("*.py")))

    assert [str(path.relative_to(ROOT)) for path in forbidden if source_exists(path)] == []


def test_production_has_no_removed_config_or_desktop_fallbacks() -> None:
    text = _production_text()
    forbidden_tokens = (
        "OPENPPX_CONFIG_FILE",
        "OPENPPX_AGENT_HOME",
        "OPENPPX_DATA_DIR",
        "OPENPPX_MCP_SERVERS_JSON",
        "OPENPPX_HEARTBEAT_",
        "OPENPPX_DESKTOP_USE_MOCK",
        "OPENPPX_DESKTOP_FORCE_LEGACY",
        "serve_client_api",
        "build_legacy_root_agent",
        "http://127.0.0.1:8765",
        "RuntimeOutboundEvent",
        "def message(",
        "def message_image(",
        "def message_file(",
    )
    assert [token for token in forbidden_tokens if token in text] == []


def test_package_metadata_has_no_removed_gateway_or_channel_entrypoints() -> None:
    metadata = ROOT.joinpath("pyproject.toml").read_text(encoding="utf-8")
    forbidden = ("openppx-gui-mcp", "openppx.app.gateway", "openppx.channels", "openppx.bridge")
    assert [token for token in forbidden if token in metadata] == []


def test_cli_exposes_only_the_new_product_groups() -> None:
    completed = subprocess.run(
        [sys.executable, "-m", "openppx.cli", "--help"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    output = completed.stdout
    expected = ("setup", "node", "action", "command", "config", "model", "extension", "operations")
    assert all(name in output for name in expected)
    assert all(name not in output for name in ("client-api", "gateway", "channel", "doctor"))


def test_workspace_templates_describe_current_runtime_tools() -> None:
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (WORKSPACE_TEMPLATE / "AGENTS.md", WORKSPACE_TEMPLATE / "TOOLS.md")
    )
    required = ("exec_command", "invoke_skill_api", "task_id", "cron")
    removed = (
        "openppx cron",
        "HEARTBEAT.md",
        "AgentLoop._register_default_tools",
        "### message",
        "### spawn",
    )
    assert all(token in text for token in required)
    assert [token for token in removed if token in text] == []
