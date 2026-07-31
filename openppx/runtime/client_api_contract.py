"""Versioned contract metadata for the OpenPPX Client API."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any


CLIENT_API_SERVICE = "openppx-client-api"
CLIENT_API_PROTOCOL_VERSION = 1


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
