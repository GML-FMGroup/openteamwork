"""Explicit Runtime Supervisor adapter for Node Cron and Heartbeat turns."""

from __future__ import annotations

from openppx.config import FilesystemConfigRepository
from openppx.runtime.cron_service import CronJob
from openppx.runtime.heartbeat_runner import HeartbeatRunRequest
from openppx.runtime.node_runtime import NodeRuntimeSupervisor


class NodeAutomationExecutor:
    """Execute background turns through the same snapshot-native Runtime as clients."""

    def __init__(
        self,
        repository: FilesystemConfigRepository,
        supervisor: NodeRuntimeSupervisor,
    ) -> None:
        self.repository = repository
        self.supervisor = supervisor
        self.automation_service = None

    def attach_automation_service(self, service) -> None:
        """Attach the formal User Automation executor after scheduler composition."""
        self.automation_service = service

    async def run_cron(self, job: CronJob) -> str:
        """Execute one persisted Cron job with explicit Agent and principal scope."""
        if job.payload.source_kind == "automation":
            if self.automation_service is None:
                raise RuntimeError("The User Automation service is not attached.")
            return await self.automation_service.run_scheduled(job)
        agent_id = self._enabled_agent(job.payload.agent_id)
        user_id = job.payload.user_id or "service:cron"
        session_id = f"cron-{job.id}"
        await self._ensure_session(agent_id, user_id=user_id, session_id=session_id)
        return await self.supervisor.hello(
            agent_id,
            job.payload.message,
            user_id=user_id,
            session_id=session_id,
        )

    async def run_heartbeat(self, request: HeartbeatRunRequest) -> None:
        """Execute one heartbeat turn using the first explicitly enabled Agent."""
        agent_id = self._enabled_agent(None)
        user_id = "service:heartbeat"
        session_id = "heartbeat-main"
        await self._ensure_session(agent_id, user_id=user_id, session_id=session_id)
        await self.supervisor.hello(
            agent_id,
            request.prompt,
            user_id=user_id,
            session_id=session_id,
        )

    def is_busy(self) -> bool:
        """Return whether an interactive or automation Run is currently active."""
        return int(self.supervisor.status().get("activeRuns", 0)) > 0

    async def _ensure_session(self, agent_id: str, *, user_id: str, session_id: str) -> None:
        session = await self.supervisor.get_session(
            agent_id,
            user_id=user_id,
            session_id=session_id,
        )
        if session is None:
            await self.supervisor.create_session(
                agent_id,
                user_id=user_id,
                session_id=session_id,
            )

    def _enabled_agent(self, requested: str | None) -> str:
        node = self.repository.read_node().document
        enabled = tuple(node.spec.enabled_agents)
        if requested is not None:
            if requested not in enabled:
                raise ValueError("The Cron target Agent is not enabled on this Node.")
            return requested
        if not enabled:
            raise ValueError("The Node has no enabled Agent for automation.")
        return enabled[0]


__all__ = ["NodeAutomationExecutor"]
