from __future__ import annotations

import tomllib
from pathlib import Path

from openppx.product import PRODUCT


def test_openteamwork_product_profile_owns_public_identity() -> None:
    assert PRODUCT.product_id == "openteamwork"
    assert PRODUCT.display_name == "OpenTeamwork"
    assert PRODUCT.python_distribution_name == "openteamwork"
    assert PRODUCT.cli_command == "otw"
    assert PRODUCT.cli_aliases == ("openteamwork",)
    assert PRODUCT.egress_proxy_command == "otw-egress-proxy"
    assert PRODUCT.npm_workspace_name == "openteamwork"
    assert PRODUCT.desktop_package_name == "@openteamwork/desktop"
    assert PRODUCT.desktop_app_id == "com.openteamwork.desktop"
    assert PRODUCT.desktop_artifact_name == "OpenTeamwork-Desktop"
    assert PRODUCT.node_root_directory == ".openteamwork"
    assert PRODUCT.workspace_state_directory == ".openteamwork"
    assert PRODUCT.credential_service == "openteamwork"
    assert PRODUCT.service_namespace == "ai.openteamwork"
    assert PRODUCT.default_client_api_port == 18765
    assert PRODUCT.environment_prefix == "OPENTEAMWORK"
    assert PRODUCT.source_root_environment_variable == "OPENTEAMWORK_ROOT"
    assert PRODUCT.node_root_environment_variable == "OPENTEAMWORK_NODE_ROOT"
    assert PRODUCT.client_api_port_environment_variable == "OPENTEAMWORK_CLIENT_API_PORT"


def test_openteamwork_keeps_the_enterprise_agent_defaults() -> None:
    assert PRODUCT.default_agent_id == "main"
    assert PRODUCT.default_agent_display_name == "Main"
    assert PRODUCT.allowed_agent_privilege_levels == ("low", "medium", "high", "root")
    assert PRODUCT.default_agent_privilege_level == "medium"
    assert PRODUCT.desktop_agent_creation_enabled is True


def test_python_distribution_and_entry_points_match_product_profile() -> None:
    manifest = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text(encoding="utf-8"))

    assert manifest["project"]["name"] == PRODUCT.python_distribution_name
    assert manifest["project"]["scripts"] == {
        PRODUCT.cli_command: "openppx.cli:main",
        PRODUCT.cli_aliases[0]: "openppx.cli:main",
        PRODUCT.egress_proxy_command: "openppx.runtime.sandbox.egress_proxy:main",
    }
    assert manifest["project"]["urls"]["Repository"] == "https://github.com/pipixia-labs/openteamwork.git"
    assert manifest["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == ["openppx"]
