"""ADK-native Automation application service and Cron schedule adapter."""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openppx.config import ConfigError, FilesystemConfigRepository
from openppx.modeling import ModelProfileRepository
from openppx.product import PRODUCT
from openppx.runtime.cron_service import (
    CronJob,
    CronSchedule,
    CronService,
    missed_schedule_occurrences,
)
from openppx.runtime.node_runtime import NodeRuntimeSupervisor
from openppx.runtime.task_store import TaskDeliveryStore, TaskEventStore, TaskStore

from .store import (
    AutomationDefinition,
    AutomationNotFoundError,
    AutomationRun,
    AutomationStateError,
    AutomationStore,
    AutomationTrigger,
)


AUTOMATION_TASK_CAPABILITIES: dict[str, bool] = {
    "status": True,
    "cancel": False,
    "interrupt": False,
    "output": True,
    "artifact": True,
    "rejoin": True,
    "pause": False,
    "checkpoint": True,
}


@dataclass(frozen=True, slots=True)
class AutomationTemplate:
    """One reviewed creation starting point that is never auto-enabled."""

    template_id: str
    name: str
    description: str
    instructions: str
    output_requirements: tuple[str, ...]
    recommended_schedule: dict[str, Any]
    required_extensions: tuple[str, ...]
    delivery_hint: str
    behavior: str = "task"
    provenance: str = "openppx"
    version: int = 1


DEFAULT_AUTOMATION_TEMPLATES: tuple[AutomationTemplate, ...] = (
    AutomationTemplate(
        "morning-brief", "Morning brief", "Summarize the day before it starts.",
        "Summarize today's calendar, important unread email, and the top priorities. Cite sources and call out missing access.",
        ("Concise daily brief", "Source links", "Explicit access gaps"),
        {"kind": "cron", "cronExpr": "0 8 * * 1-5", "timezone": ""},
        ("Google Calendar or Outlook", "Gmail, Outlook, or IMAP"), "session",
    ),
    AutomationTemplate(
        "github-digest", "GitHub digest", "Review repository activity on a schedule.",
        "Summarize merged pull requests, important commits, failing checks, and issues that need attention.",
        ("Repository digest", "Action items", "Links to source items"),
        {"kind": "cron", "cronExpr": "0 9 * * 1", "timezone": ""}, ("GitHub",), "session",
    ),
    AutomationTemplate(
        "morning-news", "Morning news briefing", "Create a short evidence-linked news digest.",
        "Collect the most important technology and world news since the previous run and create a five-bullet digest with links.",
        ("Five bullets", "Links", "Timestamp"),
        {"kind": "cron", "cronExpr": "0 8 * * *", "timezone": ""}, ("Web search",), "artifact",
    ),
    AutomationTemplate(
        "inbox-digest", "Inbox digest", "Review unread mail without mixing it with system heartbeat.",
        "Summarize unread email, group it by urgency, and identify messages that need a reply.",
        ("Urgency groups", "Reply candidates", "Message references"),
        {"kind": "cron", "cronExpr": "0 9 * * 1-5", "timezone": ""}, ("Gmail, Outlook, or IMAP",), "session",
    ),
    AutomationTemplate(
        "folder-cleanup", "Folder cleanup", "Organize a selected folder with an auditable plan.",
        "Review recent files in the selected folder, propose a safe organization plan, and only apply reversible moves allowed by the configured permissions.",
        ("Move plan", "Changed files", "Skipped files"),
        {"kind": "cron", "cronExpr": "0 17 * * 5", "timezone": ""}, ("Filesystem workspace",), "artifact",
    ),
    AutomationTemplate(
        "follow-up-monitor", "Follow-up monitor", "Monitor for changes and stay quiet when nothing changes.",
        "Review the configured sources for new follow-ups since the saved cursor. Notify only when action is required and update the observation cursor.",
        ("Change summary", "Action required", "Observation cursor"),
        {"kind": "every", "everySeconds": 3600, "timezone": ""}, ("Configured source App or MCP",), "session", "monitor",
    ),
)


