"""Tests for durable redacted permission-decision audit facts."""

from __future__ import annotations

from pathlib import Path

import pytest

from openppx.config import AgentConfig, NodeConfig
from openppx.permissions import (
    PermissionAuditQuery,
    PermissionAuditStore,
    PermissionRequest,
    compile_permission_snapshot,
    evaluate_permission,
    record_permission_audit,
)


def _snapshot():
    node = NodeConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "NodeConfig",
            "metadata": {"name": "node"},
            "spec": {"displayName": "Node", "enabledAgents": ["reader"]},
        }
    )
    agent = AgentConfig.model_validate(
        {
            "apiVersion": "openppx.io/v1alpha1",
            "kind": "AgentConfig",
            "metadata": {"name": "reader"},
            "spec": {
                "displayName": "Reader",
                "workspace": "workspace/reader",
                "ownerPrincipalId": "local:owner",
                "privilegeLevel": "low",
            },
        }
    )
    return compile_permission_snapshot(node=node, agent=agent)


def test_permission_audit_records_decision_without_resource_values(tmp_path: Path) -> None:
    snapshot = _snapshot()
    request = PermissionRequest.model_validate(
        {
            "requestId": "call-1",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": "reader", "runId": "run-1", "sessionId": "session-1"},
            "object": "workspace",
            "action": "read",
            "resource": {"kind": "workspace_path", "path": "private/customer-record.txt"},
        }
    )
    decision = evaluate_permission(snapshot, request)
    store = PermissionAuditStore(tmp_path / "permission-audit.db")

    store.record(request, decision, rollout_mode="observe")
    rows = store.list(PermissionAuditQuery(agent_id="reader"))

    assert len(rows) == 1
    assert rows[0]["outcome"] == "allow"
    assert rows[0]["enforced"] is False
    assert rows[0]["permissionRevision"] == snapshot.revision
    assert "customer-record" not in str(rows[0])


def test_audit_failure_isolated_in_observe_and_fails_closed_in_enforce() -> None:
    class BrokenAudit:
        def record(self, request, decision, *, rollout_mode: str) -> None:
            raise OSError("unavailable")

    snapshot = _snapshot()
    request = PermissionRequest.model_validate(
        {
            "requestId": "call-audit-failure",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": "reader"},
            "object": "workspace",
            "action": "read",
            "resource": {"kind": "workspace_path", "path": "faq.txt"},
        }
    )
    decision = evaluate_permission(snapshot, request)

    assert record_permission_audit(BrokenAudit(), request, decision, rollout_mode="observe") is False
    with pytest.raises(PermissionError, match="audit storage"):
        record_permission_audit(BrokenAudit(), request, decision, rollout_mode="enforce")
