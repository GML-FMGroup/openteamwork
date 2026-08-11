"""Immutable product Config to Google ADK runtime assembly boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Literal

from google.genai import types
from google.adk.agents.run_config import RunConfig

from openppx.app.agent import build_root_agent
from openppx.config import ConfigSnapshot, FilesystemConfigRepository, SecretStore
from openppx.extensions import (
    AppManager,
    AppSnapshot,
    McpManager,
    McpSnapshot,
    PluginManager,
    PluginSnapshot,
    SkillManager,
    SkillSnapshot,
    merge_mcp_snapshots,
    merge_skill_snapshots,
)
from openppx.modeling import ModelResolution
from openppx.core.mcp_registry import ManagedMcpToolset, summarize_mcp_toolsets
from openppx.permissions import (
    PermissionAuditStore,
    PermissionSnapshotAuthority,
    ResolvedPermissionSnapshot,
)
from openppx.tooling.history_tools import build_history_tools

from .adk_utils import run_text_async
from .authorization_plugin import OpenPpxAuthorizationPlugin
from .sandbox.egress_policy import write_egress_proxy_policy
from .artifact_service import ArtifactConfig, create_artifact_service
from .context_engine import LongTaskContextStore
from .goal_store import GoalStore
from .historical_session_service import HistoricalSessionService
from .history_access import sync_history_agent_catalog
from .identity_store import IdentityStore
from .agent_access_store import AgentAccessStore
from .memory_service import MemoryConfig, create_memory_service
from .mcp_adapter import McpRuntimeAdapter
from .model_adapter_factory import ModelAdapterFactory
from .plugin_hook_bridge import OpenPpxPluginHookBridge
from .runner_factory import create_runner
from .session_service import SessionConfig, create_session_service
from .session_metadata_store import SessionMetadataStore
from .task_store import TaskStore


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    """Non-sensitive provenance attached to one assembled runtime."""

    node_id: str
    agent_id: str
    model_profile_id: str
    model_profile_revision: str
    provider: str
    model: str
    workspace: str
    snapshot_revision: str
    permission_revision: str
    extension_revision: str
    mcp_diagnostics: tuple[tuple[str, str], ...]
    origin_revisions: tuple[tuple[str, str], ...]


@dataclass(frozen=True, slots=True)
class RuntimeServices:
    """Explicit ADK persistence services for one Node runtime."""

    session_service: Any
    memory_service: Any | None
    artifact_service: Any | None
    task_store: TaskStore
    context_store: LongTaskContextStore
    goal_store: GoalStore
    permission_audit: PermissionAuditStore

    @classmethod
    def local(cls, node_root: Path) -> "RuntimeServices":
        """Build deterministic Node-local services without environment lookup."""
        root = node_root.expanduser().resolve(strict=False)
        database_dir = root / "database"
        database_dir.mkdir(parents=True, exist_ok=True)
        artifacts_dir = root / "artifacts"
        session_service = create_session_service(
            SessionConfig(db_url=f"sqlite+aiosqlite:///{database_dir / 'sessions.db'}")
        )
        memory_service = create_memory_service(
            MemoryConfig(
                enabled=True,
                backend="sqlite",
                markdown_dir=str(root / "memory"),
                sqlite_db_path=str(database_dir / "memory.db"),
            )
        )
        artifact_service = create_artifact_service(
            ArtifactConfig(enabled=True, root_dir=str(artifacts_dir))
        )
        task_db_path = database_dir / "tasks.db"
        task_store = TaskStore(db_path=task_db_path)
        context_store = LongTaskContextStore(db_path=task_db_path)
        goal_store = GoalStore(db_path=database_dir / "goals.db")
        permission_audit = PermissionAuditStore(database_dir / "permission_audit.db")
        return cls(
            session_service=session_service,
            memory_service=memory_service,
            artifact_service=artifact_service,
            task_store=task_store,
            context_store=context_store,
            goal_store=goal_store,
            permission_audit=permission_audit,
        )


@dataclass(frozen=True, slots=True)
class RuntimeExtensionSnapshot:
    """Immutable Skill, direct MCP, and App resources used by one Runtime."""

    revision: str
    skills: SkillSnapshot
    mcp: McpSnapshot
    apps: AppSnapshot
    plugins: PluginSnapshot

    @classmethod
    def create(
        cls,
        skills: SkillSnapshot,
        mcp: McpSnapshot,
        apps: AppSnapshot | None = None,
        plugins: PluginSnapshot | None = None,
    ) -> "RuntimeExtensionSnapshot":
        """Combine child revisions into one deterministic cache identity."""
        resolved_apps = apps or AppSnapshot.empty()
        resolved_plugins = plugins or PluginSnapshot.empty()
        canonical = json.dumps(
            {
                "skills": skills.revision,
                "mcp": mcp.revision,
                "apps": resolved_apps.revision,
                "plugins": resolved_plugins.revision,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            revision=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
            skills=skills,
            mcp=mcp,
            apps=resolved_apps,
            plugins=resolved_plugins,
        )

    @classmethod
    def empty(cls) -> "RuntimeExtensionSnapshot":
        """Return a stable empty extension snapshot."""
        return cls.create(SkillSnapshot.empty(), McpSnapshot.empty())


@dataclass(frozen=True, slots=True)
class AssembledRuntime:
    """Runnable ADK objects pinned to one product identity and content snapshot.

    Static permissions may refresh inside that identity boundary before a new
    Tool Action; Model and extension catalog expansion requires reassembly.
    """

    snapshot: ConfigSnapshot
    metadata: RuntimeMetadata
    agent: Any
    runner: Any
    session_service: Any
    extension_toolsets: tuple[ManagedMcpToolset, ...] = ()
    artifact_service: Any | None = None
    permission_refresh_policy: Literal["current", "fixed", "fail_on_change"] = "current"

    async def close(self) -> None:
        """Release every connection-bearing extension toolset."""
        for toolset in self.extension_toolsets:
            await toolset.close()

    async def run_text(
        self,
        text: str,
        *,
        user_id: str,
        session_id: str,
        on_event: Callable[[Any], None] | None = None,
        on_text_update: Callable[[str, str], None] | None = None,
        run_config: RunConfig | None = None,
    ) -> str:
        """Run one text turn while retaining this runtime's snapshot revision."""
        request = types.UserContent(parts=[types.Part.from_text(text=text)])
        return await run_text_async(
            self.runner,
            on_event=on_event,
            on_text_update=on_text_update,
            user_id=user_id,
            session_id=session_id,
            new_message=request,
            run_config=run_config,
        )

    async def run_message(
        self,
        message: types.Content,
        *,
        user_id: str,
        session_id: str,
        on_event: Callable[[Any], None] | None = None,
        on_text_update: Callable[[str, str], None] | None = None,
        run_config: RunConfig | None = None,
    ) -> str:
        """Run one multimodal ADK turn from a validated user Content value."""
        return await run_text_async(
            self.runner,
            on_event=on_event,
            on_text_update=on_text_update,
            user_id=user_id,
            session_id=session_id,
            new_message=message,
            run_config=run_config,
        )

    async def continue_message(
        self,
        *,
        user_id: str,
        session_id: str,
        on_event: Callable[[Any], None] | None = None,
        on_text_update: Callable[[str, str], None] | None = None,
        run_config: RunConfig | None = None,
    ) -> str:
        """Continue the current Session in a fresh ADK invocation.

        No synthetic user message is inserted. The root Agent receives the
        durable Session history and OpenPPX long-task context through its
        normal ADK plugins, so continuation remains an ADK-native Run rather
        than a second execution engine.
        """
        return await run_text_async(
            self.runner,
            on_event=on_event,
            on_text_update=on_text_update,
            user_id=user_id,
            session_id=session_id,
            new_message=None,
            run_config=run_config,
        )


