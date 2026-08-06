"""Goal and TaskFlow Actions backed by Node-owned durable facts."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import NoReturn, cast

from openppx.actions import (
    ActionContext,
    ActionError,
    ActionFailure,
    ActionRegistry,
    ActionSpec,
    SlashCommandArgumentSpec,
    SlashCommandSpec,
    SlashInvocationContext,
)
from openppx.actions.models import ActionScope
from openppx.config import ConfigError
from openppx.runtime.goal_store import (
    Goal,
    GoalActiveExistsError,
    GoalConflictError,
    GoalNotFoundError,
    GoalStateError,
    GoalStore,
    GoalStoreError,
    GoalStatus,
    TaskFlow,
)
from openppx.runtime.node_runtime import NodeRuntimeSupervisor
from openppx.runtime.node_runtime import RunNotActiveError, RunNotFoundError

from .input_models import (
    GoalCommandInput,
    GoalCompleteInput,
    GoalCreateInput,
    GoalHistoryInput,
    GoalIdentityInput,
    GoalListInput,
    GoalTransitionInput,
    GoalUpdateInput,
    TaskFlowAdvanceInput,
    TaskFlowBindTaskInput,
    TaskFlowFinishInput,
    TaskFlowIdentityInput,
    TaskFlowListInput,
    TaskFlowUpdateInput,
)
from .projections import project_goal_detail, project_goal_event, project_goal_summary, project_task_flow


def register_goal_actions(
    registry: ActionRegistry,
    store: GoalStore,
    config_repository,
    profile_repository,
    *,
    runtime_provider: Callable[[], NodeRuntimeSupervisor | None],
) -> None:
    """Register the complete transport-independent Goal and TaskFlow catalog."""
    registry.register(
        _spec("goal.create", "Create Goal", "Create a durable Goal for one Agent Session.", GoalCreateInput, "goal.write", "goal", mutation=True),
        lambda context, input_data: _create_goal(
            store,
            config_repository,
            profile_repository,
            runtime_provider,
            context,
            cast(GoalCreateInput, input_data),
        ),
    )
    registry.register(
        _spec("goal.read", "Read Goal", "Read one visible Goal and its active TaskFlow.", GoalIdentityInput, "goal.read", "goal"),
        lambda _context, input_data: _read_goal(store, cast(GoalIdentityInput, input_data)),
    )
    registry.register(
        _spec("goal.list", "List Goals", "List Goals visible to one principal.", GoalListInput, "goal.read", "goal"),
        lambda _context, input_data: _list_goals(store, cast(GoalListInput, input_data)),
    )
    registry.register(
        _spec("goal.update", "Update Goal", "Edit one unfinished Goal under optimistic concurrency.", GoalUpdateInput, "goal.write", "goal", mutation=True),
        lambda context, input_data: _update_goal(store, context, cast(GoalUpdateInput, input_data)),
    )
    for action_id, title, target in (
        ("goal.pause", "Pause Goal", "paused"),
        ("goal.resume", "Resume Goal", "active"),
        ("goal.cancel", "Cancel Goal", "cancelled"),
    ):
        registry.register(
            _spec(action_id, title, f"Transition one Goal to {target}.", GoalTransitionInput, "goal.write", "goal", mutation=True),
            lambda context, input_data, target=target: _transition_goal(
                store,
                runtime_provider,
                context,
                cast(GoalTransitionInput, input_data),
                target,
            ),
        )
    registry.register(
        _spec("goal.complete", "Complete Goal", "Complete one Goal using persisted evidence or user confirmation.", GoalCompleteInput, "goal.write", "goal", mutation=True),
        lambda context, input_data: _complete_goal(store, context, cast(GoalCompleteInput, input_data)),
    )
    registry.register(
        _spec("goal.history", "Goal history", "Read append-only events for one visible Goal.", GoalHistoryInput, "goal.read", "goal"),
        lambda _context, input_data: _goal_history(store, cast(GoalHistoryInput, input_data)),
    )
    registry.register(
        ActionSpec(
            action_id="goal.command",
            namespace="goal",
            title="Current Goal",
            description="Inspect or control the Goal bound to the current Session.",
            input_model=GoalCommandInput,
            scope="goal",
            required_capabilities=frozenset({"goal.write"}),
            permission="goal.write",
            risk="medium",
            operation="mutation",
            success_presentation="panel",
            projections=("cli", "slash", "desktop", "mobile"),
            slash_commands=(
                SlashCommandSpec(
                    command="/goal",
                    title="Goal",
                    description="Create, inspect, update, pause, resume, complete, cancel, or review a Goal.",
                    icon="target",
                    arg_hint="[objective|status|update ...|pause|resume|complete|cancel|history]",
                    arguments=(
                        SlashCommandArgumentSpec(
                            name="request",
                            value_type="text",
                            description="A Goal objective or Goal operation.",
                        ),
                    ),
                    order=35,
                ),
            ),
        ),
        lambda context, input_data: _goal_command(
            store,
            config_repository,
            profile_repository,
            runtime_provider,
            context,
            cast(GoalCommandInput, input_data),
        ),
        slash_input=_goal_command_slash_input,
    )
    registry.register(
        _spec("task_flow.read", "Read TaskFlow", "Read one Goal plan and its execution references.", TaskFlowIdentityInput, "flow.read", "flow"),
        lambda _context, input_data: _read_flow(store, cast(TaskFlowIdentityInput, input_data)),
    )
    registry.register(
        _spec("task_flow.list", "List TaskFlows", "List plans belonging to one visible Goal.", TaskFlowListInput, "flow.read", "flow"),
        lambda _context, input_data: _list_flows(store, cast(TaskFlowListInput, input_data)),
    )
    registry.register(
        _spec("task_flow.update", "Update TaskFlow", "Replace a Goal plan after validating step dependencies.", TaskFlowUpdateInput, "flow.write", "flow", mutation=True),
        lambda context, input_data: _update_flow(store, context, cast(TaskFlowUpdateInput, input_data)),
    )
    registry.register(
        _spec("task_flow.advance", "Advance TaskFlow", "Advance one plan step after dependency checks.", TaskFlowAdvanceInput, "flow.write", "flow", mutation=True),
        lambda context, input_data: _advance_flow(store, context, cast(TaskFlowAdvanceInput, input_data)),
    )
    registry.register(
        _spec("task_flow.bind_task", "Bind Task to TaskFlow", "Reference an existing durable TaskRun from one plan step.", TaskFlowBindTaskInput, "flow.write", "flow", mutation=True),
        lambda context, input_data: _bind_task(store, context, cast(TaskFlowBindTaskInput, input_data)),
    )
    registry.register(
        _spec("task_flow.finish", "Finish TaskFlow", "Finish a plan after all declared steps complete.", TaskFlowFinishInput, "flow.write", "flow", mutation=True),
        lambda context, input_data: _finish_flow(store, context, cast(TaskFlowFinishInput, input_data)),
    )


def _spec(
    action_id: str,
    title: str,
    description: str,
    input_model,
    permission: str,
    scope: ActionScope,
    *,
    mutation: bool = False,
) -> ActionSpec:
    """Build consistent Goal-domain Action metadata."""
    return ActionSpec(
        action_id=action_id,
        namespace=action_id.split(".", 1)[0],
        title=title,
        description=description,
        input_model=input_model,
        scope=scope,
        required_capabilities=frozenset({permission}),
        permission=permission,
        risk="medium" if mutation else "low",
        operation="mutation" if mutation else "read",
        success_presentation="panel" if scope in {"goal", "flow"} else "inline",
        projections=("cli", "desktop", "mobile"),
    )


def _create_goal(
    store: GoalStore,
    config_repository,
    profile_repository,
    runtime_provider: Callable[[], NodeRuntimeSupervisor | None],
    context: ActionContext,
    input_data: GoalCreateInput,
) -> dict[str, object]:
    """Create a Goal with immutable Config and extension provenance."""
    try:
        agent = config_repository.read_agent(input_data.agent_id)
        workspace_ref = input_data.workspace_ref or agent.document.spec.workspace
        default_profile = agent.document.spec.model_policy.default_profile
        model_revision = ""
        if default_profile:
            model_revision = profile_repository.read_profile(default_profile).revision
        supervisor = runtime_provider()
        extension_revision = ""
        if supervisor is not None:
            extension_revision = supervisor.assembler.extension_snapshot_for_agent(input_data.agent_id).revision
        permission_revision = hashlib.sha256(
            f"{agent.revision}|{'|'.join(sorted(context.permissions))}".encode("utf-8")
        ).hexdigest()
        goal, flow = store.create_goal(
            session_id=input_data.session_id,
            agent_id=input_data.agent_id,
            user_id=input_data.user_id,
            objective=input_data.objective,
            completion_criteria=input_data.completion_criteria,
            constraints=input_data.constraints,
            workspace_ref=workspace_ref,
            budget_policy=input_data.budget_policy,
            permission_revision=permission_revision,
            model_profile_revision=model_revision,
            extension_snapshot_digest=extension_revision,
            idempotency_key=context.request_id,
            created_by=context.actor_id,
            correlation_id=context.correlation_id,
        )
        return project_goal_detail(goal, flow)
    except ConfigError as exc:
        raise ActionFailure(ActionError("configuration_not_ready", "The Agent configuration is not ready for a Goal.")) from exc
    except GoalStoreError as exc:
        _raise_goal_failure(exc)


def _read_goal(store: GoalStore, input_data: GoalIdentityInput) -> dict[str, object]:
    goal = _owned_goal(store, input_data.goal_id, input_data.user_id)
    return project_goal_detail(goal, store.flow_for_goal(goal.goal_id))


def _list_goals(store: GoalStore, input_data: GoalListInput) -> dict[str, object]:
    try:
        goals = store.list_goals(
            session_id=input_data.session_id,
            user_id=input_data.user_id,
            statuses=input_data.statuses,
            limit=input_data.limit,
        )
        return {"items": [project_goal_summary(goal) for goal in goals]}
    except GoalStoreError as exc:
        _raise_goal_failure(exc)


def _update_goal(store: GoalStore, context: ActionContext, input_data: GoalUpdateInput) -> dict[str, object]:
    _owned_goal(store, input_data.goal_id, input_data.user_id)
    try:
        goal = store.update_goal(
            input_data.goal_id,
            objective=input_data.objective,
            completion_criteria=input_data.completion_criteria,
            constraints=input_data.constraints,
            budget_policy=input_data.budget_policy,
            expected_revision=input_data.expected_revision,
            actor_id=context.actor_id,
            correlation_id=context.correlation_id,
        )
        return project_goal_detail(goal, store.flow_for_goal(goal.goal_id))
    except GoalStoreError as exc:
        _raise_goal_failure(exc)


def _transition_goal(
    store: GoalStore,
    runtime_provider: Callable[[], NodeRuntimeSupervisor | None],
    context: ActionContext,
    input_data: GoalTransitionInput,
    target: str,
) -> dict[str, object]:
    """Transition Goal facts and cooperatively stop an active ADK Run."""
    _owned_goal(store, input_data.goal_id, input_data.user_id)
    try:
        goal = store.transition_goal(
            input_data.goal_id,
            status=cast(GoalStatus, target),
            expected_revision=input_data.expected_revision,
            actor_id=context.actor_id,
            reason=input_data.reason,
            correlation_id=context.correlation_id,
        )
        if target in {"paused", "cancelled"}:
            _stop_current_goal_run(store, runtime_provider(), goal.goal_id)
        return project_goal_detail(goal, store.flow_for_goal(goal.goal_id))
    except GoalStoreError as exc:
        _raise_goal_failure(exc)


def _complete_goal(store: GoalStore, context: ActionContext, input_data: GoalCompleteInput) -> dict[str, object]:
    _owned_goal(store, input_data.goal_id, input_data.user_id)
    try:
        goal = store.transition_goal(
            input_data.goal_id,
            status="completed",
            expected_revision=input_data.expected_revision,
            actor_id=context.actor_id,
            completion_evidence=[item.model_dump(mode="json", by_alias=True, exclude_none=True) for item in input_data.completion_evidence],
            user_confirmed=input_data.user_confirmed,
            reason=input_data.reason,
            correlation_id=context.correlation_id,
        )
        return project_goal_detail(goal, store.flow_for_goal(goal.goal_id))
    except GoalStoreError as exc:
        _raise_goal_failure(exc)


def _goal_history(store: GoalStore, input_data: GoalHistoryInput) -> dict[str, object]:
    _owned_goal(store, input_data.goal_id, input_data.user_id)
    return {"items": [project_goal_event(event) for event in store.list_events(input_data.goal_id, limit=input_data.limit)]}


def _read_flow(store: GoalStore, input_data: TaskFlowIdentityInput) -> dict[str, object]:
    flow = _owned_flow(store, input_data.flow_id, input_data.user_id)
    return project_task_flow(flow)


def _list_flows(store: GoalStore, input_data: TaskFlowListInput) -> dict[str, object]:
    _owned_goal(store, input_data.goal_id, input_data.user_id)
    return {"items": [project_task_flow(flow) for flow in store.list_flows(input_data.goal_id, limit=input_data.limit)]}


def _update_flow(store: GoalStore, context: ActionContext, input_data: TaskFlowUpdateInput) -> dict[str, object]:
    _owned_flow(store, input_data.flow_id, input_data.user_id)
    try:
        flow = store.update_flow(
            input_data.flow_id,
            steps=[step.model_dump(mode="json", by_alias=True) for step in input_data.steps],
            expected_revision=input_data.expected_revision,
            actor_id=context.actor_id,
            correlation_id=context.correlation_id,
        )
        return project_task_flow(flow)
    except GoalStoreError as exc:
        _raise_goal_failure(exc)


def _advance_flow(store: GoalStore, context: ActionContext, input_data: TaskFlowAdvanceInput) -> dict[str, object]:
    _owned_flow(store, input_data.flow_id, input_data.user_id)
    try:
        flow = store.advance_flow_step(
            input_data.flow_id,
            step_id=input_data.step_id,
            status=input_data.status,
            expected_revision=input_data.expected_revision,
            actor_id=context.actor_id,
            correlation_id=context.correlation_id,
        )
        return project_task_flow(flow)
    except GoalStoreError as exc:
        _raise_goal_failure(exc)


def _bind_task(store: GoalStore, context: ActionContext, input_data: TaskFlowBindTaskInput) -> dict[str, object]:
    _owned_flow(store, input_data.flow_id, input_data.user_id)
    try:
        flow = store.bind_task(
            input_data.flow_id,
            step_id=input_data.step_id,
            task_id=input_data.task_id,
            expected_revision=input_data.expected_revision,
            actor_id=context.actor_id,
            correlation_id=context.correlation_id,
        )
        return project_task_flow(flow)
    except GoalStoreError as exc:
        _raise_goal_failure(exc)


def _finish_flow(store: GoalStore, context: ActionContext, input_data: TaskFlowFinishInput) -> dict[str, object]:
    _owned_flow(store, input_data.flow_id, input_data.user_id)
    try:
        flow = store.finish_flow(
            input_data.flow_id,
            expected_revision=input_data.expected_revision,
            actor_id=context.actor_id,
            correlation_id=context.correlation_id,
        )
        return project_task_flow(flow)
    except GoalStoreError as exc:
        _raise_goal_failure(exc)


def _goal_command_slash_input(
    _command: SlashCommandSpec,
    args: str,
    context: SlashInvocationContext,
) -> dict[str, object]:
    """Map `/goal` command text to a typed Goal operation."""
    if context.agent_id is None or context.session_id is None:
        raise ActionFailure(ActionError("session_context_required", "The /goal command requires an active Session."))
    value = args.strip()
    if not value:
        operation, text = "status", ""
    else:
        head, separator, remainder = value.partition(" ")
        if head in {"status", "pause", "resume", "complete", "cancel", "history"}:
            if separator and remainder.strip():
                raise ActionFailure(ActionError("command_arguments_invalid", f"/goal {head} does not accept more text."))
            operation, text = head, ""
        elif head == "update":
            if not separator or not remainder.strip():
                raise ActionFailure(ActionError("command_usage_required", "Usage: /goal update <text>"))
            operation, text = "update", remainder.strip()
        elif head == "create":
            if not separator or not remainder.strip():
                raise ActionFailure(ActionError("command_usage_required", "Usage: /goal create <objective>"))
            operation, text = "create", remainder.strip()
        else:
            operation, text = "create", value
    return {
        "userId": context.user_id,
        "agentId": context.agent_id,
        "sessionId": context.session_id,
        "operation": operation,
        "text": text,
    }


def _goal_command(
    store: GoalStore,
    config_repository,
    profile_repository,
    runtime_provider: Callable[[], NodeRuntimeSupervisor | None],
    context: ActionContext,
    input_data: GoalCommandInput,
) -> dict[str, object]:
    """Execute `/goal` through the same Goal domain functions as formal Actions."""
    if input_data.operation == "create":
        created = _create_goal(
            store,
            config_repository,
            profile_repository,
            runtime_provider,
            context,
            GoalCreateInput(
                userId=input_data.user_id,
                agentId=input_data.agent_id,
                sessionId=input_data.session_id,
                objective=input_data.text,
            ),
        )
        return {**created, "startAgentTurn": {"text": input_data.text, "goalId": created["goalId"]}}
    if input_data.operation == "history":
        return _list_goals(
            store,
            GoalListInput(userId=input_data.user_id, sessionId=input_data.session_id, limit=20),
        )
    current = store.current_goal(input_data.session_id)
    if current is None or current.user_id != input_data.user_id:
        if input_data.operation == "status":
            return {"current": None}
        raise ActionFailure(ActionError("goal_not_found", "This Session has no unfinished Goal."))
    if input_data.operation == "status":
        return {"current": project_goal_detail(current, store.flow_for_goal(current.goal_id))}
    if input_data.operation == "update":
        return _update_goal(
            store,
            context,
            GoalUpdateInput(
                goalId=current.goal_id,
                userId=input_data.user_id,
                expectedRevision=current.revision,
                objective=input_data.text,
            ),
        )
    if input_data.operation == "complete":
        return _complete_goal(
            store,
            context,
            GoalCompleteInput(
                goalId=current.goal_id,
                userId=input_data.user_id,
                expectedRevision=current.revision,
                userConfirmed=True,
            ),
        )
    target = {"pause": "paused", "resume": "active", "cancel": "cancelled"}[input_data.operation]
    result = _transition_goal(
        store,
        runtime_provider,
        context,
        GoalTransitionInput(
            goalId=current.goal_id,
            userId=input_data.user_id,
            expectedRevision=current.revision,
        ),
        target,
    )
    if target == "active":
        result["startAgentTurn"] = {
            "text": f"Resume the active Goal and continue working toward: {current.objective}",
            "goalId": current.goal_id,
        }
    return result


def _stop_current_goal_run(
    store: GoalStore,
    supervisor: NodeRuntimeSupervisor | None,
    goal_id: str,
) -> None:
    """Request cancellation of the Run referenced by the Goal's active Flow."""
    if supervisor is None:
        return
    flow = store.flow_for_goal(goal_id)
    run_id = str((flow.recovery_state if flow is not None else {}).get("currentRunId") or "").strip()
    if not run_id:
        return
    try:
        supervisor.stop_run(run_id)
    except (RunNotFoundError, RunNotActiveError):
        return


