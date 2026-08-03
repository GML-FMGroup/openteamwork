"""Immutable governance facts and stable policy decisions."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from openppx.actions.models import ActionContext, ActionSpec

ActionRisk = Literal["low", "medium", "high"]


_SCOPE_KEYS: tuple[tuple[str, str], ...] = (
    ("node_id", "nodeId"),
    ("agent_id", "agentId"),
    ("session_id", "sessionId"),
    ("run_id", "runId"),
    ("task_id", "taskId"),
)
_RESOURCE_KEYS = (
    "extensionId",
    "connectionId",
    "appId",
    "profileId",
    "skillId",
    "serverId",
    "jobId",
)


@dataclass(frozen=True, slots=True)
class PolicyContext:
    """All non-sensitive facts used to authorize one Action invocation."""

    request_id: str
    correlation_id: str
    actor_id: str
    client_id: str | None
    device_id: str | None
    action_id: str
    risk: ActionRisk
    required_capabilities: tuple[str, ...]
    requested_permission: str
    confirmed: bool
    node_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    run_id: str | None = None
    task_id: str | None = None
    extension_id: str | None = None
    resource_id: str | None = None
    requested_scope: tuple[tuple[str, str], ...] = ()

    @classmethod
    def from_action(
        cls,
        spec: "ActionSpec",
        context: "ActionContext",
        raw_input: Mapping[str, object],
    ) -> "PolicyContext":
        """Build bounded governance facts without retaining Action input values."""
        requested_scope: list[tuple[str, str]] = []
        requested: dict[str, str] = {}
        for field_name, alias in _SCOPE_KEYS:
            value = _bounded_identity(raw_input.get(alias, raw_input.get(field_name)))
            if value is not None:
                requested_scope.append((field_name, value))
                requested[field_name] = value
        resource_id = next(
            (
                value
                for key in _RESOURCE_KEYS
                if (value := _bounded_identity(raw_input.get(key))) is not None
            ),
            None,
        )
        extension_id = _bounded_identity(raw_input.get("extensionId"))
        return cls(
            request_id=context.request_id,
            correlation_id=context.correlation_id,
            actor_id=context.actor_id,
            client_id=context.client_id,
            device_id=context.device_id,
            action_id=spec.action_id,
            risk=spec.risk,
            required_capabilities=tuple(sorted(spec.required_capabilities)),
            requested_permission=spec.permission,
            confirmed=context.confirmed,
            node_id=context.node_id or requested.get("node_id"),
            agent_id=context.agent_id or requested.get("agent_id"),
            session_id=context.session_id or requested.get("session_id"),
            run_id=context.run_id or requested.get("run_id"),
            task_id=context.task_id or requested.get("task_id"),
            extension_id=extension_id,
            resource_id=resource_id,
            requested_scope=tuple(requested_scope),
        )


@dataclass(frozen=True, slots=True)
class PolicyDecision:
    """Stable policy result shared by execution, audit, and clients."""

    allow: bool
    code: str
    reason: str
    details: tuple[tuple[str, object], ...] = ()

    def detail_dict(self) -> dict[str, object]:
        """Return deterministic JSON-ready decision details."""
        return dict(self.details)


def _bounded_identity(value: object) -> str | None:
    """Return a bounded visible identifier suitable for governance metadata."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 256:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized


__all__ = ["PolicyContext", "PolicyDecision"]
