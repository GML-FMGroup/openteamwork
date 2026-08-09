"""Google ADK-native authorization observation and enforcement plugin."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any, Literal

from google.adk.plugins.base_plugin import BasePlugin

from openppx.permissions import (
    PermissionRequest,
    PermissionSnapshotAuthority,
    ResolvedPermissionSnapshot,
    authorize_network_url,
    evaluate_permission,
)
from openppx.permissions.audit import NullPermissionAuditSink, PermissionAuditSink
from openppx.permissions.tooling import describe_adk_tool


PermissionRolloutMode = Literal["observe", "enforce"]


class OpenPpxAuthorizationPlugin(BasePlugin):
    """Authorize every ADK Tool invocation against current trusted permissions.

    The plugin provides the common Tool boundary for raw callables, ADK
    FunctionTool/BaseTool implementations, MCP tools, and native App tools.
    Object-specific executors still perform their own Path, Command, Process,
    and Network authorization because an ADK callback is not an OS boundary.
    """

    def __init__(
        self,
        snapshot: ResolvedPermissionSnapshot,
        *,
        audit: PermissionAuditSink | None = None,
        rollout_mode: PermissionRolloutMode | None = None,
        authority: PermissionSnapshotAuthority | None = None,
        fixed_network_policies: Mapping[str, tuple[str, Literal["read", "write"]]] | None = None,
    ) -> None:
        super().__init__(name="openppx_authorization")
        self._snapshot = snapshot
        if authority is not None and authority.baseline.revision != snapshot.revision:
            raise ValueError("permission authority baseline must match the Plugin snapshot")
        self._authority = authority or PermissionSnapshotAuthority(snapshot)
        self._audit = audit or NullPermissionAuditSink()
        self._rollout_mode = rollout_mode
        self._fixed_network_policies = dict(fixed_network_policies or {})

    @property
    def permission_revision(self) -> str:
        """Return the baseline revision used to assemble this Plugin instance."""

        return self._snapshot.revision

    async def before_tool_callback(
        self,
        *,
        tool: Any,
        tool_args: dict[str, Any],
        tool_context: Any,
    ) -> dict[str, Any] | None:
        """Record the Tool decision and block only when rollout is enforce."""

        del tool_args
        try:
            snapshot = self._authority.current()
        except PermissionError:
            return _denial_payload(
                reason_code="permission_snapshot_unavailable",
                revision=self._snapshot.revision,
            )
        rollout_mode = self._rollout_mode or snapshot.rollout_for("tool")
        descriptor = describe_adk_tool(tool)
        try:
            snapshot.assert_enforce_ready("tool")
        except PermissionError:
            return _denial_payload(
                reason_code="permission_enforcement_not_ready",
                revision=snapshot.revision,
            )
        request = _tool_request(
            snapshot,
            descriptor=descriptor,
            tool_context=tool_context,
        )
        decision = evaluate_permission(snapshot, request)
        audit_available = True
        try:
            self._audit.record(request, decision, rollout_mode=rollout_mode)
        except Exception:
            audit_available = False

        _publish_decision_metadata(
            tool_context,
            descriptor=descriptor,
            decision=decision,
            rollout_mode=rollout_mode,
            audit_available=audit_available,
        )
        if rollout_mode != "enforce":
            return self._network_denial(tool, snapshot=snapshot)
        if not audit_available:
            return _denial_payload(
                reason_code="permission_audit_unavailable",
                revision=self._snapshot.revision,
            )
        if decision.outcome == "deny":
            return _denial_payload(
                reason_code=decision.reason_code,
                revision=decision.permission_revision,
            )
        return self._network_denial(tool, snapshot=snapshot)

    def _network_denial(
        self,
        tool: Any,
        *,
        snapshot: ResolvedPermissionSnapshot,
    ) -> dict[str, Any] | None:
        """Intersect Native App Tool access with its fixed provider origin."""

        descriptor = describe_adk_tool(tool)
        metadata = getattr(tool, "custom_metadata", None)
        openppx_meta = metadata.get("openppx") if isinstance(metadata, dict) else None
        origin = openppx_meta.get("networkOrigin") if isinstance(openppx_meta, dict) else None
        fixed_policy: tuple[str, Literal["read", "write"]] | None = None
        if not isinstance(origin, str) or not origin:
            fixed_policy = next(
                (
                    value
                    for prefix, value in sorted(
                        self._fixed_network_policies.items(),
                        key=lambda item: len(item[0]),
                        reverse=True,
                    )
                    if descriptor.name == prefix or descriptor.name.startswith(f"{prefix}_")
                ),
                None,
            )
            origin = fixed_policy[0] if fixed_policy is not None else None
        if not isinstance(origin, str) or not origin:
            return None
        access = fixed_policy[1] if fixed_policy is not None else str(
            openppx_meta.get("access", "read")
        ).lower()
        actions = ("connect", "read") if access == "read" else ("connect", "write", "upload")
        try:
            authorize_network_url(
                snapshot,
                origin,
                method="GET" if access == "read" else "POST",
                actions=actions,
                audit=self._audit,
            )
        except PermissionError:
            return _denial_payload(
                reason_code="network_intersection_denied",
                revision=snapshot.revision,
            )
        return None


def _tool_request(
    snapshot: ResolvedPermissionSnapshot,
    *,
    descriptor: Any,
    tool_context: Any,
) -> PermissionRequest:
    """Construct a Tool request only from Node-owned runtime facts."""

    invocation_id = _bounded_id(getattr(tool_context, "invocation_id", None))
    function_call_id = _bounded_id(getattr(tool_context, "function_call_id", None))
    session = getattr(tool_context, "session", None)
    session_id = _bounded_id(getattr(session, "id", None))
    run_id = _bounded_id(getattr(tool_context, "run_id", None)) or invocation_id
    request_id = function_call_id or invocation_id or f"permission-{uuid.uuid4().hex}"
    return PermissionRequest.model_validate(
        {
            "requestId": request_id,
            "permissionRevision": snapshot.revision,
            "subject": {
                "agentId": snapshot.agent_id,
                "runId": run_id,
                "sessionId": session_id,
            },
            "object": "tool",
            "action": "invoke",
            "resource": {
                "kind": "tool",
                "toolId": descriptor.tool_id,
                "operation": descriptor.operation,
                "source": descriptor.source,
            },
        }
    )


def _bounded_id(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > 256:
        return None
    if any(ord(character) < 32 or ord(character) == 127 for character in normalized):
        return None
    return normalized


def _publish_decision_metadata(
    tool_context: Any,
    *,
    descriptor: Any,
    decision: Any,
    rollout_mode: str,
    audit_available: bool,
) -> None:
    """Attach a bounded decision projection to ADK invocation metadata."""

    metadata = getattr(tool_context, "custom_metadata", None)
    if not isinstance(metadata, dict):
        return
    metadata["openppx.permission"] = {
        "toolId": descriptor.tool_id,
        "operation": descriptor.operation,
        "rolloutMode": rollout_mode,
        "outcome": decision.outcome,
        "reasonCode": decision.reason_code,
        "permissionRevision": decision.permission_revision,
        "auditAvailable": audit_available,
    }


def _denial_payload(*, reason_code: str, revision: str) -> dict[str, Any]:
    """Return the stable ADK Tool result used when enforcement denies a call."""

    return {
        "ok": False,
        "error": {
            "code": "permission_denied",
            "reasonCode": reason_code,
            "message": "This Tool invocation is not allowed by the Agent permission policy.",
            "permissionRevision": revision,
        },
    }


__all__ = ["OpenPpxAuthorizationPlugin", "PermissionRolloutMode"]
