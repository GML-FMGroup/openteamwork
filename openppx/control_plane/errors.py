"""Stable redacted domain failure mapping for Control Plane handlers."""

from __future__ import annotations

from typing import NoReturn

from openppx.actions import ActionError, ActionFailure
from openppx.config import ConfigError, ConfigLoadError, ConfigRevisionConflict, ConfigWriteError
from openppx.modeling import ModelSelectionError


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
