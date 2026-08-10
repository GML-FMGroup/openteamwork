"""Versioned contract metadata for the OpenPPX Client API."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from typing import Any

from ..product import PRODUCT


CLIENT_API_SERVICE = "openppx-client-api"
CLIENT_API_PROTOCOL_VERSION = 1
CLIENT_API_CAPABILITIES = (
    "agents.multi",
    "runs.sse",
    "sessions",
)


def get_openppx_product_version() -> str:
    """Return the installed product version without changing the shared protocol identity."""

    try:
        return version(PRODUCT.python_distribution_name)
    except PackageNotFoundError:
        return "unknown"


def build_client_api_health_data(
    *,
    agents: int,
    timestamp: str,
    ready: bool = True,
    state: str = "healthy",
) -> dict[str, Any]:
    """Build health metadata without exposing Node filesystem paths."""

    return {
        "service": CLIENT_API_SERVICE,
        "product_version": get_openppx_product_version(),
        "protocol_version": CLIENT_API_PROTOCOL_VERSION,
        "ready": ready,
        "state": state,
        "agents": agents,
        "timestamp": timestamp,
    }


def build_public_client_api_health_data(*, timestamp: str) -> dict[str, Any]:
    """Build the non-sensitive liveness and protocol negotiation payload."""

    return {
        "service": CLIENT_API_SERVICE,
        "product_version": get_openppx_product_version(),
        "protocol_version": CLIENT_API_PROTOCOL_VERSION,
        "ready": True,
        "state": "healthy",
        "timestamp": timestamp,
    }


def build_client_api_node_data(
    *,
    node_id: str,
    display_name: str,
    agents: int,
    authentication_required: bool,
    capabilities: tuple[str, ...] = CLIENT_API_CAPABILITIES,
) -> dict[str, Any]:
    """Build authenticated Node identity and capability metadata."""

    return {
        "node_id": node_id,
        "display_name": display_name,
        "product_version": get_openppx_product_version(),
        "protocol": {
            "min": CLIENT_API_PROTOCOL_VERSION,
            "max": CLIENT_API_PROTOCOL_VERSION,
        },
        "capabilities": list(capabilities),
        "agents": agents,
        "authentication_required": authentication_required,
    }
