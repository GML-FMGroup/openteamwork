from __future__ import annotations

import pytest

from openppx.runtime.adk_identity import adk_app_name_for_agent_id


@pytest.mark.parametrize(
    ("agent_id", "expected"),
    [
        ("main", "openppx_agent_main"),
        ("root-main", "openppx_agent_root_main"),
        ("agent-2", "openppx_agent_agent_2"),
    ],
)
def test_adk_app_name_is_stable_and_derived_from_immutable_agent_id(
    agent_id: str,
    expected: str,
) -> None:
    assert adk_app_name_for_agent_id(agent_id) == expected


@pytest.mark.parametrize("agent_id", ["", "Main", "root_main", "-main", "main-"])
def test_adk_app_name_rejects_invalid_agent_ids(agent_id: str) -> None:
    with pytest.raises(ValueError, match="Invalid OpenPPX Agent ID"):
        adk_app_name_for_agent_id(agent_id)
