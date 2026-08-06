"""Google ADK-native tools for durable Goal and TaskFlow facts.

These tools mutate the same Node-owned GoalStore used by Control Plane Actions.
They intentionally manage intent and plan facts only; Google ADK Runner and the
existing Task runtime remain the execution engines and evidence sources.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from openppx.runtime.goal_store import (
    FLOW_STEP_STATUSES,
    Goal,
    GoalStateError,
    GoalStore,
    GoalStoreError,
    TaskFlow,
)


@dataclass(frozen=True, slots=True)
class GoalToolRuntimeSnapshot:
    """Immutable runtime provenance captured when an ADK Agent is assembled."""

    agent_id: str
    workspace_ref: str
    permission_revision: str
    model_profile_revision: str
    extension_snapshot_digest: str


def _context_attr(tool_context: Any | None, name: str) -> str:
    """Return one string attribute from an ADK ToolContext-like object."""
    value = getattr(tool_context, name, None)
    return value if isinstance(value, str) else ""


def _session_id(tool_context: Any | None) -> str:
    """Return the current ADK Session identity."""
    session = getattr(tool_context, "session", None)
    value = getattr(session, "id", None)
    return value if isinstance(value, str) else ""


def _identity(tool_context: Any | None) -> tuple[str, str, str]:
    """Return user, Session, and invocation identities for one tool call."""
    user_id = _context_attr(tool_context, "user_id")
    session_id = _session_id(tool_context)
    invocation_id = _context_attr(tool_context, "invocation_id")
    if not user_id or not session_id:
        raise GoalStateError("Goal tools require an ADK user_id and session")
    return user_id, session_id, invocation_id


def _json_result(payload: dict[str, Any]) -> str:
    """Serialize one structured tool result without leaking storage encoding."""
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _flow_payload(flow: TaskFlow | None) -> dict[str, Any] | None:
    """Project one TaskFlow for an ADK tool result."""
    if flow is None:
        return None
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
    }


def _goal_payload(goal: Goal, flow: TaskFlow | None) -> dict[str, Any]:
    """Project one Goal and its current TaskFlow for an ADK tool result."""
    return {
        "goalId": goal.goal_id,
        "sessionId": goal.session_id,
        "agentId": goal.agent_id,
        "objective": goal.objective,
        "completionCriteria": goal.completion_criteria,
        "constraints": goal.constraints,
        "status": goal.status,
        "revision": goal.revision,
        "budgetPolicy": goal.budget_policy,
        "budgetState": goal.budget_state,
        "completionEvidence": goal.completion_evidence,
        "correlationId": goal.correlation_id,
        "flow": _flow_payload(flow),
    }


def _owned_goal(store: GoalStore, goal_id: str, *, user_id: str, session_id: str) -> Goal:
    """Resolve one caller-owned Goal without allowing cross-Session mutation."""
    goal = store.get_goal(goal_id) if goal_id.strip() else store.current_goal(session_id)
    if goal is None:
        raise GoalStateError("no unfinished Goal exists for this Session")
    if goal.user_id != user_id or goal.session_id != session_id:
        raise GoalStateError("the current ADK principal cannot access this Goal")
    return goal


def _completion_evidence_is_persisted(flow: TaskFlow, evidence: list[dict[str, Any]]) -> bool:
    """Return whether every claimed evidence reference is already persisted."""
    task_refs = {str(item.get("taskId") or "") for item in flow.task_run_refs}
    artifact_refs = {
        str(item.get("artifactId") or item.get("ref") or "") for item in flow.artifact_refs
    }
    recovery_refs = {
        str(value)
        for key, value in flow.recovery_state.items()
        if key in {"runId", "invocationId", "checkpointRef"} and value
    }
    for item in evidence:
        evidence_type = str(item.get("type") or "").strip()
        ref = str(item.get("ref") or "").strip()
        if not evidence_type or not ref:
            return False
        if evidence_type in {"task", "task_run"} and ref in task_refs:
            continue
        if evidence_type == "artifact" and ref in artifact_refs:
            continue
        if evidence_type in {"run", "checkpoint"} and ref in recovery_refs:
            continue
        return False
    return bool(evidence)


def build_goal_tools(
    store: GoalStore,
    snapshot: GoalToolRuntimeSnapshot,
) -> tuple[Any, ...]:
    """Build typed ADK function tools bound to one immutable Agent snapshot."""

    def get_goal(goal_id: str = "", tool_context: Any | None = None) -> str:
        """Read the durable Goal and TaskFlow for the current ADK Session."""
        try:
            user_id, session_id, _invocation_id = _identity(tool_context)
            goal = _owned_goal(store, goal_id, user_id=user_id, session_id=session_id)
            return _json_result({"ok": True, "goal": _goal_payload(goal, store.flow_for_goal(goal.goal_id))})
        except GoalStoreError as exc:
            return _json_result({"ok": False, "error": str(exc)})

    def create_goal(
        objective: str,
        completion_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        tool_context: Any | None = None,
    ) -> str:
        """Create a Goal only when the user explicitly requested durable goal tracking."""
        try:
            user_id, session_id, invocation_id = _identity(tool_context)
            call_id = _context_attr(tool_context, "function_call_id")
            idempotency_key = hashlib.sha256(
                f"{user_id}:{session_id}:{invocation_id}:{call_id}:create_goal:{objective}".encode()
            ).hexdigest()
            goal, flow = store.create_goal(
                session_id=session_id,
                agent_id=snapshot.agent_id,
                user_id=user_id,
                objective=objective,
                completion_criteria=completion_criteria,
                constraints=constraints,
                workspace_ref=snapshot.workspace_ref,
                permission_revision=snapshot.permission_revision,
                model_profile_revision=snapshot.model_profile_revision,
                extension_snapshot_digest=snapshot.extension_snapshot_digest,
                idempotency_key=idempotency_key,
                created_by=user_id,
                correlation_id=invocation_id,
            )
            return _json_result({"ok": True, "goal": _goal_payload(goal, flow)})
        except GoalStoreError as exc:
            return _json_result({"ok": False, "error": str(exc)})

    def update_goal(
        goal_id: str = "",
        objective: str = "",
        completion_criteria: list[str] | None = None,
        constraints: list[str] | None = None,
        status: str = "",
        reason: str = "",
        tool_context: Any | None = None,
    ) -> str:
        """Update policy or non-terminal status for the current durable Goal."""
        try:
            user_id, session_id, invocation_id = _identity(tool_context)
            goal = _owned_goal(store, goal_id, user_id=user_id, session_id=session_id)
            if status:
                if status not in {"active", "waiting", "paused", "blocked", "cancelled"}:
                    raise GoalStateError("use complete_goal for completion; unsupported Goal status")
                goal = store.transition_goal(
                    goal.goal_id,
                    status=status,  # type: ignore[arg-type]
                    expected_revision=goal.revision,
                    actor_id=user_id,
                    reason=reason,
                    correlation_id=invocation_id,
                )
            else:
                goal = store.update_goal(
                    goal.goal_id,
                    objective=objective or None,
                    completion_criteria=completion_criteria,
                    constraints=constraints,
                    expected_revision=goal.revision,
                    actor_id=user_id,
                    correlation_id=invocation_id,
                )
            return _json_result({"ok": True, "goal": _goal_payload(goal, store.flow_for_goal(goal.goal_id))})
        except GoalStoreError as exc:
            return _json_result({"ok": False, "error": str(exc)})

    def update_task_flow(
        steps: list[dict[str, Any]],
        flow_id: str = "",
        tool_context: Any | None = None,
    ) -> str:
        """Replace the current Goal plan; this does not execute any step."""
        try:
            user_id, session_id, invocation_id = _identity(tool_context)
            goal = _owned_goal(store, "", user_id=user_id, session_id=session_id)
            flow = store.get_flow(flow_id) if flow_id.strip() else store.flow_for_goal(goal.goal_id)
            if flow is None or flow.goal_id != goal.goal_id:
                raise GoalStateError("the requested TaskFlow is not owned by the current Goal")
            updated = store.update_flow(
                flow.flow_id,
                steps=steps,
                expected_revision=flow.revision,
                actor_id=user_id,
                correlation_id=invocation_id,
            )
            return _json_result({"ok": True, "flow": _flow_payload(updated)})
        except GoalStoreError as exc:
            return _json_result({"ok": False, "error": str(exc)})

    def advance_task_flow_step(
        step_id: str,
        status: str,
        flow_id: str = "",
        tool_context: Any | None = None,
    ) -> str:
        """Advance one TaskFlow step after dependency validation."""
        try:
            user_id, session_id, invocation_id = _identity(tool_context)
            goal = _owned_goal(store, "", user_id=user_id, session_id=session_id)
            flow = store.get_flow(flow_id) if flow_id.strip() else store.flow_for_goal(goal.goal_id)
            if flow is None or flow.goal_id != goal.goal_id:
                raise GoalStateError("the requested TaskFlow is not owned by the current Goal")
            if status not in FLOW_STEP_STATUSES:
                raise GoalStateError("unsupported TaskFlow step status")
            updated = store.advance_flow_step(
                flow.flow_id,
                step_id=step_id,
                status=status,
                expected_revision=flow.revision,
                actor_id=user_id,
                correlation_id=invocation_id,
            )
            return _json_result({"ok": True, "flow": _flow_payload(updated)})
        except GoalStoreError as exc:
            return _json_result({"ok": False, "error": str(exc)})

    def bind_task_flow(
        step_id: str,
        task_id: str,
        flow_id: str = "",
        tool_context: Any | None = None,
    ) -> str:
        """Bind an existing TaskRun reference to a TaskFlow step."""
        try:
            user_id, session_id, invocation_id = _identity(tool_context)
            goal = _owned_goal(store, "", user_id=user_id, session_id=session_id)
            flow = store.get_flow(flow_id) if flow_id.strip() else store.flow_for_goal(goal.goal_id)
            if flow is None or flow.goal_id != goal.goal_id:
                raise GoalStateError("the requested TaskFlow is not owned by the current Goal")
            updated = store.bind_task(
                flow.flow_id,
                step_id=step_id,
                task_id=task_id,
                expected_revision=flow.revision,
                actor_id=user_id,
                correlation_id=invocation_id,
            )
            return _json_result({"ok": True, "flow": _flow_payload(updated)})
        except GoalStoreError as exc:
            return _json_result({"ok": False, "error": str(exc)})

    def complete_goal(
        evidence: list[dict[str, Any]],
        goal_id: str = "",
        tool_context: Any | None = None,
    ) -> str:
        """Complete the current Goal only with persisted execution evidence."""
        try:
            user_id, session_id, invocation_id = _identity(tool_context)
            goal = _owned_goal(store, goal_id, user_id=user_id, session_id=session_id)
            flow = store.flow_for_goal(goal.goal_id)
            if flow is None or not _completion_evidence_is_persisted(flow, evidence):
                raise GoalStateError("Goal completion evidence is not present in persisted TaskFlow facts")
            completed = store.transition_goal(
                goal.goal_id,
                status="completed",
                expected_revision=goal.revision,
                actor_id=user_id,
                completion_evidence=evidence,
                correlation_id=invocation_id,
            )
            return _json_result({"ok": True, "goal": _goal_payload(completed, flow)})
        except GoalStoreError as exc:
            return _json_result({"ok": False, "error": str(exc)})

    return (
        get_goal,
        create_goal,
        update_goal,
        update_task_flow,
        advance_task_flow_step,
        bind_task_flow,
        complete_goal,
    )


__all__ = ["GoalToolRuntimeSnapshot", "build_goal_tools"]
