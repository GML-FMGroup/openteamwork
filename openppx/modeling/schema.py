"""JSON Schema export for Model Profile resources."""

from __future__ import annotations

from typing import Any

from .profiles import ModelProfile


def export_model_profile_schema() -> dict[str, Any]:
    """Return the generated strict Model Profile JSON Schema."""
    return ModelProfile.model_json_schema(by_alias=True)
