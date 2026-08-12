"""Runtime guard for the exact Google ADK version validated by OpenTeamwork."""

from __future__ import annotations

from importlib import metadata

_SUPPORTED_ADK_VERSION = "2.6.3"


def installed_adk_version() -> str:
    """Return the installed google-adk package version."""
    try:
        return metadata.version("google-adk")
    except metadata.PackageNotFoundError as exc:
        raise RuntimeError(
            "google-adk is not installed; "
            f"OpenTeamwork requires google-adk {_SUPPORTED_ADK_VERSION}."
        ) from exc


def assert_supported_adk_major() -> None:
    """Raise unless the installed Google ADK matches the validated runtime version.

    The historical function name is retained because it is imported during package
    initialization. The contract is intentionally stricter than a major-version
    check because OpenTeamwork depends on ADK runtime behavior that can change in a
    minor release.
    """
    version = installed_adk_version()
    if version != _SUPPORTED_ADK_VERSION:
        raise RuntimeError(
            "Unsupported google-adk version "
            f"{version!r}; OpenTeamwork requires google-adk {_SUPPORTED_ADK_VERSION}. "
            "Reinstall the project dependencies before starting OpenTeamwork."
        )
