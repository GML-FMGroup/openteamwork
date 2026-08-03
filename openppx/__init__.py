"""openppx package."""

from __future__ import annotations

import importlib

from .runtime.adk_version import assert_supported_adk_major as _assert_supported_adk_major

_assert_supported_adk_major()

__all__ = ["agent", "cli"]


def __getattr__(name: str):
    if name == "agent":
        return importlib.import_module(".app.agent", __name__)
    if name == "cli":
        return importlib.import_module(".cli", __name__)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
