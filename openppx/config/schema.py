"""JSON Schema export for strict configuration resources."""

from __future__ import annotations

from .models import AgentConfig, NodeConfig


def export_config_schemas() -> dict[str, dict[str, object]]:
    """Return schemas generated from the authoritative Pydantic models."""
    return {
        "AgentConfig": AgentConfig.model_json_schema(by_alias=True),
        "NodeConfig": NodeConfig.model_json_schema(by_alias=True),
    }