class AutomationService:
    """Own Automation facts and execute every run through the normal ADK runtime."""

    def __init__(
        self,
        *,
        node_root: Path,
        store: AutomationStore,
        config_repository: FilesystemConfigRepository,
        profile_repository: ModelProfileRepository,
        supervisor: NodeRuntimeSupervisor,
        cron: CronService,
        task_store: TaskStore,
        operations_runtime: Any,
    ) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.store = store
        self.config_repository = config_repository
        self.profile_repository = profile_repository
        self.supervisor = supervisor
        self.cron = cron
        self.task_store = task_store
        self.task_events = TaskEventStore(db_path=task_store.db_path)
        self.delivery_store = TaskDeliveryStore(db_path=task_store.db_path)
        self.operations_runtime = operations_runtime

    def list_definitions(self, *, user_id: str, statuses: list[str], limit: int) -> list[AutomationDefinition]:
        """Return only user-created Automations, never scheduler infrastructure."""
        return self.store.list_definitions(user_id=user_id, statuses=statuses or None, limit=limit)

    def read_definition(self, automation_id: str, *, user_id: str) -> AutomationDefinition:
        definition = self.store.read_definition(automation_id)
        self._require_owner(definition, user_id)
        return definition

    def create_definition(self, *, actor_id: str, request_id: str, correlation_id: str, **data: Any) -> AutomationDefinition:
        """Validate one Automation, persist it, and derive its scheduler record."""
        agent, workspace, model_profile = self._resolve_definition_inputs(
            data["agent_id"], data["workspace_ref"], data["model_profile_ref"]
        )
        permission_policy = dict(data.get("permission_policy") or {})
        requested_permissions = permission_policy.get("permissions")
        needs_confirmation = bool(requested_permissions) or bool(data.get("extension_policy")) or bool(data.get("delivery_policy"))
        permission_policy.setdefault(
            "confirmed",
            bool(data.get("permissions_confirmed")) or not needs_confirmation,
        )
        definition, _trigger = self.store.create_definition(
            name=data["name"],
            description=data.get("description", ""),
            instructions=data["instructions"],
            output_requirements=data.get("output_requirements", []),
            agent_id=data["agent_id"],
            user_id=data["user_id"],
            workspace_ref=workspace,
            context_mode=data.get("context_mode", "isolated"),
            model_profile_ref=model_profile,
            extension_policy=data.get("extension_policy", {}),
            permission_policy=permission_policy,
            delivery_policy=data.get("delivery_policy", {}),
            concurrency_policy=data.get("concurrency_policy") or {"mode": "skip", "limit": 1},
            missed_run_policy=data.get("missed_run_policy") or {"mode": "run-latest", "maxCatchUp": 1},
            retry_policy=data.get("retry_policy") or {"maxAttempts": 1, "backoffSeconds": 30},
            budget_policy=data.get("budget_policy", {}),
            monitor_policy=data.get("monitor_policy", {}),
            schedule=data.get("schedule"),
            local_event=data.get("local_event"),
            idempotency_key=request_id,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        del agent
        return self._sync_schedule(definition)

    def update_definition(self, definition: AutomationDefinition, *, actor_id: str, correlation_id: str, **data: Any) -> AutomationDefinition:
        """Update policy and rebuild the derived schedule from the new revision."""
        agent_id = data.get("agent_id") or definition.agent_id
        workspace_ref = data.get("workspace_ref") if data.get("workspace_ref") is not None else definition.workspace_ref
        model_profile_ref = data.get("model_profile_ref") if data.get("model_profile_ref") is not None else definition.model_profile_ref
        _agent, workspace, model_profile = self._resolve_definition_inputs(agent_id, workspace_ref, model_profile_ref)
        json_fields = {
            "output_requirements": "output_requirements_json",
            "extension_policy": "extension_policy_json",
            "permission_policy": "permission_policy_json",
            "delivery_policy": "delivery_policy_json",
            "concurrency_policy": "concurrency_policy_json",
            "missed_run_policy": "missed_run_policy_json",
            "retry_policy": "retry_policy_json",
            "budget_policy": "budget_policy_json",
            "monitor_policy": "monitor_policy_json",
        }
        changes: dict[str, Any] = {
            key: data.get(key)
            for key in ("name", "description", "instructions", "context_mode")
            if data.get(key) is not None
        }
        changes.update({"agent_id": agent_id, "workspace_ref": workspace, "model_profile_ref": model_profile})
        for source, target in json_fields.items():
            if data.get(source) is not None:
                changes[target] = json.dumps(data[source], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        updated = self.store.update_definition(
            definition.automation_id,
            expected_revision=data["expected_revision"],
            actor_id=actor_id,
            correlation_id=correlation_id,
            **changes,
        )
        if "schedule" in data or "local_event" in data:
            # An explicit null removes the trigger while omission preserves it.
            self.store.replace_trigger(
                updated.automation_id,
                schedule=data.get("schedule"),
                local_event=data.get("local_event"),
            )
        return self._sync_schedule(updated)

    def transition(self, definition: AutomationDefinition, *, status: str, expected_revision: int, actor_id: str, correlation_id: str) -> AutomationDefinition:
        """Apply one explicit lifecycle transition and synchronize scheduling."""
        updated = self.store.set_status(
            definition.automation_id,
            status=status,  # type: ignore[arg-type]
            expected_revision=expected_revision,
            actor_id=actor_id,
            correlation_id=correlation_id,
        )
        return self._sync_schedule(updated)

    def permission_preview(self, definition: AutomationDefinition) -> dict[str, Any]:
        """Return a value-free standing approval preview for user confirmation."""
        policy = definition.permission_policy
        requested = policy.get("permissions") if isinstance(policy.get("permissions"), list) else []
        return {
            "automationId": definition.automation_id,
            "confirmed": bool(policy.get("confirmed")),
            "principal": f"automation:{definition.automation_id}",
            "workspaceRef": definition.workspace_ref,
            "requestedPermissions": [str(item) for item in requested],
            "extensions": definition.extension_policy,
            "delivery": definition.delivery_policy,
        }

    def revoke_permissions(self, definition: AutomationDefinition, *, actor_id: str, correlation_id: str) -> AutomationDefinition:
        import json

        policy = dict(definition.permission_policy)
        policy["confirmed"] = False
        updated = self.store.update_definition(
            definition.automation_id,
            expected_revision=definition.revision,
            actor_id=actor_id,
            correlation_id=correlation_id,
            permission_policy_json=json.dumps(policy, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        )
        if updated.status == "active":
            updated = self.store.set_status(
                updated.automation_id,
                status="blocked",
                expected_revision=updated.revision,
                actor_id=actor_id,
                correlation_id=correlation_id,
            )
        return self._sync_schedule(updated)

    def readiness(self, definition: AutomationDefinition) -> dict[str, Any]:
        """Evaluate current runtime dependencies without exposing secrets."""
        reasons: list[dict[str, str]] = []
        try:
            agent = self.config_repository.read_agent(definition.agent_id)
            node = self.config_repository.read_node().document
            if definition.agent_id not in node.spec.enabled_agents:
                reasons.append({"code": "agent_disabled", "message": "The target Agent is not enabled."})
            workspace = Path(definition.workspace_ref).expanduser()
            if not workspace.exists():
                reasons.append({"code": "workspace_missing", "message": "The configured workspace does not exist."})
            profile_id = definition.model_profile_ref or agent.document.spec.model_policy.default_profile
            if profile_id:
                self.profile_repository.read_profile(profile_id)
            self.supervisor.assembler.extension_snapshot_for_agent(definition.agent_id)
        except ConfigError:
            reasons.append({"code": "configuration_not_ready", "message": "Agent or model configuration is not ready."})
        except Exception:
            reasons.append({"code": "extension_not_ready", "message": "One or more configured extensions are not ready."})
        if not bool(definition.permission_policy.get("confirmed")):
            reasons.append({"code": "permission_confirmation_required", "message": "Background permissions require confirmation."})
        if definition.status != "active":
            reasons.append({"code": f"automation_{definition.status}", "message": f"The Automation is {definition.status}."})
        return {"ready": not reasons, "reasons": reasons}

    def run_now(
        self,
        definition: AutomationDefinition,
        *,
        actor_id: str,
        correlation_id: str,
        input_snapshot: dict[str, Any],
        request_id: str = "",
    ) -> AutomationRun:
        """Create a manual run and submit normal ADK execution without blocking the client."""
        run, created = self._prepare_run(
            definition,
            trigger_type="manual",
            occurrence_id=f"manual:{request_id or uuid.uuid4().hex}",
            started_by=actor_id,
            correlation_id=correlation_id,
            input_snapshot=input_snapshot,
        )
        if created:
            self.operations_runtime.submit(lambda: self._execute_run(run.automation_run_id))
        return run

    def trigger_local_event(
        self,
        definition: AutomationDefinition,
        *,
        actor_id: str,
        correlation_id: str,
        event_key: str,
        event_id: str,
        input_snapshot: dict[str, Any],
    ) -> AutomationRun:
        """Start one declared trusted-LAN event through the normal ADK run path."""
        trigger = self.store.trigger_for(definition.automation_id)
        if trigger is None or trigger.trigger_type != "local_event":
            raise AutomationStateError("automation does not declare a local event trigger")
        if trigger.event_key != event_key:
            raise AutomationStateError("local event key does not match the configured trigger")
        try:
            encoded = json.dumps(input_snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise AutomationStateError("local event input must be valid JSON") from exc
        if len(encoded) > 65_536:
            raise AutomationStateError("local event input exceeds 64 KiB")
        _validate_local_event_input(input_snapshot, trigger.input_schema)
        run, created = self._prepare_run(
            definition,
            trigger_type="local_event",
            occurrence_id=f"event:{event_key}:{event_id}",
            started_by=actor_id,
            correlation_id=correlation_id,
            input_snapshot={"eventKey": event_key, "payload": input_snapshot},
        )
        if created:
            self.operations_runtime.submit(lambda: self._execute_run(run.automation_run_id))
        return run

    async def run_scheduled(self, job: CronJob) -> str:
        """Execute one derived Cron occurrence through the same Automation run path."""
        definition = self.store.read_definition(job.payload.source_id)
        occurrence = job.state.next_run_at_ms or int(time.time() * 1000)
        now_ms = int(time.time() * 1000)
        policy = definition.missed_run_policy
        mode = str(_policy_value(policy, "mode", "mode", default="run-latest"))
        max_catch_up = int(_policy_value(policy, "maxCatchUp", "max_catch_up", default=1))
        grace_ms = max(0, int(_policy_value(policy, "graceSeconds", "grace_seconds", default=30))) * 1_000
        missed = now_ms > occurrence + grace_ms
        occurrences = [occurrence]
        if missed and mode == "bounded-catch-up":
            occurrences = missed_schedule_occurrences(
                job.schedule,
                first_due_ms=occurrence,
                now_ms=now_ms,
                limit=max_catch_up,
            )
        elif missed and mode == "skip":
            run, _created = self._prepare_run(
                definition,
                trigger_type="schedule",
                occurrence_id=f"schedule:{occurrence}",
                started_by=f"automation:{definition.automation_id}",
                correlation_id=f"automation:{definition.automation_id}:{occurrence}",
                input_snapshot={"scheduledForMs": occurrence, "missed": True},
            )
            task_id = str(run.task_run_refs[0].get("taskId", "")) if run.task_run_refs else ""
            summary = "Skipped by the Automation missed-run policy."
            self._settle_task(task_id, completed=False, summary=summary, terminal="cancelled")
            result = self.store.update_run(
                run.automation_run_id,
                status="skipped",
                blocked_reason=summary,
            )
            self._sync_trigger_runtime(definition)
            return result.status

        result: AutomationRun | None = None
        for scheduled_for in occurrences:
            run, created = self._prepare_run(
                definition,
                trigger_type="schedule",
                occurrence_id=f"schedule:{scheduled_for}",
                started_by=f"automation:{definition.automation_id}",
                correlation_id=f"automation:{definition.automation_id}:{scheduled_for}",
                input_snapshot={
                    "scheduledForMs": scheduled_for,
                    "missed": missed,
                    "catchUpCount": len(occurrences),
                },
            )
            result = await self._execute_run(run.automation_run_id) if created else run
        refreshed = self.store.read_definition(definition.automation_id)
        self._sync_trigger_runtime(refreshed)
        return (result.output_summary or result.status) if result is not None else "skipped"

    async def _execute_run(self, automation_run_id: str) -> AutomationRun:
        """Execute one prepared run using a regular, snapshot-native ADK turn."""
        run = self.store.read_run(automation_run_id)
        definition = self.store.read_definition(run.automation_id)
        definition_snapshot = self._definition_snapshot_for_run(definition, run)
        task_id = str(run.task_run_refs[0].get("taskId", "")) if run.task_run_refs else ""
        readiness = self.readiness(definition)
        if not readiness["ready"]:
            reason = "; ".join(str(item.get("message", "")) for item in readiness["reasons"])
            self._settle_task(task_id, completed=False, summary=reason)
            blocked = self.store.update_run(automation_run_id, status="blocked", blocked_reason=reason)
            await self._drain_queued_run(definition.automation_id, exclude_run_id=automation_run_id)
            return blocked
        self.store.update_run(automation_run_id, status="running")
        self._update_task(task_id, status="running", summary="Automation Agent Run started.")
        retry = dict(definition_snapshot.get("retryPolicy") or {})
        max_attempts = int(_policy_value(retry, "maxAttempts", "max_attempts", default=1))
        backoff_seconds = int(_policy_value(retry, "backoffSeconds", "backoff_seconds", default=30))
        budget = dict(definition_snapshot.get("budgetPolicy") or {})
        timeout_seconds = int(_policy_value(budget, "timeoutSeconds", "timeout_seconds", default=1_800))
        started_at = time.monotonic()
        last_error = ""
        try:
            session = await self.supervisor.get_session(
                definition.agent_id,
                user_id=f"automation:{definition.automation_id}",
                session_id=run.session_id,
            )
            if session is None:
                await self.supervisor.create_session(
                    definition.agent_id,
                    user_id=f"automation:{definition.automation_id}",
                    session_id=run.session_id,
                )
            for attempt in range(1, max_attempts + 1):
                self.store.set_run_attempt(automation_run_id, attempt)
                self.task_events.append_event(
                    task_id,
                    "automation.attempt.started",
                    message=f"Automation attempt {attempt} started.",
                    payload={"attempt": attempt, "maxAttempts": max_attempts},
                )
                try:
                    prompt = self._render_prompt(definition_snapshot, run)
                    output = await asyncio.wait_for(
                        self.supervisor.hello(
                            definition.agent_id,
                            prompt,
                            user_id=f"automation:{definition.automation_id}",
                            session_id=run.session_id,
                        ),
                        timeout=timeout_seconds,
                    )
                    invocation_id = await self._latest_invocation_id(
                        definition.agent_id,
                        definition.automation_id,
                        run.session_id,
                    )
                    monitor_policy = dict(definition_snapshot.get("monitorPolicy") or {})
                    visible_output, monitor_state = self._apply_monitor_policy(
                        definition,
                        output,
                        policy=monitor_policy,
                    )
                    delivery_refs = self._record_delivery(
                        definition,
                        task_id,
                        visible_output,
                        monitor_state,
                        policy=dict(definition_snapshot.get("deliveryPolicy") or {}),
                    )
                    elapsed_ms = int((time.monotonic() - started_at) * 1_000)
                    budget_state = {
                        "timeoutSeconds": timeout_seconds,
                        "elapsedMs": elapsed_ms,
                        "attempts": attempt,
                        "exhausted": False,
                    }
                    self._settle_task(task_id, completed=True, summary=visible_output)
                    result = self.store.update_run(
                        automation_run_id,
                        status="completed",
                        adk_run_id=invocation_id,
                        output_summary=visible_output[:16_384],
                        delivery_refs_json=json.dumps(delivery_refs, ensure_ascii=False, sort_keys=True),
                        budget_state_json=json.dumps(budget_state, ensure_ascii=False, sort_keys=True),
                    )
                    self._apply_monitor_stop_condition(definition, output, policy=monitor_policy)
                    return result
                except Exception as exc:
                    last_error = "timeout" if isinstance(exc, TimeoutError) else type(exc).__name__
                    self.task_events.append_event(
                        task_id,
                        "automation.attempt.failed",
                        message=last_error,
                        payload={"attempt": attempt, "retrying": attempt < max_attempts},
                    )
                    if attempt < max_attempts and backoff_seconds:
                        await asyncio.sleep(min(backoff_seconds * (2 ** (attempt - 1)), 3_600))
            elapsed_ms = int((time.monotonic() - started_at) * 1_000)
            self._settle_task(task_id, completed=False, summary=last_error)
            return self.store.update_run(
                automation_run_id,
                status="failed",
                error_summary=last_error,
                budget_state_json=json.dumps(
                    {
                        "timeoutSeconds": timeout_seconds,
                        "elapsedMs": elapsed_ms,
                        "attempts": max_attempts,
                        "exhausted": last_error == "timeout",
                    },
                    sort_keys=True,
                ),
            )
        finally:
            await self._drain_queued_run(definition.automation_id, exclude_run_id=automation_run_id)

    def _prepare_run(
        self,
        definition: AutomationDefinition,
        *,
        trigger_type: str,
        occurrence_id: str,
        started_by: str,
        correlation_id: str,
        input_snapshot: dict[str, Any],
    ) -> tuple[AutomationRun, bool]:
        if definition.status != "active":
            raise AutomationStateError("automation is not active")
        existing = self.store.run_for_occurrence(definition.automation_id, occurrence_id)
        if existing is not None:
            return existing, False
        concurrency = definition.concurrency_policy
        mode = str(concurrency.get("mode") or "skip")
        active_count = self.store.active_run_count(definition.automation_id)
        parallel_limit = int(concurrency.get("limit") or 1)
        skip_for_concurrency = (
            (mode == "skip" and active_count > 0)
            or (mode == "parallel-with-limit" and active_count >= parallel_limit)
            or (mode == "queue-one" and active_count >= 2)
        )
        queue_for_concurrency = mode == "queue-one" and active_count == 1
        agent = self.config_repository.read_agent(definition.agent_id)
        runtime = self.supervisor.runtime_for(definition.agent_id)
        principal = {
            "principalId": f"automation:{definition.automation_id}",
            "ownerUserId": definition.user_id,
            "permissionPolicy": definition.permission_policy,
            "definitionSnapshot": self._definition_snapshot(definition),
        }
        task = self.task_store.create_task(
            kind="automation_run",
            status="queued",
            title=f"Automation: {definition.name}",
            owner_key=f"automation:{definition.automation_id}",
            user_id=definition.user_id,
            thread_id=f"automation:{definition.automation_id}",
            session_id=f"automation:{definition.automation_id}",
            turn_id=occurrence_id,
            dedupe_key=f"automation:{definition.automation_id}:{occurrence_id}",
            external_ref=f"automation:{definition.automation_id}:{occurrence_id}",
            runner_payload={
                "runner": "automation",
                "automationId": definition.automation_id,
                "definitionRevision": definition.revision,
                "triggerType": trigger_type,
                "occurrenceId": occurrence_id,
            },
            runner_capabilities=AUTOMATION_TASK_CAPABILITIES,
            resume_policy="new_adk_run",
            stop_policy="cooperative",
            cancel_policy="cooperative",
            progress_summary="Automation queued.",
        )
        self.task_events.append_event(task.task_id, "task.queued", message="Automation queued.", payload={"automationId": definition.automation_id})
        run, created = self.store.create_run(
            definition=definition,
            trigger_type=trigger_type,
            occurrence_id=occurrence_id,
            started_by=started_by,
            input_snapshot=input_snapshot,
            principal_snapshot=principal,
            permission_revision=hashlib.sha256(repr(sorted(definition.permission_policy.items())).encode()).hexdigest(),
            agent_revision=agent.revision,
            model_profile_revision=runtime.metadata.model_profile_revision,
            extension_snapshot_digest=runtime.metadata.extension_revision,
            task_id=task.task_id,
            correlation_id=correlation_id,
        )
        if not created:
            self._settle_task(task.task_id, completed=False, summary="Duplicate Automation occurrence skipped.", terminal="cancelled")
        elif skip_for_concurrency:
            summary = "Skipped because this Automation already has an active Run."
            self._settle_task(task.task_id, completed=False, summary=summary, terminal="cancelled")
            run = self.store.update_run(run.automation_run_id, status="skipped", blocked_reason=summary)
            created = False
        elif queue_for_concurrency:
            self._update_task(task.task_id, status="queued", summary="Waiting for the previous Automation Run.")
            created = False
        return run, created

    async def reconcile_runtime(self) -> None:
        """Rebuild derived schedules and make interrupted facts explicit on startup."""
        definitions = self.store.list_all_definitions()
        by_id = {item.automation_id: item for item in definitions}
        for definition in definitions:
            self._sync_schedule(definition)

        for job in list(self.cron.list_jobs(include_disabled=True)):
            if job.payload.source_kind == "automation" and job.payload.source_id not in by_id:
                self.cron.remove_job(job.id)

        queued_by_automation: dict[str, list[AutomationRun]] = {}
        for run in self.store.list_incomplete_runs():
            task_id = str(run.task_run_refs[0].get("taskId", "")) if run.task_run_refs else ""
            if run.status == "running":
                summary = "The Node restarted before this Automation Run settled."
                self._settle_task(task_id, completed=False, summary=summary, terminal="blocked")
                self.store.update_run(
                    run.automation_run_id,
                    status="blocked",
                    blocked_reason=summary,
                )
                continue
            queued_by_automation.setdefault(run.automation_id, []).append(run)

        for automation_id, queued in queued_by_automation.items():
            definition = by_id.get(automation_id)
            if definition is None or definition.status != "active":
                continue
            mode = str(definition.concurrency_policy.get("mode") or "skip")
            limit = int(definition.concurrency_policy.get("limit") or 1)
            resumable = queued[:1] if mode in {"skip", "queue-one"} else queued[: max(1, limit)]
            for run in resumable:
                asyncio.create_task(self._execute_run(run.automation_run_id))

        for delivery in self.delivery_store.list_retryable_deliveries(limit=100):
            self.delivery_store.mark_delivered(delivery.delivery_key)

    def _sync_schedule(self, definition: AutomationDefinition) -> AutomationDefinition:
        """Synchronize the derived Cron job without discarding persisted due work.

        A Node restart may load a Cron job whose ``next_run_at_ms`` is already
        due.  Rewriting an unchanged job would recompute that timestamp and
        silently lose the missed occurrence.  Only definition revisions or
        enablement changes are therefore written back.
        """
        if definition.status == "deleted":
            if definition.scheduler_job_id:
                self.cron.remove_job(definition.scheduler_job_id)
                definition = self.store.set_scheduler_job_id(definition.automation_id, "")
            return definition
        trigger = self.store.trigger_for(definition.automation_id)
        if trigger is None:
            if definition.scheduler_job_id:
                self.cron.remove_job(definition.scheduler_job_id)
                definition = self.store.set_scheduler_job_id(definition.automation_id, "")
            return definition
        if trigger.trigger_type != "schedule":
            if definition.scheduler_job_id:
                self.cron.remove_job(definition.scheduler_job_id)
                definition = self.store.set_scheduler_job_id(definition.automation_id, "")
            return definition
        schedule = CronSchedule(
            kind=trigger.schedule_kind, every_seconds=trigger.every_seconds,
            cron_expr=trigger.cron_expr or None, at_ms=trigger.at_ms, tz=trigger.timezone or None,
        )
        enabled = definition.status == "active"
        if definition.scheduler_job_id:
            existing = next(
                (
                    item
                    for item in self.cron.list_jobs(include_disabled=True)
                    if item.id == definition.scheduler_job_id
                ),
                None,
            )
            if existing is None:
                definition = self.store.set_scheduler_job_id(definition.automation_id, "")
                return self._sync_schedule(definition)
            if existing.payload.source_revision != definition.revision:
                job = self.cron.update_job(
                    definition.scheduler_job_id, name=definition.name, schedule=schedule,
                    message=definition.instructions, agent_id=definition.agent_id,
                    user_id=f"automation:{definition.automation_id}", delete_after_run=trigger.schedule_kind == "at",
                    source_kind="automation", source_id=definition.automation_id, source_revision=definition.revision,
                )
                if job is None:
                    definition = self.store.set_scheduler_job_id(definition.automation_id, "")
                    return self._sync_schedule(definition)
            else:
                job = existing
        else:
            job = self.cron.add_job(
                name=definition.name, schedule=schedule, message=definition.instructions,
                agent_id=definition.agent_id, user_id=f"automation:{definition.automation_id}",
                delete_after_run=trigger.schedule_kind == "at", source_kind="automation",
                source_id=definition.automation_id, source_revision=definition.revision,
            )
            definition = self.store.set_scheduler_job_id(definition.automation_id, job.id)
        if job.enabled != enabled:
            updated = self.cron.enable_job(job.id, enabled=enabled)
            if updated is not None and not isinstance(updated, bool):
                job = updated
        self.store.update_trigger_runtime(
            definition.automation_id, next_run_at_ms=job.state.next_run_at_ms,
            last_run_at_ms=job.state.last_run_at_ms,
        )
        return definition

    def _sync_trigger_runtime(self, definition: AutomationDefinition) -> None:
        if not definition.scheduler_job_id:
            return
        job = next((item for item in self.cron.list_jobs(include_disabled=True) if item.id == definition.scheduler_job_id), None)
        if job is not None:
            self.store.update_trigger_runtime(
                definition.automation_id, next_run_at_ms=job.state.next_run_at_ms,
                last_run_at_ms=job.state.last_run_at_ms,
            )

    def _resolve_definition_inputs(self, agent_id: str, workspace_ref: str, model_profile_ref: str) -> tuple[Any, str, str]:
        agent = self.config_repository.read_agent(agent_id)
        node = self.config_repository.read_node().document
        if agent_id not in node.spec.enabled_agents:
            raise AutomationStateError("target agent is not enabled")
        workspace = workspace_ref or agent.document.spec.workspace
        profile = model_profile_ref or agent.document.spec.model_policy.default_profile
        if profile:
            self.profile_repository.read_profile(profile)
        return agent, workspace, profile or ""

    @staticmethod
    def _require_owner(definition: AutomationDefinition, user_id: str) -> None:
        if definition.user_id != user_id:
            raise AutomationNotFoundError("automation not found")

    @staticmethod
    def _render_prompt(definition: dict[str, Any], run: AutomationRun) -> str:
        requirements = "\n".join(f"- {item}" for item in definition.get("outputRequirements", []))
        input_text = repr(run.input_snapshot) if run.input_snapshot else "{}"
        return (
            f"You are executing a user-created {PRODUCT.display_name} Automation as an independent Google ADK Agent Run.\n"
            f"Automation: {definition.get('name', '')}\nInstructions:\n{definition.get('instructions', '')}\n"
            f"Output requirements:\n{requirements or '- Return a clear result.'}\n"
            f"Typed input snapshot: {input_text}\n"
            "Do not claim completion without reporting concrete results, artifacts, errors, and access limitations."
        )

    async def _latest_invocation_id(self, agent_id: str, automation_id: str, session_id: str) -> str:
        session = await self.supervisor.get_session(agent_id, user_id=f"automation:{automation_id}", session_id=session_id)
        events = list(getattr(session, "events", ()) or ()) if session is not None else []
        return str(getattr(events[-1], "invocation_id", "") or "") if events else ""

    def _update_task(self, task_id: str, *, status: str, summary: str) -> None:
        if not task_id:
            return
        self.task_store.update_task(task_id, status=status, progress_summary=summary)
        self.task_events.append_event(task_id, f"task.{status}", message=summary, payload={})

    def _settle_task(self, task_id: str, *, completed: bool, summary: str, terminal: str | None = None) -> None:
        if not task_id:
            return
        status = terminal or ("completed" if completed else "failed")
        self.task_store.update_task(
            task_id, status=status, progress_summary=summary[:2000], terminal_summary=summary[:16_384],
            last_error="" if completed else summary[:2000],
        )
        self.task_events.append_event(task_id, f"task.{status}", message=summary[:2000], payload={})

    async def _drain_queued_run(self, automation_id: str, *, exclude_run_id: str) -> None:
        """Run the single queue-one successor after the active ADK Run settles."""
        if self.store.running_run_count(automation_id) > 0:
            return
        queued = [run for run in self.store.queued_runs(automation_id, limit=2) if run.automation_run_id != exclude_run_id]
        if queued:
            await self._execute_run(queued[0].automation_run_id)

    def _apply_monitor_policy(
        self,
        definition: AutomationDefinition,
        output: str,
        *,
        policy: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Persist a comparison cursor and suppress duplicate monitor notifications."""
        policy = dict(policy if policy is not None else definition.monitor_policy)
        enabled = bool(policy.get("enabled"))
        digest = hashlib.sha256(output.strip().encode("utf-8")).hexdigest()
        previous = self.store.monitor_state(definition.automation_id) if enabled else {}
        changed = previous.get("observationDigest") != digest
        state = {"enabled": enabled, "changed": changed, "observationDigest": digest}
        if enabled:
            self.store.update_monitor_state(
                definition.automation_id,
                observation_digest=digest,
                observation_summary=output,
                cursor={"runDigest": digest},
            )
        notify_only = bool(_policy_value(policy, "notifyOnChangeOnly", "notify_on_change_only", default=True))
        if enabled and notify_only and not changed:
            return "No change detected.", state
        return output, state

    def _apply_monitor_stop_condition(
        self,
        definition: AutomationDefinition,
        output: str,
        *,
        policy: dict[str, Any] | None = None,
    ) -> None:
        """Pause a monitor after its explicit deterministic stop condition matches."""
        resolved_policy = dict(policy if policy is not None else definition.monitor_policy)
        needle = str(_policy_value(resolved_policy, "stopWhenContains", "stop_when_contains", default="")).strip()
        if not needle or needle.casefold() not in output.casefold():
            return
        current = self.store.read_definition(definition.automation_id)
        if current.status == "active":
            self.transition(
                current,
                status="paused",
                expected_revision=current.revision,
                actor_id=f"automation:{current.automation_id}",
                correlation_id=f"automation:{current.automation_id}:stop-condition",
            )

    def _record_delivery(
        self,
        definition: AutomationDefinition,
        task_id: str,
        output: str,
        monitor_state: dict[str, Any],
        *,
        policy: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Record delivery independently so publishing failures do not fail execution."""
        if monitor_state.get("enabled") and not monitor_state.get("changed"):
            return []
        delivery_policy = dict(policy if policy is not None else definition.delivery_policy)
        delivery_type = str(delivery_policy.get("type") or "session")
        delivery, _created = self.delivery_store.record_once(
            task_id=task_id,
            delivery_type=delivery_type,
            delivery_key=f"automation:{definition.automation_id}:{task_id}:{delivery_type}",
            payload={"automationId": definition.automation_id, "summary": output[:2_000]},
        )
        try:
            delivered = self.delivery_store.mark_delivered(delivery.delivery_key)
            final = delivered or delivery
        except Exception as exc:
            failed = self.delivery_store.mark_failed(
                delivery.delivery_key,
                error=type(exc).__name__,
                retry_after_ms=60_000,
            )
            final = failed or delivery
        return [{"deliveryKey": final.delivery_key, "type": final.delivery_type, "status": final.status}]

    @staticmethod
    def _definition_snapshot(definition: AutomationDefinition) -> dict[str, Any]:
        """Freeze all behavior-affecting Definition fields for one Run."""
        return {
            "revision": definition.revision,
            "name": definition.name,
            "instructions": definition.instructions,
            "outputRequirements": definition.output_requirements,
            "agentId": definition.agent_id,
            "workspaceRef": definition.workspace_ref,
            "modelProfileRef": definition.model_profile_ref,
            "extensionPolicy": definition.extension_policy,
            "permissionPolicy": definition.permission_policy,
            "deliveryPolicy": definition.delivery_policy,
            "concurrencyPolicy": definition.concurrency_policy,
            "missedRunPolicy": definition.missed_run_policy,
            "retryPolicy": definition.retry_policy,
            "budgetPolicy": definition.budget_policy,
            "monitorPolicy": definition.monitor_policy,
        }

    @classmethod
    def _definition_snapshot_for_run(
        cls,
        definition: AutomationDefinition,
        run: AutomationRun,
    ) -> dict[str, Any]:
        """Read the immutable Run snapshot, with backward-compatible fallback."""
        value = run.principal_snapshot.get("definitionSnapshot")
        return dict(value) if isinstance(value, dict) else cls._definition_snapshot(definition)

    @staticmethod
    def templates() -> tuple[AutomationTemplate, ...]:
        return DEFAULT_AUTOMATION_TEMPLATES


__all__ = ["AutomationService", "AutomationTemplate", "DEFAULT_AUTOMATION_TEMPLATES"]


def _policy_value(policy: dict[str, Any], camel: str, snake: str, *, default: Any) -> Any:
    """Read policy values from stable camelCase facts or Python-native inputs."""
    if camel in policy:
        return policy[camel]
    if snake in policy:
        return policy[snake]
    return default


def _validate_local_event_input(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate the deliberately small, deterministic local-event schema subset."""
    if schema.get("type") != "object":
        raise AutomationStateError("local event input schema is invalid")
    properties = schema.get("properties", {})
    required = schema.get("required", [])
    if not isinstance(properties, dict) or not isinstance(required, list):
        raise AutomationStateError("local event input schema is invalid")
    missing = [name for name in required if name not in payload]
    if missing:
        raise AutomationStateError(f"local event input is missing required field: {missing[0]}")
    if schema.get("additionalProperties", False) is False:
        unknown = sorted(set(payload) - set(properties))
        if unknown:
            raise AutomationStateError(f"local event input contains unknown field: {unknown[0]}")
    expected_types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
        "null": type(None),
    }
    for name, value in payload.items():
        rule = properties.get(name)
        if not isinstance(rule, dict):
            continue
        expected = rule.get("type")
        python_type = expected_types.get(str(expected))
        if python_type is None:
            raise AutomationStateError(f"local event schema for {name} uses an unsupported type")
        if expected in {"integer", "number"} and isinstance(value, bool):
            valid = False
        else:
            valid = isinstance(value, python_type)
        if not valid:
            raise AutomationStateError(f"local event field {name} must be {expected}")
        if isinstance(value, str) and isinstance(rule.get("maxLength"), int) and len(value) > int(rule["maxLength"]):
            raise AutomationStateError(f"local event field {name} exceeds maxLength")
        if isinstance(value, list) and isinstance(rule.get("maxItems"), int) and len(value) > int(rule["maxItems"]):
            raise AutomationStateError(f"local event field {name} exceeds maxItems")
