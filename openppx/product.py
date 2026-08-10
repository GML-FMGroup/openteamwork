"""Trusted build-time product identity for the OpenTeamwork edition."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files


_RESOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ENVIRONMENT_PREFIX_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]*$")
_NPM_PACKAGE_PATTERN = re.compile(r"^@[a-z0-9][a-z0-9-]*/[a-z0-9][a-z0-9-]*$")
_APPLICATION_ID_PATTERN = re.compile(r"^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*){2,}$")


@dataclass(frozen=True, slots=True)
class ProductProfile:
    """Validated product-shell differences shared by backend and Desktop code."""

    product_id: str
    display_name: str
    python_distribution_name: str
    cli_command: str
    cli_aliases: tuple[str, ...]
    egress_proxy_command: str
    npm_workspace_name: str
    desktop_package_name: str
    desktop_app_id: str
    desktop_artifact_name: str
    node_root_directory: str
    workspace_state_directory: str
    credential_service: str
    service_namespace: str
    default_client_api_port: int
    environment_prefix: str
    default_agent_id: str
    default_agent_display_name: str
    allowed_agent_privilege_levels: tuple[str, ...]
    default_agent_privilege_level: str
    desktop_agent_creation_enabled: bool
    desktop_user_data_directory: str

    @property
    def source_root_environment_variable(self) -> str:
        """Return the product source-checkout override name."""
        return f"{self.environment_prefix}_ROOT"

    @property
    def node_root_environment_variable(self) -> str:
        """Return the product Node-root override name."""
        return f"{self.environment_prefix}_NODE_ROOT"

    @property
    def client_api_port_environment_variable(self) -> str:
        """Return the product local Client API port override name."""
        return f"{self.environment_prefix}_CLIENT_API_PORT"


def _required_text(payload: dict[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise RuntimeError(f"product profile field '{key}' must be non-empty text")
    return value.strip()


def _hidden_directory(payload: dict[str, object], key: str) -> str:
    value = _required_text(payload, key)
    if not re.fullmatch(r"\.[a-z0-9][a-z0-9-]*", value):
        raise RuntimeError(f"product profile field '{key}' must be one hidden directory name")
    return value


def _load_product_profile() -> ProductProfile:
    """Load and validate the packaged product profile exactly once."""
    payload = json.loads(files("openppx").joinpath("product.json").read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("product profile must be a JSON object")

    product_id = _required_text(payload, "productId")
    default_agent_id = _required_text(payload, "defaultAgentId")
    if _RESOURCE_NAME_PATTERN.fullmatch(product_id) is None:
        raise RuntimeError("product profile productId must be a resource name")
    if _RESOURCE_NAME_PATTERN.fullmatch(default_agent_id) is None:
        raise RuntimeError("product profile defaultAgentId must be a resource name")

    port = payload.get("defaultClientApiPort")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65_535:
        raise RuntimeError("product profile defaultClientApiPort must be a valid port")

    environment_prefix = _required_text(payload, "environmentPrefix")
    if _ENVIRONMENT_PREFIX_PATTERN.fullmatch(environment_prefix) is None:
        raise RuntimeError("product profile environmentPrefix must use uppercase environment syntax")

    raw_levels = payload.get("allowedAgentPrivilegeLevels")
    if not isinstance(raw_levels, list) or not raw_levels or not all(
        isinstance(item, str) and item in {"low", "medium", "high", "root"}
        for item in raw_levels
    ):
        raise RuntimeError("product profile allowedAgentPrivilegeLevels is invalid")
    levels = tuple(dict.fromkeys(raw_levels))
    default_level = _required_text(payload, "defaultAgentPrivilegeLevel")
    if default_level not in levels:
        raise RuntimeError("default Agent privilege must be allowed by the product profile")

    desktop_creation = payload.get("desktopAgentCreationEnabled")
    if not isinstance(desktop_creation, bool):
        raise RuntimeError("product profile desktopAgentCreationEnabled must be boolean")

    command_names = {
        "pythonDistributionName": _required_text(payload, "pythonDistributionName"),
        "cliCommand": _required_text(payload, "cliCommand"),
        "egressProxyCommand": _required_text(payload, "egressProxyCommand"),
        "npmWorkspaceName": _required_text(payload, "npmWorkspaceName"),
    }
    for key, value in command_names.items():
        if _RESOURCE_NAME_PATTERN.fullmatch(value) is None:
            raise RuntimeError(f"product profile {key} must be a lowercase package name")
    raw_cli_aliases = payload.get("cliAliases")
    if not isinstance(raw_cli_aliases, list) or not all(isinstance(item, str) for item in raw_cli_aliases):
        raise RuntimeError("product profile cliAliases must be a list of command names")
    cli_aliases = tuple(dict.fromkeys(item.strip() for item in raw_cli_aliases))
    if any(not item or _RESOURCE_NAME_PATTERN.fullmatch(item) is None for item in cli_aliases):
        raise RuntimeError("product profile cliAliases contains an invalid command name")
    if command_names["cliCommand"] in cli_aliases:
        raise RuntimeError("product profile cliAliases must not repeat cliCommand")
    desktop_package_name = _required_text(payload, "desktopPackageName")
    if _NPM_PACKAGE_PATTERN.fullmatch(desktop_package_name) is None:
        raise RuntimeError("product profile desktopPackageName must be a scoped npm package name")
    desktop_app_id = _required_text(payload, "desktopAppId")
    if _APPLICATION_ID_PATTERN.fullmatch(desktop_app_id) is None:
        raise RuntimeError("product profile desktopAppId must be a reverse-domain application id")

    return ProductProfile(
        product_id=product_id,
        display_name=_required_text(payload, "displayName"),
        python_distribution_name=command_names["pythonDistributionName"],
        cli_command=command_names["cliCommand"],
        cli_aliases=cli_aliases,
        egress_proxy_command=command_names["egressProxyCommand"],
        npm_workspace_name=command_names["npmWorkspaceName"],
        desktop_package_name=desktop_package_name,
        desktop_app_id=desktop_app_id,
        desktop_artifact_name=_required_text(payload, "desktopArtifactName"),
        node_root_directory=_hidden_directory(payload, "nodeRootDirectory"),
        workspace_state_directory=_hidden_directory(payload, "workspaceStateDirectory"),
        credential_service=_required_text(payload, "credentialService"),
        service_namespace=_required_text(payload, "serviceNamespace"),
        default_client_api_port=port,
        environment_prefix=environment_prefix,
        default_agent_id=default_agent_id,
        default_agent_display_name=_required_text(payload, "defaultAgentDisplayName"),
        allowed_agent_privilege_levels=levels,
        default_agent_privilege_level=default_level,
        desktop_agent_creation_enabled=desktop_creation,
        desktop_user_data_directory=_required_text(payload, "desktopUserDataDirectory"),
    )


PRODUCT = _load_product_profile()


__all__ = ["PRODUCT", "ProductProfile"]
