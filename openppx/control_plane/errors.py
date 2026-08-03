"""Stable redacted domain failure mapping for Control Plane handlers."""

from __future__ import annotations

from typing import NoReturn

from openppx.actions import ActionError, ActionFailure
from openppx.config import ConfigError, ConfigLoadError, ConfigRevisionConflict, ConfigWriteError
from openppx.modeling import ModelSelectionError
from openppx.extensions import ExtensionError


def raise_config_failure(exc: ConfigError) -> NoReturn:
    """Raise one stable Action failure without exposing resource filesystem paths."""
    if isinstance(exc, ConfigRevisionConflict):
        raise ActionFailure(
            ActionError(
                "revision_conflict",
                "The resource changed since it was read.",
                details={
                    "expectedRevision": exc.expected_revision,
                    "actualRevision": exc.actual_revision,
                },
                retryable=True,
            )
        ) from None
    issues = [
        {
            "code": issue.code,
            "path": list(issue.path),
            "message": issue.message,
            "source": issue.source,
        }
        for issue in exc.issues
    ]
    if isinstance(exc, ConfigLoadError) and exc.kind == "not_found":
        code = "resource_not_found"
        message = "The requested resource was not found."
    elif isinstance(exc, ConfigWriteError):
        code = "write_failed"
        message = "The resource could not be persisted."
    else:
        code = "invalid_resource"
        message = "The resource is invalid."
    raise ActionFailure(
        ActionError(
            code,
            message,
            details={"errorKind": exc.kind, "issues": issues},
            retryable=exc.kind in {"lock_timeout", "io_error"},
        )
    ) from None

def raise_model_failure(exc: ModelSelectionError) -> NoReturn:
    """Raise a stable Model readiness failure containing only profile IDs/reasons."""
    raise ActionFailure(
        ActionError(
            "model_not_ready",
            "No configured Model Profile is ready for this request.",
            details={
                "attempts": [
                    {"profileId": attempt.profile_id, "reason": attempt.reason}
                    for attempt in exc.attempts
                ]
            },
        )
    ) from None


def raise_extension_failure(exc: ExtensionError) -> NoReturn:
    """Raise one stable Extension failure with an allowlisted detail projection."""
    allowed = {
        "actualRevision",
        "agentIds",
        "capabilities",
        "connectionId",
        "connectionIds",
        "environment",
        "executables",
        "expectedRevision",
        "issues",
        "references",
    }
    details = {key: value for key, value in exc.details.items() if key in allowed}
    retryable = exc.code in {"registry_busy", "source_unavailable", "write_failed"}
    raise ActionFailure(
        ActionError(
            exc.code,
            _extension_message(exc.code),
            details=details,
            retryable=retryable,
        )
    ) from None


def _extension_message(code: str) -> str:
    """Map Extension failures to bounded product messages rather than backend text."""
    return {
        "confirmation_required": "The Extension operation requires explicit confirmation.",
        "dependency_missing": "The Extension is not ready because a dependency is unavailable.",
        "extension_conflict": "The Extension conflicts with another installed resource.",
        "extension_in_use": "The Extension is still enabled or referenced.",
        "extension_not_found": "The requested Extension resource was not found.",
        "extension_unavailable": "The installed Extension content is unavailable.",
        "invalid_extension_kind": "The requested Extension kind is not supported.",
        "invalid_identity": "The Extension identity is invalid.",
        "invalid_manifest": "The Extension manifest is invalid.",
        "invalid_operation": "The operation is not valid for this Extension resource.",
        "invalid_registry": "The installed Extension record is invalid.",
        "invalid_source": "The Extension source reference is invalid.",
        "registry_busy": "The Extension registry is busy; retry with a fresh revision.",
        "revision_conflict": "The Extension changed since it was read.",
        "source_unavailable": "The Extension source is unavailable.",
        "unsafe_path": "The Extension package contains an unsafe path.",
        "write_failed": "The Extension resource could not be persisted.",
    }.get(code, "The Extension operation could not be completed.")
