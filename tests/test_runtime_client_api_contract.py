from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from openppx.runtime.client_api_contract import (
    CLIENT_API_PROTOCOL_VERSION,
    CLIENT_API_SERVICE,
    build_client_api_health_data,
    get_openppx_product_version,
)
from openppx.runtime.client_api_service import ClientApiCoordinator


_FIXTURE_DIR = Path(__file__).parents[1] / "contracts" / "client-api" / "fixtures"


def _load_fixture(name: str) -> dict[str, Any]:
    """Load one shared Client API contract fixture."""

    return json.loads((_FIXTURE_DIR / name).read_text(encoding="utf-8"))


def test_health_data_matches_shared_protocol_v1_fixture() -> None:
    fixture = _load_fixture("health-v1.json")
    expected = dict(fixture["data"])
    expected["product_version"] = get_openppx_product_version()

    actual = build_client_api_health_data(
        data_dir=Path("<OPENPPX_DATA_DIR>"),
        agents=0,
        timestamp="2026-07-31T00:00:00+00:00",
    )

    assert actual == expected
    assert actual["service"] == CLIENT_API_SERVICE
    assert actual["protocol_version"] == CLIENT_API_PROTOCOL_VERSION


def test_incompatible_health_fixture_is_a_future_protocol() -> None:
    fixture = _load_fixture("health-incompatible.json")

    assert fixture["ok"] is True
    assert fixture["data"]["service"] == CLIENT_API_SERVICE
    assert fixture["data"]["protocol_version"] > CLIENT_API_PROTOCOL_VERSION


def test_coordinator_health_serves_the_versioned_handshake(tmp_path: Path) -> None:
    coordinator = ClientApiCoordinator(data_dir=tmp_path)
    payload = coordinator.health()

    assert payload["ok"] is True
    assert payload["data"]["service"] == CLIENT_API_SERVICE
    assert payload["data"]["protocol_version"] == CLIENT_API_PROTOCOL_VERSION
    assert payload["data"]["ready"] is True
