"""OpenPPX Node composition root and ordered service lifecycle."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openppx.config import SecretStore, SystemCredentialSecretStore
from openppx.control_plane import ControlPlaneApplication, build_control_plane
from openppx.extensions import AppManager, McpManager, SkillManager
from openppx.extensions.prefixes import ToolPrefixIndex

from .assembly import RuntimeAssembler
from .client_api_auth import resolve_client_api_access_token, validate_client_api_bind
from .client_api_service import ClientApiCoordinator, ClientApiHttpServer
from .node_runtime import NodeRuntimeSupervisor
from .task_scheduler import TaskWakeScheduler


ServerFactory = Callable[..., Any]


class _AsyncServiceThread:
    """Run one async start/stop service on a dedicated Node-owned loop."""

    def __init__(self, service: Any, *, name: str) -> None:
        self._service = service
        self._name = name
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready = threading.Event()
        self._error: BaseException | None = None

    def start(self) -> None:
        """Start the service and wait until its coroutine has completed."""
        if self._thread is not None:
            return
        thread = threading.Thread(target=self._run, name=self._name, daemon=True)
        self._thread = thread
        thread.start()
        self._ready.wait(timeout=10)
        if self._error is not None:
            raise RuntimeError(f"Failed to start {self._name}.") from self._error
        if not self._ready.is_set():
            raise RuntimeError(f"Timed out starting {self._name}.")

    def stop(self) -> None:
        """Stop the service once and join its owner thread."""
        thread = self._thread
        loop = self._loop
        if thread is None or loop is None:
            return
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=10)
        self._thread = None
        self._loop = None

    def _run(self) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(self._service.start())
            self._ready.set()
            loop.run_forever()
            loop.run_until_complete(self._service.stop())
        except BaseException as exc:  # pragma: no cover - defensive thread boundary
            self._error = exc
            self._ready.set()
        finally:
            loop.close()


@dataclass(slots=True)
class OpenPpxNodeHost:
    """Own one complete Node graph and its deterministic lifecycle."""

    node_root: Path
    control_plane: ControlPlaneApplication
    runtime_supervisor: NodeRuntimeSupervisor
    coordinator: ClientApiCoordinator
    server: Any
    scheduler: Any
    address: tuple[str, int]
    authentication_required: bool
    _scheduler_thread: _AsyncServiceThread = field(init=False, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _started: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self._scheduler_thread = _AsyncServiceThread(
            self.scheduler,
            name="openppx-node-scheduler",
        )

    @classmethod
    def build(
        cls,
        node_root: Path,
        *,
        host: str | None = None,
        port: int | None = None,
        access_token: str | None = None,
        secret_store: SecretStore | None = None,
        scheduler: Any | None = None,
        server_factory: ServerFactory = ClientApiHttpServer,
    ) -> "OpenPpxNodeHost":
        """Compose one Node from its strict Config and explicit dependencies."""
        root = node_root.expanduser().resolve(strict=False)
        secrets = secret_store or SystemCredentialSecretStore()
        control_plane = build_control_plane(root, secret_store=secrets)
        node_resource = control_plane.config_repository.read_node()
        listener = node_resource.document.spec.client_api
        resolved_host = host or listener.listen_host
        resolved_port = port or listener.port
        authentication_required = listener.authentication == "required"
        resolved_token = resolve_client_api_access_token(access_token) if authentication_required else ""
        if authentication_required and not resolved_token:
            raise ValueError("Node Client API authentication is required but no access token is configured.")
        validate_client_api_bind(host=resolved_host, access_token=resolved_token)

        prefix_index = ToolPrefixIndex()
        mcp_manager = McpManager(root, secrets, prefix_index=prefix_index)
        app_manager = AppManager(root, secrets, prefix_index=prefix_index)
        assembler = RuntimeAssembler(
            node_root=root,
            secret_store=secrets,
            skill_manager=SkillManager(root, builtin_skills=_builtin_skill_roots()),
            mcp_manager=mcp_manager,
            app_manager=app_manager,
        )
        runtime_supervisor = NodeRuntimeSupervisor(
            config_service=control_plane.config_service,
            assembler=assembler,
        )
        control_plane.attach_runtime(runtime_supervisor)
        coordinator = ClientApiCoordinator(
            data_dir=root,
            control_plane=control_plane,
            runtime_supervisor=runtime_supervisor,
        )
        resolved_scheduler = scheduler or TaskWakeScheduler(
            task_store=assembler.services.task_store,
        )
        server = server_factory(
            (resolved_host, resolved_port),
            coordinator,
            access_token=resolved_token,
        )
        return cls(
            node_root=root,
            control_plane=control_plane,
            runtime_supervisor=runtime_supervisor,
            coordinator=coordinator,
            server=server,
            scheduler=resolved_scheduler,
            address=(resolved_host, resolved_port),
            authentication_required=authentication_required,
        )

    def serve_forever(self) -> None:
        """Start Node-owned services and block in the Client API server."""
        if self._closed:
            raise RuntimeError("The OpenPPX Node host is already closed.")
        self._scheduler_thread.start()
        self._started = True
        try:
            self.server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            self._close_components(shutdown_server=False)

    def close(self) -> None:
        """Idempotently stop accepting requests, then stop Node services."""
        self._close_components(shutdown_server=self._started)

    def _close_components(self, *, shutdown_server: bool) -> None:
        if self._closed:
            return
        self._closed = True
        if shutdown_server:
            self.server.shutdown()
        self.server.server_close()
        self._scheduler_thread.stop()
        self.runtime_supervisor.close()


def run_node(
    node_root: Path,
    *,
    host: str | None = None,
    port: int | None = None,
    access_token: str | None = None,
) -> None:
    """Build and run the foreground OpenPPX Node process."""
    node = OpenPpxNodeHost.build(
        node_root,
        host=host,
        port=port,
        access_token=access_token,
    )
    auth = "required" if node.authentication_required else "disabled"
    print(
        f"openppx node listening on http://{node.address[0]}:{node.address[1]} "
        f"(authentication: {auth})",
        flush=True,
    )
    node.serve_forever()


def _builtin_skill_roots() -> dict[str, Path]:
    """Return the packaged Skill roots registered by the Node composition root."""
    package_root = Path(__file__).resolve().parent.parent / "skills"
    return {
        child.name: child
        for child in sorted(package_root.iterdir(), key=lambda item: item.name)
        if child.is_dir() and child.joinpath("SKILL.md").is_file()
    }


__all__ = ["OpenPpxNodeHost", "run_node"]
