"""Redacted domain-to-application projections shared by Action handlers."""

from __future__ import annotations

from typing import Any

from openppx.config import ConfigDiagnostics, VersionedResource
from openppx.config.service import ConfigApplyResult, ConfigPreview, ValidationResult
from openppx.modeling import ModelResolution


def project_issues(diagnostics: ConfigDiagnostics) -> list[dict[str, Any]]:
    """Project stable Config issues without file paths or rejected values."""
    return [
        {
            "code": issue.code,
            "path": list(issue.path),
            "message": issue.message,
            "source": issue.source,
            "severity": issue.severity,
            **({"line": issue.line} if issue.line is not None else {}),
            **({"column": issue.column} if issue.column is not None else {}),
        }
        for issue in diagnostics.issues
    ]


def project_diagnostics(diagnostics: ConfigDiagnostics) -> dict[str, Any]:
    """Return a wire-safe diagnostic object."""
    return {
        "ok": diagnostics.ok,
        "source": diagnostics.source,
        "errorKind": diagnostics.error_kind,
        "revision": diagnostics.revision,
        "issues": project_issues(diagnostics),
    }

def project_resource(resource: VersionedResource[Any]) -> dict[str, Any]:
    """Project a strict resource without filesystem provenance."""
    return {
        "resourceId": resource.resource_id,
        "revision": resource.revision,
        "document": resource.document.model_dump(mode="json", by_alias=True),
    }


def project_validation(result: ValidationResult[Any]) -> dict[str, Any]:
    """Project non-raising validation and its candidate revision."""
    return {
        "valid": result.ok,
        "candidateRevision": result.diagnostics.revision,
        "diagnostics": project_diagnostics(result.diagnostics),
    }


def project_preview(preview: ConfigPreview) -> dict[str, Any]:
    """Project a value-free structural diff and lifecycle effect."""
    return {
        "baseRevision": preview.base_revision,
        "candidateRevision": preview.candidate_revision,
        "changes": [
            {"path": list(change.path), "changeKind": change.change_kind}
            for change in preview.changes
        ],
        "effect": preview.effect.value,
    }


def project_apply(result: ConfigApplyResult[Any]) -> dict[str, Any]:
    """Project persisted identity, structural changes, and lifecycle effect."""
    return {
        "resourceId": result.resource.resource_id,
        "revision": result.resource.revision,
        "changes": [
            {"path": list(change.path), "changeKind": change.change_kind}
            for change in result.changes
        ],
        "effect": result.effect.value,
    }


def project_resolution(resolution: ModelResolution) -> dict[str, Any]:
    """Project Model selection provenance without SecretRef or Secret value."""
    return {
        "profileId": resolution.profile_id,
        "revision": resolution.revision,
        "provider": resolution.provider,
        "model": resolution.model,
        "selectionSource": resolution.selection_source,
        "credentialState": resolution.secret_status.state if resolution.secret_status is not None else "not_required",
        "attempts": [
            {"profileId": attempt.profile_id, "reason": attempt.reason}
            for attempt in resolution.attempts
        ],
    }
