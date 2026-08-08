"""Operator Actions for redacted Agent permission decisions."""

from __future__ import annotations

from typing import cast

from openppx.actions import ActionRegistry, ActionSpec
from openppx.permissions import PermissionAuditQuery, PermissionAuditStore

from .input_models import PermissionAuditListInput


def register_permission_actions(
    registry: ActionRegistry,
    audit: PermissionAuditStore,
) -> None:
    """Register the transport-neutral static-permission audit surface."""

    registry.register(
        ActionSpec(
            action_id="permissions.audit.list",
            namespace="permissions",
            title="Read permission audit",
            description="Read redacted Agent static-permission decisions.",
            input_model=PermissionAuditListInput,
            scope="node",
            required_capabilities=frozenset({"audit.read"}),
            permission="audit.read",
            projections=("cli", "desktop", "mobile"),
        ),
        lambda _context, value: _list_permission_audit(
            audit,
            cast(PermissionAuditListInput, value),
        ),
    )


def _list_permission_audit(
    audit: PermissionAuditStore,
    value: PermissionAuditListInput,
) -> dict[str, object]:
    """Project one bounded query without exposing resource values or Tool arguments."""

    items = audit.list(
        PermissionAuditQuery(
            limit=value.limit,
            agent_id=value.agent_id,
            object=value.object,
            outcome=value.outcome,
            permission_revision=value.permission_revision,
        )
    )
    return {"items": list(items), "count": len(items)}


__all__ = ["register_permission_actions"]
