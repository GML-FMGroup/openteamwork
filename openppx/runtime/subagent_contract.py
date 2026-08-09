"""Stable contract shared by the Subagent Tool and Node runtime."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class SubagentSpawnRequest:
    """Trusted request for one same-Agent detached background worker."""

    task_id: str
    prompt: str
    agent_id: str
    user_id: str
    session_id: str
    invocation_id: str
    function_call_id: str
    route: str
    scope_id: str
    snapshot_revision: str
    permission_revision: str
    extension_revision: str
    notify_on_complete: bool = True


__all__ = ["SubagentSpawnRequest"]