ModelFactory = Callable[[ModelResolution], Any]
AgentFactory = Callable[..., Any]
RunnerFactory = Callable[..., tuple[Any, Any]]
CurrentPermissionProvider = Callable[[str], ResolvedPermissionSnapshot]


class RuntimeAssembler:
    """Construct complete ADK runtimes from validated immutable snapshots."""

    def __init__(
        self,
        *,
        node_root: Path,
        secret_store: SecretStore,
        services: RuntimeServices | None = None,
        model_factory: ModelFactory | None = None,
        agent_factory: AgentFactory = build_root_agent,
        runner_factory: RunnerFactory = create_runner,
        skill_manager: SkillManager | None = None,
        mcp_manager: McpManager | None = None,
        app_manager: AppManager | None = None,
        plugin_manager: PluginManager | None = None,
        mcp_adapter: McpRuntimeAdapter | None = None,
        permission_snapshot_provider: CurrentPermissionProvider | None = None,
        historical_session_service: HistoricalSessionService | None = None,
    ) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)
        self.secret_store = secret_store
        self.services = services or RuntimeServices.local(self.node_root)
        adapter_factory = ModelAdapterFactory(secret_store)
        self._model_factory = model_factory or adapter_factory.build
        self._agent_factory = agent_factory
        self._runner_factory = runner_factory
        self._skill_manager = skill_manager
        self._mcp_manager = mcp_manager
        self._app_manager = app_manager
        self._plugin_manager = plugin_manager
        self._mcp_adapter = mcp_adapter or McpRuntimeAdapter(secret_store)
        self._permission_snapshot_provider = permission_snapshot_provider
        self._task_controller: Any | None = None
        identity_db_path = self.node_root / "database" / "identity.db"
        identity_store = IdentityStore(db_path=identity_db_path)
        agent_access_store = AgentAccessStore(db_path=identity_db_path)
        config_repository = FilesystemConfigRepository(self.node_root)
        self.historical_session_service = historical_session_service or HistoricalSessionService(
            session_service=self.services.session_service,
            identity_store=identity_store,
            agent_access_store=agent_access_store,
            session_metadata=SessionMetadataStore(self.node_root / "database" / "sessions.db"),
            catalog_refresher=lambda: sync_history_agent_catalog(
                repository=config_repository,
                identity_store=identity_store,
                agent_access_store=agent_access_store,
            ),
        )

    def attach_task_controller(self, task_controller: Any) -> None:
        """Attach the Node-owned task controller before assembling runtimes."""
        self._task_controller = task_controller

    def skill_snapshot_for_agent(self, agent_id: str) -> SkillSnapshot:
        """Capture the immutable Skill set used to key and assemble a Runtime."""
        if self._skill_manager is None:
            return SkillSnapshot.empty()
        return self._skill_manager.snapshot_for_agent(agent_id)

    def extension_snapshot_for_agent(self, agent_id: str) -> RuntimeExtensionSnapshot:
        """Capture all extension resources that key a newly assembled Runtime."""
        direct_skills = self.skill_snapshot_for_agent(agent_id)
        plugins = (
            PluginSnapshot.empty()
            if self._plugin_manager is None
            else self._plugin_manager.snapshot_for_agent(agent_id)
        )
        skills = merge_skill_snapshots(direct_skills, plugins.skills)
        direct_mcp = (
            McpSnapshot.empty()
            if self._mcp_manager is None
            else self._mcp_manager.snapshot_for_agent(agent_id)
        )
        apps = (
            AppSnapshot.empty()
            if self._app_manager is None
            else self._app_manager.snapshot_for_agent(agent_id)
        )
        mcp = merge_mcp_snapshots(direct_mcp, apps.mcp, plugins.mcp)
        return RuntimeExtensionSnapshot.create(skills, mcp, apps, plugins)

    def assemble(
        self,
        snapshot: ConfigSnapshot,
        *,
        extension_tools: tuple[Any, ...] = (),
        extension_snapshot: RuntimeExtensionSnapshot | None = None,
        permission_refresh_policy: Literal["current", "fixed", "fail_on_change"] = "current",
        restrict_subagent: bool = False,
    ) -> AssembledRuntime:
        """Build a snapshot-native Agent and Runner with explicit dependencies."""
        resolved_extensions = extension_snapshot or self.extension_snapshot_for_agent(
            snapshot.agent.metadata.name
        )
        permission_provider = (
            None
            if self._permission_snapshot_provider is None
            or permission_refresh_policy == "fixed"
            else lambda: self._permission_snapshot_provider(snapshot.agent.metadata.name)
        )
        permission_authority = PermissionSnapshotAuthority(
            baseline=snapshot.permissions,
            provider=permission_provider,
            required_revision=(
                snapshot.permissions.revision
                if permission_refresh_policy == "fail_on_change" and permission_provider is not None
                else None
            ),
        )
        proxy = snapshot.permissions.code_egress_proxy
        if (
            proxy is not None
            and snapshot.permissions.preset in {"medium", "high"}
            and snapshot.permissions.rollout_for("command") == "enforce"
        ):
            write_egress_proxy_policy(
                snapshot.permissions,
                policy_directory=Path(proxy.policy_directory),
            )
        mcp_build = self._mcp_adapter.build(
            resolved_extensions.mcp,
            permission_snapshot=snapshot.permissions,
            permission_audit=self.services.permission_audit,
        )
        native_app_tools = (
            ()
            if self._app_manager is None
            else self._app_manager.build_native_tools(resolved_extensions.apps)
        )
        resolved_extension_tools = (*mcp_build.toolsets, *native_app_tools, *extension_tools)
        model = self._model_factory(snapshot.model)
        agent = self._agent_factory(
            snapshot,
            model=model,
            extension_tools=resolved_extension_tools,
            include_gui_tools=False,
            skill_snapshot=resolved_extensions.skills,
            mcp_summaries=summarize_mcp_toolsets(list(mcp_build.toolsets)),
            goal_store=self.services.goal_store,
            permission_audit=self.services.permission_audit,
            permission_authority=permission_authority,
            extension_snapshot_digest=resolved_extensions.revision,
            task_controller=self._task_controller,
            history_tools=build_history_tools(self.historical_session_service),
        )
        if restrict_subagent:
            from .subagent_agent import build_restricted_subagent

            agent = build_restricted_subagent(agent)
        runner, session_service = self._runner_factory(
            agent=agent,
            app_name=agent.name,
            profile="snapshot",
            session_service=self.services.session_service,
            memory_service=self.services.memory_service,
            artifact_service=self.services.artifact_service,
            task_store=self.services.task_store,
            context_store=self.services.context_store,
            goal_store=self.services.goal_store,
            extra_plugins=(
                OpenPpxAuthorizationPlugin(
                    snapshot.permissions,
                    audit=self.services.permission_audit,
                    authority=permission_authority,
                    fixed_network_policies=dict(mcp_build.network_policies),
                ),
                *(
                    ()
                    if not resolved_extensions.plugins.hooks.entries
                    else (
                        OpenPpxPluginHookBridge(
                            resolved_extensions.plugins.hooks,
                            workspace=Path(snapshot.agent.spec.workspace),
                            root_agent_name=agent.name,
                        ),
                    )
                ),
            ),
        )
        metadata = RuntimeMetadata(
            node_id=snapshot.node.metadata.name,
            agent_id=snapshot.agent.metadata.name,
            model_profile_id=snapshot.model.profile_id,
            model_profile_revision=snapshot.model.revision,
            provider=snapshot.model.provider,
            model=snapshot.model.model,
            workspace=snapshot.agent.spec.workspace,
            snapshot_revision=snapshot.revision,
            permission_revision=snapshot.permissions.revision,
            extension_revision=resolved_extensions.revision,
            mcp_diagnostics=tuple(
                (diagnostic.server_id, diagnostic.code) for diagnostic in mcp_build.diagnostics
            ),
            origin_revisions=tuple(
                (origin.resource_id, origin.revision) for origin in snapshot.origins
            ),
        )
        return AssembledRuntime(
            snapshot=snapshot,
            metadata=metadata,
            agent=agent,
            runner=runner,
            session_service=session_service,
            artifact_service=self.services.artifact_service,
            extension_toolsets=mcp_build.toolsets,
            permission_refresh_policy=permission_refresh_policy,
        )

    def assemble_subagent(
        self,
        snapshot: ConfigSnapshot,
        *,
        extension_snapshot: RuntimeExtensionSnapshot | None = None,
    ) -> AssembledRuntime:
        """Build a restricted ADK worker pinned to the spawn-time permissions."""
        return self.assemble(
            snapshot,
            extension_snapshot=extension_snapshot,
            permission_refresh_policy="fail_on_change",
            restrict_subagent=True,
        )


__all__ = [
    "AssembledRuntime",
    "RuntimeAssembler",
    "RuntimeMetadata",
    "RuntimeExtensionSnapshot",
    "RuntimeServices",
]
