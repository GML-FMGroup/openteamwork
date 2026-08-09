"""Trusted current-snapshot authority for long-lived Agent runtimes."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from .models import ResolvedPermissionSnapshot


PermissionSnapshotProvider = Callable[[], ResolvedPermissionSnapshot]


@dataclass(frozen=True, slots=True)
class PermissionSnapshotAuthority:
    """Resolve current permissions while preserving one Runtime identity boundary.

    A permission-only config update may take effect before the next side effect.
    Agent identity or Workspace changes require a new Runtime and therefore fail
    closed in an already assembled Runtime.
    """

    baseline: ResolvedPermissionSnapshot
    provider: PermissionSnapshotProvider | None = field(default=None, repr=False, compare=False)
    required_revision: str | None = None

    def current(self) -> ResolvedPermissionSnapshot:
        """Return the current compatible snapshot or fail closed."""

        if self.provider is None:
            return self.baseline
        try:
            snapshot = self.provider()
        except Exception as exc:
            raise PermissionError("The current Agent permission snapshot is unavailable.") from exc
        if not isinstance(snapshot, ResolvedPermissionSnapshot):
            raise PermissionError("The current Agent permission snapshot is invalid.")
        if snapshot.agent_id != self.baseline.agent_id:
            raise PermissionError("The Agent identity changed and requires a new Runtime.")
        baseline_workspace = Path(self.baseline.workspace).expanduser().resolve(strict=False)
        current_workspace = Path(snapshot.workspace).expanduser().resolve(strict=False)
        if current_workspace != baseline_workspace:
            raise PermissionError("The Agent Workspace changed and requires a new Runtime.")
        if self.required_revision is not None and snapshot.revision != self.required_revision:
            raise PermissionError(
                "The delegated permission ceiling changed; the Subagent must stop or be restarted."
            )
        return snapshot


__all__ = ["PermissionSnapshotAuthority", "PermissionSnapshotProvider"]
