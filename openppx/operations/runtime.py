"""Node-owned lifecycle for schedulers and automation services."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any


class NodeOperationsRuntime:
    """Start, stop, and call every Node automation service on one event loop."""

    def __init__(
        self,
        *,
        task_scheduler: Any,
        cron: Any,
        heartbeat: Any,
        task_scheduler_enabled: bool = True,
        cron_enabled: bool = True,
        heartbeat_enabled: bool = False,
    ) -> None:
        self.task_scheduler = task_scheduler
        self.cron = cron
        self.heartbeat = heartbeat
        self._services = (
            ("taskScheduler", task_scheduler, task_scheduler_enabled),
            ("cron", cron, cron_enabled),
            ("heartbeat", heartbeat, heartbeat_enabled),
        )
        self._running = False
        self._started: list[tuple[str, Any]] = []
        self._loop: asyncio.AbstractEventLoop | None = None
        self._startup_error: str | None = None

    async def start(self) -> None:
        """Start enabled services in dependency order and roll back on failure."""
        if self._running:
            return
        self._loop = asyncio.get_running_loop()
        self._startup_error = None
        try:
            for name, service, enabled in self._services:
                if not enabled:
                    continue
                await _maybe_await(service.start())
                self._started.append((name, service))
        except Exception:
            self._startup_error = "operations_service_start_failed"
            await self._stop_started()
            self._loop = None
            raise
        self._running = True

    async def stop(self) -> None:
        """Stop started services in reverse order exactly once."""
        await self._stop_started()
        self._running = False
        self._loop = None

    def call(self, operation: Callable[[], Any], *, timeout: float = 30.0) -> Any:
        """Run one async operation on the Node operations loop from a sync Action."""
        loop = self._loop
        if loop is None or not self._running:
            raise RuntimeError("Node operations runtime is not running.")

        async def invoke() -> Any:
            return await _maybe_await(operation())

        future = asyncio.run_coroutine_threadsafe(invoke(), loop)
        return future.result(timeout=timeout)

    def status(self) -> dict[str, object]:
        """Return lifecycle state without exposing thread or event-loop internals."""
        return {
            "running": self._running,
            "startupError": self._startup_error,
            "components": {
                name: {
                    "enabled": enabled,
                    "running": name in {item[0] for item in self._started},
                }
                for name, _service, enabled in self._services
            },
        }

    async def _stop_started(self) -> None:
        while self._started:
            _name, service = self._started.pop()
            await _maybe_await(service.stop())


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


__all__ = ["NodeOperationsRuntime"]
