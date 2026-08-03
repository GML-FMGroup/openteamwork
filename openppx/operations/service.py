"""Transport-independent Operations facade for one OpenPPX Node."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from openppx.config import ConfigError, FilesystemConfigRepository
from openppx.extensions import ExtensionRegistry
from openppx.governance import ActionAuditStore, AuditQuery
from openppx.runtime.cron_service import CronSchedule, CronService
from openppx.runtime.heartbeat_runner import HeartbeatRunner
from openppx.runtime.node_runtime import NodeRuntimeSupervisor
from openppx.runtime.task_execution import TaskController
from openppx.runtime.task_store import TaskStore
from openppx.runtime.token_usage_store import read_token_usage_stats
from openppx.setup import SetupService

from .runtime import NodeOperationsRuntime


HealthState = Literal["healthy", "degraded", "unavailable", "disabled"]


@dataclass(frozen=True, slots=True)
class HealthComponent:
    """One stable, non-sensitive Node component health result."""

    component: str
    state: HealthState
    code: str
    reason: str
    remediation: str | None = None

    def payload(self) -> dict[str, object]:
        """Project one component using client-contract field names."""
        return asdict(self)


class OperationsService:
    """Expose existing runtime facts and controls through one application service."""

    def __init__(
        self,
        *,
        node_root: Path,
        repository: FilesystemConfigRepository,
        setup: SetupService,
        extensions: ExtensionRegistry,
        supervisor: NodeRuntimeSupervisor,
        task_store: TaskStore,
        cron: CronService,
        heartbeat: HeartbeatRunner,
        runtime: NodeOperationsRuntime,
        audit: ActionAuditStore,
    ) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.repository = repository
        self.setup = setup
        self.extensions = extensions
        self.supervisor = supervisor
        self.task_store = task_store
        self.task_controller = TaskController(task_store=task_store)
        self.cron = cron
        self.heartbeat = heartbeat
        self.runtime = runtime
        self.audit = audit
        self.usage_db_path = self.node_root / "database" / "token_usage.db"

    def overview(self) -> dict[str, object]:
        """Return a compact operator summary backed by the full health report."""
        health = self.health()
        tasks = self.task_store.count_by_status()
        cron_status = self.cron.status()
        return {
            "state": health["state"],
            "components": health["components"],
            "tasks": {"total": sum(tasks.values()), "byStatus": tasks},
            "automation": {
                "cronJobs": int(cron_status.get("jobs", 0) or 0),
                "heartbeatEnabled": bool(self.heartbeat.status().get("enabled")),
            },
        }

    def health(self) -> dict[str, object]:
        """Aggregate Node readiness with stable redacted component semantics."""
        components = [
            self._setup_health(),
            self._runtime_health(),
            self._extension_health(),
            self._lifecycle_health("taskScheduler"),
            self._cron_health(),
            self._heartbeat_health(),
            self._secret_health(),
            self._audit_health(),
            self._stores_health(),
            self._sandbox_health(),
        ]
        state = _aggregate_state(components)
        return {
            "state": state,
            "components": [item.payload() for item in components],
        }

    def list_tasks(self, *, session_id: str | None, limit: int) -> dict[str, object]:
        """Return durable Task projections from the mature TaskController."""
        return {"items": self.task_controller.list_tasks(session_id=session_id, limit=limit)["items"]}

    def list_cron(self, *, include_disabled: bool, history_limit: int) -> dict[str, object]:
        """Return Cron jobs, recent history, and Node-owned runtime status."""
        return {
            "status": _camelize(self.cron.status()),
            "items": [_cron_job_payload(item) for item in self.cron.list_jobs(include_disabled=include_disabled)],
            "history": [_cron_history_payload(item) for item in self.cron.list_history(limit=history_limit)],
        }

    def create_cron(
        self,
        *,
        name: str,
        agent_id: str,
        user_id: str,
        message: str,
        schedule: CronSchedule,
        delete_after_run: bool,
    ) -> dict[str, object]:
        """Create one Agent-scoped Cron job in the Node-owned store."""
        self._require_enabled_agent(agent_id)
        job = self.cron.add_job(
            name=name,
            schedule=schedule,
            message=message,
            agent_id=agent_id,
            user_id=user_id,
            delete_after_run=delete_after_run,
        )
        return {"job": _cron_job_payload(job)}

    def enable_cron(self, job_id: str, *, enabled: bool) -> dict[str, object]:
        """Enable or disable one existing Cron job."""
        job = self.cron.enable_job(job_id, enabled=enabled)
        if job is None:
            raise LookupError("cron_job_not_found")
        return {"job": _cron_job_payload(job)}

    def remove_cron(self, job_id: str) -> dict[str, object]:
        """Remove one existing Cron job."""
        if not self.cron.remove_job(job_id):
            raise LookupError("cron_job_not_found")
        return {"id": job_id, "removed": True}

    def run_cron(self, job_id: str, *, force: bool) -> dict[str, object]:
        """Execute one Cron job on the Node operations event loop."""
        result = self.runtime.call(lambda: self.cron.run_job_with_result(job_id, force=force))
        if result.reason == "not_found":
            raise LookupError("cron_job_not_found")
        return {
            "executed": result.executed,
            "reason": result.reason,
            "taskId": result.task_id,
            "error": None if result.error is None else "Cron execution failed.",
        }

    def heartbeat_status(self) -> dict[str, object]:
        """Return current Node-owned heartbeat state."""
        return _camelize(self.heartbeat.status())

    def run_heartbeat(self, *, reason: str) -> dict[str, object]:
        """Execute one immediate heartbeat on the Node operations event loop."""
        result = self.runtime.call(lambda: self.heartbeat.trigger_now(reason=reason))
        return {
            "status": result.status,
            "reason": result.reason,
            "durationMs": result.duration_ms,
            "error": None if result.error is None else "Heartbeat execution failed.",
        }

    def usage(self, *, limit: int, provider: str | None) -> dict[str, object]:
        """Return Node-local token usage without ambient data-dir lookup."""
        return _camelize(
            read_token_usage_stats(
                limit=limit,
                provider=provider,
                db_path=self.usage_db_path,
            )
        )

    def audit_rows(self, query: AuditQuery) -> dict[str, object]:
        """Return redacted Action audit facts with bounded filters."""
        return {"items": list(self.audit.list(query))}

    def _setup_health(self) -> HealthComponent:
        try:
            status = self.setup.status()
        except Exception:
            return _component("setup", "unavailable", "setup_unavailable", "Setup status is unavailable.", "Inspect Node configuration.")
        state = status.get("state")
        if state == "ready":
            return _component("setup", "healthy", "setup_ready", "Setup and first Hello are verified.")
        return _component("setup", "degraded", "setup_incomplete", "Setup is not fully verified.", "Complete setup and run the first Hello.")

    def _runtime_health(self) -> HealthComponent:
        status = self.supervisor.status()
        if status.get("state") == "running":
            return _component("runtime", "healthy", "runtime_ready", "Runtime Supervisor is ready.")
        return _component("runtime", "unavailable", "runtime_stopped", "Runtime Supervisor is stopped.", "Restart the Node.")

    def _extension_health(self) -> HealthComponent:
        try:
            items = self.extensions.list()
        except Exception:
            return _component("extensions", "degraded", "extension_inventory_unavailable", "Extension inventory is unavailable.", "Inspect Extension resources.")
        not_ready = sum(not item.ready for item in items)
        if not_ready:
            return _component("extensions", "degraded", "extension_not_ready", f"{not_ready} Extension resource(s) are not ready.", "Open Extensions and resolve readiness issues.")
        return _component("extensions", "healthy", "extensions_ready", f"{len(items)} Extension resource(s) are ready.")

    def _lifecycle_health(self, name: str) -> HealthComponent:
        status = self.runtime.status()
        item = status["components"][name]
        if not item["enabled"]:
            return _component(name, "disabled", f"{name}_disabled", f"{name} is disabled by NodeConfig.")
        if status["running"] and item["running"]:
            return _component(name, "healthy", f"{name}_ready", f"{name} is running.")
        return _component(name, "unavailable", f"{name}_stopped", f"{name} is not running.", "Restart the Node.")

    def _cron_health(self) -> HealthComponent:
        lifecycle = self._lifecycle_health("cron")
        if lifecycle.state != "healthy":
            return lifecycle
        status = self.cron.status()
        if status.get("store_error"):
            return _component("cron", "degraded", "cron_store_error", "Cron storage has an error.", "Inspect the Cron store and Node logs.")
        return _component("cron", "healthy", "cron_ready", "Cron scheduler is running.")

    def _heartbeat_health(self) -> HealthComponent:
        lifecycle = self._lifecycle_health("heartbeat")
        if lifecycle.state != "healthy":
            return lifecycle
        status = self.heartbeat.status()
        if status.get("last_status") == "failed":
            return _component("heartbeat", "degraded", "heartbeat_last_run_failed", "The latest heartbeat run failed.", "Inspect the Agent and model readiness.")
        return _component("heartbeat", "healthy", "heartbeat_ready", "Heartbeat is running.")

    def _secret_health(self) -> HealthComponent:
        try:
            credential = self.setup.status()["steps"]["credential"]
        except Exception:
            return _component("secret", "unavailable", "secret_status_unavailable", "Secret status is unavailable.", "Inspect the system credential backend.")
        if credential in {"available", "not_required"}:
            return _component("secret", "healthy", "secret_ready", "Required credentials are available.")
        state: HealthState = "unavailable" if credential == "backend_unavailable" else "degraded"
        return _component("secret", state, "secret_unavailable", "A required credential is unavailable.", "Open Models and update the protected credential.")

    def _audit_health(self) -> HealthComponent:
        status = self.audit.health()
        return _component("audit", status["state"], status["code"], status["reason"], "Restore writable Node storage." if status["state"] != "healthy" else None)  # type: ignore[arg-type]

    def _stores_health(self) -> HealthComponent:
        try:
            self.task_store.count_by_status()
            read_token_usage_stats(limit=1, db_path=self.usage_db_path)
        except Exception:
            return _component("stores", "unavailable", "store_unavailable", "One or more Node stores are unavailable.", "Inspect Node database storage.")
        return _component("stores", "healthy", "stores_ready", "Node stores are readable and writable.")

    def _sandbox_health(self) -> HealthComponent:
        try:
            node = self.repository.read_node().document
            workspaces = [Path(self.repository.read_agent(agent_id).document.spec.workspace) for agent_id in node.spec.enabled_agents]
        except ConfigError:
            return _component("sandbox", "degraded", "sandbox_scope_unavailable", "Agent workspace policy is unavailable.", "Repair Node and Agent configuration.")
        if all(path.is_dir() for path in workspaces):
            return _component("sandbox", "healthy", "sandbox_scope_ready", "Agent workspace boundaries are available.")
        return _component("sandbox", "degraded", "workspace_unavailable", "An Agent workspace is unavailable.", "Create or repair the configured Agent workspace.")

    def _require_enabled_agent(self, agent_id: str) -> None:
        enabled = self.repository.read_node().document.spec.enabled_agents
        if agent_id not in enabled:
            raise ValueError("agent_not_enabled")


def _component(
    name: str,
    state: HealthState,
    code: str,
    reason: str,
    remediation: str | None = None,
) -> HealthComponent:
    return HealthComponent(name, state, code, reason, remediation)


def _aggregate_state(components: list[HealthComponent]) -> str:
    states = {item.state for item in components}
    if "unavailable" in states:
        return "unavailable"
    if "degraded" in states:
        return "degraded"
    return "healthy"


def _cron_job_payload(job) -> dict[str, object]:
    return {
        "id": job.id,
        "name": job.name,
        "enabled": job.enabled,
        "agentId": job.payload.agent_id,
        "userId": job.payload.user_id,
        "message": job.payload.message,
        "schedule": _camelize(asdict(job.schedule)),
        "state": _camelize(asdict(job.state)),
        "createdAtMs": job.created_at_ms,
        "updatedAtMs": job.updated_at_ms,
        "deleteAfterRun": job.delete_after_run,
    }


def _cron_history_payload(entry) -> dict[str, object]:
    return {
        "jobId": entry.job_id,
        "name": entry.name,
        "schedule": _camelize(asdict(entry.schedule)),
        "status": entry.status,
        "createdAtMs": entry.created_at_ms,
        "eventAtMs": entry.event_at_ms,
        "updatedAtMs": entry.updated_at_ms,
        "error": None if entry.error is None else "Cron execution failed.",
    }


def _camelize(value: Any) -> Any:
    if isinstance(value, dict):
        return {_camel_key(str(key)): _camelize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_camelize(item) for item in value]
    return value


def _camel_key(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(item[:1].upper() + item[1:] for item in tail)


__all__ = ["HealthComponent", "OperationsService"]