def _owned_goal(store: GoalStore, goal_id: str, user_id: str) -> Goal:
    """Return a Goal only when it belongs to the requesting principal."""
    goal = store.get_goal(goal_id)
    if goal is None:
        raise ActionFailure(ActionError("goal_not_found", "The requested Goal was not found."))
    if goal.user_id != user_id:
        raise ActionFailure(ActionError("permission_denied", "The caller cannot access this Goal."))
    return goal


def _owned_flow(store: GoalStore, flow_id: str, user_id: str) -> TaskFlow:
    """Return a TaskFlow only through its caller-visible Goal."""
    flow = store.get_flow(flow_id)
    if flow is None:
        raise ActionFailure(ActionError("flow_not_found", "The requested TaskFlow was not found."))
    _owned_goal(store, flow.goal_id, user_id)
    return flow


def _raise_goal_failure(exc: GoalStoreError) -> NoReturn:
    """Translate deterministic store failures to stable Action errors."""
    if isinstance(exc, GoalNotFoundError):
        code = "goal_not_found"
    elif isinstance(exc, GoalActiveExistsError):
        code = "goal_active_exists"
    elif isinstance(exc, GoalConflictError):
        code = "revision_conflict"
    elif isinstance(exc, GoalStateError):
        code = "goal_state_invalid"
    else:
        code = "goal_store_error"
    raise ActionFailure(ActionError(code, str(exc))) from exc


__all__ = ["register_goal_actions"]
