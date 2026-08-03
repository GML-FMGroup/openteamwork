"""Structured, redacted diagnostics for configuration resources."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, TypeAlias

from pydantic import ValidationError


ConfigErrorKind: TypeAlias = Literal[
    "not_found",
    "invalid_utf8",
    "invalid_json",
    "invalid_root",
    "invalid_schema",
    "name_mismatch",
    "path_outside_root",
    "io_error",
    "revision_conflict",
    "lock_timeout",
    "write_failed",
]
ConfigPathSegment: TypeAlias = str | int


@dataclass(frozen=True, slots=True)
class ConfigIssue:
    """One safe, machine-readable configuration problem."""

    code: str
    path: tuple[ConfigPathSegment, ...]
    message: str
    source: str
    severity: Literal["error"] = "error"
    line: int | None = None
    column: int | None = None


@dataclass(frozen=True, slots=True)
class ConfigDiagnostics:
    """Result of validating one configuration source without raising."""

    ok: bool
    source: str
    issues: tuple[ConfigIssue, ...] = ()
    error_kind: ConfigErrorKind | None = None
    revision: str | None = None

    def __str__(self) -> str:
        """Render only redacted diagnostic fields."""
        return json.dumps(
            {
                "ok": self.ok,
                "source": self.source,
                "errorKind": self.error_kind,
                "revision": self.revision,
                "issues": [
                    {
                        "code": issue.code,
                        "path": list(issue.path),
                        "message": issue.message,
                        "source": issue.source,
                        "severity": issue.severity,
                        "line": issue.line,
                        "column": issue.column,
                    }
                    for issue in self.issues
                ],
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )


class ConfigError(ValueError):
    """Base class for safe structured configuration failures."""

    def __init__(
        self,
        path: Path,
        kind: ConfigErrorKind,
        summary: str,
        issues: tuple[ConfigIssue, ...],
    ) -> None:
        self.path = path
        self.kind = kind
        self.summary = summary
        self.issues = issues
        super().__init__(f"{summary} ({kind}) at {path}")


class ConfigLoadError(ConfigError):
    """Raised when a configuration source cannot be loaded safely."""


class ConfigWriteError(ConfigError):
    """Raised when a validated resource cannot be persisted safely."""


class ConfigRevisionConflict(ConfigWriteError):
    """Raised when create/update expectations do not match current state."""

    def __init__(
        self,
        path: Path,
        *,
        source: str,
        expected_revision: str | None,
        actual_revision: str | None,
    ) -> None:
        self.expected_revision = expected_revision
        self.actual_revision = actual_revision
        issue = ConfigIssue(
            "revision_conflict",
            (),
            "Configuration revision does not match current state.",
            source,
        )
        super().__init__(
            path,
            "revision_conflict",
            "Configuration revision conflict",
            (issue,),
        )


_VALIDATION_CODE_MAP: dict[str, str] = {
    "extra_forbidden": "unknown_field",
    "int_type": "invalid_type",
    "bool_type": "invalid_type",
    "string_type": "invalid_type",
    "list_type": "invalid_type",
    "dict_type": "invalid_type",
    "literal_error": "invalid_value",
}


def validation_issues(error: ValidationError, *, source: str) -> tuple[ConfigIssue, ...]:
    """Convert Pydantic errors to stable issues without retaining input values."""
    issues: list[ConfigIssue] = []
    for item in error.errors(
        include_url=False,
        include_context=False,
        include_input=False,
    ):
        error_type = str(item.get("type", "invalid_value"))
        code = _VALIDATION_CODE_MAP.get(error_type)
        if code is None:
            code = "invalid_type" if error_type.endswith("_type") else "invalid_value"
        message = {
            "unknown_field": "Unknown setting.",
            "invalid_type": "Setting has an invalid type.",
            "invalid_value": "Setting has an invalid value.",
        }[code]
        issues.append(
            ConfigIssue(
                code=code,
                path=tuple(item.get("loc", ())),
                message=message,
                source=source,
            )
        )
    return tuple(issues)


def read_json_object(path: Path, *, source: str) -> dict[str, object]:
    """Read one UTF-8 JSON object or raise a structured load error."""
    try:
        payload = path.read_bytes()
    except FileNotFoundError as exc:
        issue = ConfigIssue("not_found", (), "Configuration file was not found.", source)
        raise ConfigLoadError(path, "not_found", "Configuration file was not found", (issue,)) from exc
    except OSError as exc:
        issue = ConfigIssue("io_error", (), "Configuration file could not be read.", source)
        raise ConfigLoadError(path, "io_error", "Configuration file could not be read", (issue,)) from exc

    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        issue = ConfigIssue("invalid_utf8", (), "Configuration file is not valid UTF-8.", source)
        raise ConfigLoadError(path, "invalid_utf8", "Configuration file is not valid UTF-8", (issue,)) from exc

    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        issue = ConfigIssue(
            "invalid_json",
            (),
            "Configuration file is not valid JSON.",
            source,
            line=exc.lineno,
            column=exc.colno,
        )
        raise ConfigLoadError(path, "invalid_json", "Configuration file is not valid JSON", (issue,)) from exc

    if not isinstance(document, dict):
        issue = ConfigIssue("invalid_root", (), "Configuration root must be an object.", source)
        raise ConfigLoadError(path, "invalid_root", "Configuration root must be an object", (issue,))
    return document
