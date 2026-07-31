"""Versioned contract metadata for the OpenPPX Client API."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


CLIENT_API_SERVICE = "openppx-client-api"
CLIENT_API_PROTOCOL_VERSION = 1
CLIENT_API_CAPABILITIES = (
    "agents.multi",
    "runs.sse",
    "sessions",
)


def get_openppx_product_version() -> str:
    """Return the installed OpenPPX version without requiring package imports."""

    try:
        return version("openppx")
    except PackageNotFoundError:
        return "unknown"


def build_client_api_health_data(
    *,
    data_dir: Path,
    agents: int,
    timestamp: str,
) -> dict[str, Any]:
    """Build the versioned data object returned by the Client API health endpoint."""

    return {
        "service": CLIENT_API_SERVICE,
        "product_version": get_openppx_product_version(),
        "protocol_version": CLIENT_API_PROTOCOL_VERSION,
        "ready": True,
        "state": "healthy",
        "data_dir": str(data_dir),
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
        "capabilities": list(CLIENT_API_CAPABILITIES),
        "agents": agents,
        "authentication_required": authentication_required,
    }
