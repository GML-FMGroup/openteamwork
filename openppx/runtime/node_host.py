"""OpenPPX Node composition root and ordered service lifecycle."""

from __future__ import annotations

import asyncio
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from openppx.config import ConfigLoadError, NodeOperationsSpec, SecretStore, SystemCredentialSecretStore
from openppx.automation import AutomationService, AutomationStore
from openppx.control_plane import ControlPlaneApplication, build_control_plane
from openppx.extensions import (
    AppManager,
    ExtensionRegistry,
    McpManager,
    PluginManager,
    PluginMarketplaceManager,
    SkillManager,
    default_native_app_adapter_registry,
)
from openppx.extensions.indexes import ExtensionReferenceIndex, ResourceIdentityIndex
from openppx.extensions.prefixes import ToolPrefixIndex
from openppx.operations import NodeAutomationExecutor, NodeOperationsRuntime, OperationsService

from .assembly import RuntimeAssembler
from .client_api_auth import resolve_client_api_access_token, validate_client_api_bind
from .client_api_service import ClientApiCoordinator, ClientApiHttpServer
from .node_runtime import NodeRuntimeSupervisor
from .mcp_adapter import McpRuntimeAdapter
from .cron_service import CronService
from .heartbeat_runner import HeartbeatRunner
from .paths import configure_node_root
from .task_store import TaskEventStore
from .task_scheduler import TaskWakeScheduler
from .session_metadata_store import SessionMetadataStore


ServerFactory = Callable[..., Any]


@dataclass(frozen=True, slots=True)
class NodeComposition:
    """One Node-owned Control Plane, Extension, and Runtime object graph."""

    control_plane: ControlPlaneApplication
    runtime_supervisor: NodeRuntimeSupervisor
    assembler: RuntimeAssembler
    operations_service: OperationsService
    operations_runtime: NodeOperationsRuntime
    automation_service: AutomationService
    session_metadata: SessionMetadataStore


