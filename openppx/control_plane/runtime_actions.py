"""Session and Run Actions backed by the Node Runtime Supervisor."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import cast

from openppx.actions import ActionError, ActionFailure, ActionRegistry, ActionSpec
from openppx.runtime.node_runtime import (
    NodeRuntimeSupervisor,
    RunNotActiveError,
    RunNotFoundError,
    RuntimeSupervisorError,
)

from .input_models import RunStopInput, SessionNewInput


def register_runtime_actions(
    registry: ActionRegistry,
    supervisor: NodeRuntimeSupervisor,
) -> None:
    """Register the first shared Session and Run control Actions."""
    registry.register(
        ActionSpec(
            action_id="session.new",
            namespace="session",
            title="New session",
            description="Create a new Agent conversation Session.",
            input_model=SessionNewInput,
            scope="agent",
            required_capabilities=frozenset({"session.write"}),
            permission="session.write",
            projections=("cli", "slash", "desktop", "mobile"),
        ),
        lambda _context, input_data: _new_session(
            supervisor,
            cast(SessionNewInput, input_data),
        ),
    )
    registry.register(
        ActionSpec(
            action_id="run.stop",
            namespace="run",
            title="Stop run",
            description="Request cooperative cancellation of one active Run.",
            input_model=RunStopInput,
            scope="run",
            required_capabilities=frozenset({"run.control"}),
            permission="run.control",
            risk="medium",
            projections=("cli", "slash", "desktop", "mobile"),
        ),
        lambda _context, input_data: _stop_run(
            supervisor,
            cast(RunStopInput, input_data),
        ),
    )


def _new_session(
    supervisor: NodeRuntimeSupervisor,
    input_data: SessionNewInput,
) -> dict[str, object]:
    try:
        session = supervisor.create_session_sync(
            input_data.agent_id,
            user_id=input_data.user_id,
        )
    except RuntimeSupervisorError as exc:
        raise ActionFailure(
            ActionError("runtime_unavailable", "The Node runtime is not available.")
        ) from exc
    last_update = getattr(session, "last_update_time", None)
    if isinstance(last_update, (int, float)):
        updated_at = datetime.fromtimestamp(last_update, tz=timezone.utc).isoformat()
    else:
        updated_at = datetime.now(timezone.utc).isoformat()
    return {
        "session": {
            "id": str(getattr(session, "id", "")),
            "agentId": input_data.agent_id,
            "subjectPrincipalId": input_data.user_id,
            "title": "New chat",
            "updatedAt": updated_at,
            "lastMessagePreview": "",
            "archived": False,
        }
    }


def _stop_run(
    supervisor: NodeRuntimeSupervisor,
    input_data: RunStopInput,
) -> dict[str, object]:
    try:
        run = supervisor.stop_run(input_data.run_id)
    except RunNotFoundError as exc:
        raise ActionFailure(
            ActionError("run_not_found", "The requested Run was not found.")
        ) from exc
    except RunNotActiveError as exc:
        raise ActionFailure(
            ActionError("run_not_active", "The requested Run is no longer active.")
        ) from exc
    return {
        "run": {
            "id": run.run_id,
            "agentId": run.agent_id,
            "sessionId": run.session_id,
            "snapshotRevision": run.snapshot_revision,
            "startedAt": run.started_at,
            "state": run.state,
        }
    }


__all__ = ["register_runtime_actions"]
