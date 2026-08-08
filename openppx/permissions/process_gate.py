"""Process authorization based on Node-recorded provenance, never caller claims."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from .audit import NullPermissionAuditSink, PermissionAuditSink, record_permission_audit
from .evaluator import evaluate_permission
from .models import ProcessConstraints, PermissionAction, PermissionRequest, ResolvedPermissionSnapshot


@dataclass(frozen=True, slots=True)
class ProcessFacts:
    """Trusted facts copied from ProcessSessionManager state."""

    process_id: int | None = None
    created_by_agent_id: str | None = None
    created_by_task_id: str | None = None
    created_by_allowed_command: bool = False
    protected: bool = False
    system_process: bool = False


def authorize_process(
    snapshot: ResolvedPermissionSnapshot,
    *,
    action: PermissionAction,
    facts: ProcessFacts,
    task_id: str | None,
    run_id: str | None = None,
    audit: PermissionAuditSink | None = None,
) -> bool:
    """Authorize a process action and return whether enforcement is active."""

    snapshot.assert_enforce_ready("process")
    request = PermissionRequest.model_validate(
        {
            "requestId": f"process-{uuid.uuid4().hex}",
            "permissionRevision": snapshot.revision,
            "subject": {"agentId": snapshot.agent_id, "taskId": task_id, "runId": run_id},
            "object": "process",
            "action": action,
            "resource": {
                "kind": "process",
                "processId": facts.process_id,
                "createdByAgentId": facts.created_by_agent_id,
                "createdByTaskId": facts.created_by_task_id,
                "createdByAllowedCommand": facts.created_by_allowed_command,
                "protected": facts.protected,
                "systemProcess": facts.system_process,
            },
        }
    )
    decision = evaluate_permission(snapshot, request)
    rollout_mode = snapshot.rollout_for("process")
    record_permission_audit(
        audit or NullPermissionAuditSink(),
        request,
        decision,
        rollout_mode=rollout_mode,
    )
    if rollout_mode == "enforce" and decision.outcome != "allow":
        raise PermissionError(
            f"Process action '{action}' is denied by Agent permissions "
            f"({decision.reason_code}, revision {snapshot.revision})."
        )
    if rollout_mode == "enforce":
        constraints = tuple(
            rule.constraints
            for rule in snapshot.rules
            if rule.rule_id in decision.matched_rule_ids
            and rule.effect == "allow"
            and isinstance(rule.constraints, ProcessConstraints)
        )
        if any(
            item.current_task_only
            and (task_id is None or facts.created_by_task_id != task_id)
            for item in constraints
        ):
            raise PermissionError("Process rule restricts this action to the current task.")
        if any(
            item.current_agent_only and facts.created_by_agent_id != snapshot.agent_id
            for item in constraints
        ):
            raise PermissionError("Process rule restricts this action to the current Agent.")
    return rollout_mode == "enforce"


__all__ = ["ProcessFacts", "authorize_process"]
