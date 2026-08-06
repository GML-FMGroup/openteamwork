"""Redacted domain-to-application projections shared by Action handlers."""

from __future__ import annotations

from typing import Any

from openppx.config import ConfigDiagnostics, VersionedResource
from openppx.config.service import ConfigApplyResult, ConfigPreview, ValidationResult
from openppx.modeling import ModelResolution
from openppx.runtime.goal_store import Goal, GoalEvent, TaskFlow
from openppx.automation import AutomationDefinition, AutomationEvent, AutomationRun, AutomationTrigger


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


def project_task_flow(flow: TaskFlow) -> dict[str, Any]:
    """Project one TaskFlow as structured plan and recovery facts."""
    return {
        "flowId": flow.flow_id,
        "goalId": flow.goal_id,
        "status": flow.status,
        "revision": flow.revision,
        "steps": flow.steps,
        "taskRunRefs": flow.task_run_refs,
        "artifactRefs": flow.artifact_refs,
        "waitReason": flow.wait_reason,
        "recoveryState": flow.recovery_state,
        "lastEvent": flow.last_event,
        "createdAtMs": flow.created_at_ms,
        "updatedAtMs": flow.updated_at_ms,
    }


def project_goal_summary(goal: Goal) -> dict[str, Any]:
    """Project one Goal without embedding its potentially large event history."""
    return {
        "goalId": goal.goal_id,
        "sessionId": goal.session_id,
        "agentId": goal.agent_id,
        "userId": goal.user_id,
        "objective": goal.objective,
        "status": goal.status,
        "revision": goal.revision,
        "activeFlowId": goal.active_flow_id,
        "completionCriteria": goal.completion_criteria,
        "budgetState": goal.budget_state,
        "updatedAtMs": goal.updated_at_ms,
        "createdAtMs": goal.created_at_ms,
        "completedAtMs": goal.completed_at_ms,
        "cancelledAtMs": goal.cancelled_at_ms,
    }


def project_goal_detail(goal: Goal, flow: TaskFlow | None) -> dict[str, Any]:
    """Project one complete Goal policy plus its current TaskFlow."""
    return {
        **project_goal_summary(goal),
        "workspaceRef": goal.workspace_ref,
        "constraints": goal.constraints,
        "budgetPolicy": goal.budget_policy,
        "permissionRevision": goal.permission_revision,
        "modelProfileRevision": goal.model_profile_revision,
        "extensionSnapshotDigest": goal.extension_snapshot_digest,
        "completionEvidence": goal.completion_evidence,
        "correlationId": goal.correlation_id,
        "createdBy": goal.created_by,
        "flow": project_task_flow(flow) if flow is not None else None,
    }


def project_goal_event(event: GoalEvent) -> dict[str, Any]:
    """Project one append-only Goal event without storage encoding."""
    return {
        "eventId": event.event_id,
        "goalId": event.goal_id,
        "flowId": event.flow_id,
        "eventType": event.event_type,
        "actorId": event.actor_id,
        "correlationId": event.correlation_id,
        "payload": event.payload,
        "createdAtMs": event.created_at_ms,
    }


def project_automation_trigger(trigger: AutomationTrigger | None) -> dict[str, Any] | None:
    """Project one persisted automatic start condition."""
    if trigger is None:
        return None
    return {
        "triggerId": trigger.trigger_id,
        "type": trigger.trigger_type,
        "enabled": bool(trigger.enabled),
        "schedule": {
            "kind": trigger.schedule_kind,
            "everySeconds": trigger.every_seconds,
            "cronExpr": trigger.cron_expr,
            "atMs": trigger.at_ms,
            "timezone": trigger.timezone,
        } if trigger.trigger_type == "schedule" else None,
        "eventKey": trigger.event_key,
        "inputSchema": trigger.input_schema,
        "nextRunAtMs": trigger.next_run_at_ms,
        "lastRunAtMs": trigger.last_run_at_ms,
    }


def project_automation_run(run: AutomationRun) -> dict[str, Any]:
    """Project one Automation execution and its frozen provenance."""
    return {
        "automationRunId": run.automation_run_id,
        "automationId": run.automation_id,
        "definitionRevision": run.definition_revision,
        "triggerType": run.trigger_type,
        "triggerOccurrenceId": run.trigger_occurrence_id,
        "startedBy": run.started_by,
        "inputSnapshot": run.input_snapshot,
        "principalSnapshot": run.principal_snapshot,
        "permissionRevision": run.permission_revision,
        "agentRevision": run.agent_revision,
        "modelProfileRevision": run.model_profile_revision,
        "extensionSnapshotDigest": run.extension_snapshot_digest,
        "sessionId": run.session_id,
        "adkRunId": run.adk_run_id,
        "taskRunRefs": run.task_run_refs,
        "goalId": run.goal_id,
        "status": run.status,
        "attempt": run.attempt,
        "artifactRefs": run.artifact_refs,
        "deliveryRefs": run.delivery_refs,
        "outputSummary": run.output_summary,
        "errorSummary": run.error_summary,
        "blockedReason": run.blocked_reason,
        "budgetState": run.budget_state,
        "correlationId": run.correlation_id,
        "createdAtMs": run.created_at_ms,
        "startedAtMs": run.started_at_ms,
        "endedAtMs": run.ended_at_ms,
        "updatedAtMs": run.updated_at_ms,
    }


def project_automation_summary(
    definition: AutomationDefinition,
    trigger: AutomationTrigger | None,
    latest_run: AutomationRun | None,
) -> dict[str, Any]:
    """Project a compact Automation list row."""
    return {
        "automationId": definition.automation_id,
        "name": definition.name,
        "description": definition.description,
        "status": definition.status,
        "revision": definition.revision,
        "agentId": definition.agent_id,
        "trigger": project_automation_trigger(trigger),
        "latestRun": project_automation_run(latest_run) if latest_run is not None else None,
        "createdAtMs": definition.created_at_ms,
        "updatedAtMs": definition.updated_at_ms,
    }


def project_automation_detail(
    definition: AutomationDefinition,
    trigger: AutomationTrigger | None,
    latest_run: AutomationRun | None,
    readiness: dict[str, Any],
) -> dict[str, Any]:
    """Project complete Automation policy without scheduler internals."""
    return {
        **project_automation_summary(definition, trigger, latest_run),
        "instructions": definition.instructions,
        "outputRequirements": definition.output_requirements,
        "userId": definition.user_id,
        "workspaceRef": definition.workspace_ref,
        "contextMode": definition.context_mode,
        "modelProfileRef": definition.model_profile_ref,
        "extensionPolicy": definition.extension_policy,
        "permissionPolicy": definition.permission_policy,
        "deliveryPolicy": definition.delivery_policy,
        "concurrencyPolicy": definition.concurrency_policy,
        "missedRunPolicy": definition.missed_run_policy,
        "retryPolicy": definition.retry_policy,
        "budgetPolicy": definition.budget_policy,
        "monitorPolicy": definition.monitor_policy,
        "readiness": readiness,
        "correlationId": definition.correlation_id,
        "createdBy": definition.created_by,
    }


def project_automation_event(event: AutomationEvent) -> dict[str, Any]:
    """Project one Automation lifecycle event."""
    return {
        "eventId": event.event_id,
        "automationId": event.automation_id,
        "automationRunId": event.automation_run_id,
        "eventType": event.event_type,
        "actorId": event.actor_id,
        "correlationId": event.correlation_id,
        "payload": event.payload,
        "createdAtMs": event.created_at_ms,
    }