def build_node_composition(
    node_root: Path,
    *,
    secret_store: SecretStore | None = None,
    task_scheduler: Any | None = None,
    cron_service: Any | None = None,
    heartbeat_runner: Any | None = None,
) -> NodeComposition:
    """Build the shared business/runtime graph without binding a transport."""
    root = node_root.expanduser().resolve(strict=False)
    secrets = secret_store or SystemCredentialSecretStore()
    control_plane = build_control_plane(root, secret_store=secrets)
    prefix_index = ToolPrefixIndex()
    identity_index = ResourceIdentityIndex()
    reference_index = ExtensionReferenceIndex()
    plugin_manager = PluginManager(
        root,
        secrets,
        identity_index=identity_index,
        reference_index=reference_index,
        prefix_index=prefix_index,
    )
    plugin_marketplaces = PluginMarketplaceManager(root)
    mcp_manager = McpManager(
        root,
        secrets,
        prefix_index=prefix_index,
        identity_index=identity_index,
    )
    app_manager = AppManager(
        root,
        secrets,
        prefix_index=prefix_index,
        identity_index=identity_index,
        adapter_registry=default_native_app_adapter_registry(),
    )
    skill_manager = SkillManager(
        root,
        builtin_skills=_builtin_skill_roots(),
        identity_index=identity_index,
    )
    extension_registry = ExtensionRegistry(
        skills=skill_manager,
        mcp=mcp_manager,
        apps=app_manager,
        plugins=plugin_manager,
    )
    mcp_adapter = McpRuntimeAdapter(secrets)
    control_plane.attach_extensions(
        extension_registry,
        skills=skill_manager,
        mcp=mcp_manager,
        apps=app_manager,
        plugins=plugin_manager,
        plugin_marketplaces=plugin_marketplaces,
        mcp_probe=mcp_adapter,
    )
    assembler = RuntimeAssembler(
        node_root=root,
        secret_store=secrets,
        skill_manager=skill_manager,
        mcp_manager=mcp_manager,
        app_manager=app_manager,
        plugin_manager=plugin_manager,
        mcp_adapter=mcp_adapter,
    )
    runtime_supervisor = NodeRuntimeSupervisor(
        config_service=control_plane.config_service,
        assembler=assembler,
    )
    session_metadata = control_plane.session_metadata
    control_plane.attach_runtime(runtime_supervisor)
    try:
        operations_config = control_plane.config_repository.read_node().document.spec.operations
    except ConfigLoadError as exc:
        if exc.kind != "not_found":
            raise
        operations_config = NodeOperationsSpec()
    automation = NodeAutomationExecutor(control_plane.config_repository, runtime_supervisor)
    resolved_scheduler = task_scheduler or TaskWakeScheduler(
        task_store=assembler.services.task_store,
    )
    resolved_cron = cron_service or CronService(
        root / "database" / "cron.json",
        on_job=automation.run_cron,
        task_store=assembler.services.task_store,
        event_store=TaskEventStore(db_path=assembler.services.task_store.db_path),
    )
    heartbeat_config = operations_config.heartbeat
    resolved_heartbeat = heartbeat_runner or HeartbeatRunner(
        on_run=automation.run_heartbeat,
        every=f"{heartbeat_config.every_seconds}s" if heartbeat_config.enabled else "",
        prompt=heartbeat_config.prompt,
        active_hours={
            "start": heartbeat_config.active_hours.start or "",
            "end": heartbeat_config.active_hours.end or "",
            "timezone": heartbeat_config.active_hours.timezone,
        },
        is_busy=automation.is_busy,
    )
    operations_runtime = NodeOperationsRuntime(
        task_scheduler=resolved_scheduler,
        cron=resolved_cron,
        heartbeat=resolved_heartbeat,
        task_scheduler_enabled=operations_config.task_scheduler_enabled,
        cron_enabled=operations_config.cron_enabled,
        heartbeat_enabled=heartbeat_config.enabled,
    )
    operations_service = OperationsService(
        node_root=root,
        repository=control_plane.config_repository,
        setup=control_plane.setup_service,
        extensions=extension_registry,
        supervisor=runtime_supervisor,
        task_store=assembler.services.task_store,
        cron=resolved_cron,
        heartbeat=resolved_heartbeat,
        runtime=operations_runtime,
        audit=control_plane.audit_store,
    )
    control_plane.attach_operations(operations_service)
    automation_service = AutomationService(
        node_root=root,
        store=AutomationStore(root / "database" / "automations.db"),
        config_repository=control_plane.config_repository,
        profile_repository=control_plane.profile_repository,
        supervisor=runtime_supervisor,
        cron=resolved_cron,
        task_store=assembler.services.task_store,
        operations_runtime=operations_runtime,
    )
    operations_runtime.register_startup_hook(control_plane.goal_store.reconcile_runtime)
    operations_runtime.register_startup_hook(automation_service.reconcile_runtime)
    automation.attach_automation_service(automation_service)
    control_plane.attach_automations(automation_service)
    return NodeComposition(
        control_plane=control_plane,
        runtime_supervisor=runtime_supervisor,
        assembler=assembler,
        operations_service=operations_service,
        operations_runtime=operations_runtime,
        automation_service=automation_service,
        session_metadata=session_metadata,
    )


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
    operations_runtime: NodeOperationsRuntime
    address: tuple[str, int]
    authentication_required: bool
    _operations_thread: _AsyncServiceThread = field(init=False, repr=False)
    _closed: bool = field(init=False, default=False, repr=False)
    _started: bool = field(init=False, default=False, repr=False)

    def __post_init__(self) -> None:
        self._operations_thread = _AsyncServiceThread(
            self.operations_runtime,
            name="openppx-node-operations",
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
        composition = build_node_composition(
            root,
            secret_store=secrets,
            task_scheduler=scheduler,
        )
        control_plane = composition.control_plane
        try:
            node_resource = control_plane.config_repository.read_node()
        except ConfigLoadError as exc:
            if exc.kind != "not_found":
                raise
            node_resource = None
        if node_resource is None:
            resolved_host = host or "127.0.0.1"
            resolved_port = port or 18765
            resolved_token = resolve_client_api_access_token(access_token)
            authentication_required = bool(resolved_token)
        else:
            listener = node_resource.document.spec.client_api
            resolved_host = host or listener.listen_host
            resolved_port = port or listener.port
            authentication_required = listener.authentication == "required"
            resolved_token = resolve_client_api_access_token(access_token) if authentication_required else ""
        if authentication_required and not resolved_token:
            raise ValueError("Node Client API authentication is required but no access token is configured.")
        validate_client_api_bind(host=resolved_host, access_token=resolved_token)

        runtime_supervisor = composition.runtime_supervisor
        coordinator = ClientApiCoordinator(
            data_dir=root,
            control_plane=control_plane,
            runtime_supervisor=runtime_supervisor,
            session_metadata=composition.session_metadata,
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
            operations_runtime=composition.operations_runtime,
            address=(resolved_host, resolved_port),
            authentication_required=authentication_required,
        )

    def serve_forever(self) -> None:
        """Start Node-owned services and block in the Client API server."""
        if self._closed:
            raise RuntimeError("The OpenPPX Node host is already closed.")
        self._operations_thread.start()
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
        self._operations_thread.stop()
        self.runtime_supervisor.close()
        self.control_plane.close()


def run_node(
    node_root: Path,
    *,
    host: str | None = None,
    port: int | None = None,
    access_token: str | None = None,
) -> None:
    """Build and run the foreground OpenPPX Node process."""
    configure_node_root(node_root)
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


__all__ = ["NodeComposition", "OpenPpxNodeHost", "build_node_composition", "run_node"]
