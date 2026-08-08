"""Stable Google ADK identity mapping for OpenPPX resources."""

from __future__ import annotations

import re


LEGACY_ADK_APP_NAME = "openppx"
_AGENT_ID_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def adk_app_name_for_agent_id(agent_id: str) -> str:
    """Return the immutable ADK app name owned by one OpenPPX Agent ID.

    OpenPPX Agent IDs cannot contain underscores, so replacing hyphens with
    underscores is reversible and keeps the result valid as an ADK Agent name.
    The mutable display name is intentionally excluded from this identity.
    """
    normalized = str(agent_id or "").strip()
    if not _AGENT_ID_PATTERN.fullmatch(normalized):
        raise ValueError(f"Invalid OpenPPX Agent ID: {agent_id!r}")
    return f"openppx_agent_{normalized.replace('-', '_')}"
