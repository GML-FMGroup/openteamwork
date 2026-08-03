"""Immutable product Config to Google ADK runtime assembly boundary."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from google.genai import types

from openppx.app.agent import build_root_agent
from openppx.config import ConfigSnapshot, SecretStore
from openppx.extensions import McpManager, McpSnapshot, SkillManager, SkillSnapshot
from openppx.modeling import ModelResolution
from openppx.core.mcp_registry import ManagedMcpToolset, summarize_mcp_toolsets

from .adk_utils import run_text_async
from .artifact_service import ArtifactConfig, create_artifact_service
from .context_engine import LongTaskContextStore
from .memory_service import MemoryConfig, create_memory_service
from .mcp_adapter import McpRuntimeAdapter
from .model_adapter_factory import ModelAdapterFactory
from .runner_factory import create_runner
from .session_service import SessionConfig, create_session_service
from .task_store import TaskStore


@dataclass(frozen=True, slots=True)
class RuntimeMetadata:
    """Non-sensitive provenance attached to one assembled runtime."""

    node_id: str
    agent_id: str
    model_profile_id: str
    provider: str
    model: str
    workspace: str
    snapshot_revision: str
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
        return cls(
            session_service=session_service,
            memory_service=memory_service,
            artifact_service=artifact_service,
            task_store=task_store,
            context_store=context_store,
        )


@dataclass(frozen=True, slots=True)
class RuntimeExtensionSnapshot:
    """Immutable Skill and direct MCP resources used by one Runtime."""

    revision: str
    skills: SkillSnapshot
    mcp: McpSnapshot

    @classmethod
    def create(cls, skills: SkillSnapshot, mcp: McpSnapshot) -> "RuntimeExtensionSnapshot":
        """Combine child revisions into one deterministic cache identity."""
        canonical = json.dumps(
            {"skills": skills.revision, "mcp": mcp.revision},
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return cls(
            revision=f"sha256:{hashlib.sha256(canonical).hexdigest()}",
            skills=skills,
            mcp=mcp,
        )

    @classmethod
    def empty(cls) -> "RuntimeExtensionSnapshot":
        """Return a stable empty extension snapshot."""
        return cls.create(SkillSnapshot.empty(), McpSnapshot.empty())


@dataclass(frozen=True, slots=True)
class AssembledRuntime:
    """Runnable ADK objects pinned to one immutable product snapshot."""

    snapshot: ConfigSnapshot
    metadata: RuntimeMetadata
    agent: Any
    runner: Any
    session_service: Any
    extension_toolsets: tuple[ManagedMcpToolset, ...] = ()

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
        )


ModelFactory = Callable[[ModelResolution], Any]
AgentFactory = Callable[..., Any]
RunnerFactory = Callable[..., tuple[Any, Any]]


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
        mcp_adapter: McpRuntimeAdapter | None = None,
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
        self._mcp_adapter = mcp_adapter or McpRuntimeAdapter(secret_store)

    def skill_snapshot_for_agent(self, agent_id: str) -> SkillSnapshot:
        """Capture the immutable Skill set used to key and assemble a Runtime."""
        if self._skill_manager is None:
            return SkillSnapshot.empty()
        return self._skill_manager.snapshot_for_agent(agent_id)

    def extension_snapshot_for_agent(self, agent_id: str) -> RuntimeExtensionSnapshot:
        """Capture all extension resources that key a newly assembled Runtime."""
        skills = self.skill_snapshot_for_agent(agent_id)
        mcp = McpSnapshot.empty() if self._mcp_manager is None else self._mcp_manager.snapshot_for_agent(agent_id)
        return RuntimeExtensionSnapshot.create(skills, mcp)

    def assemble(
        self,
        snapshot: ConfigSnapshot,
        *,
        extension_tools: tuple[Any, ...] = (),
        extension_snapshot: RuntimeExtensionSnapshot | None = None,
    ) -> AssembledRuntime:
        """Build a snapshot-native Agent and Runner with explicit dependencies."""
        resolved_extensions = extension_snapshot or self.extension_snapshot_for_agent(
            snapshot.agent.metadata.name
        )
        mcp_build = self._mcp_adapter.build(resolved_extensions.mcp)
        resolved_extension_tools = (*mcp_build.toolsets, *extension_tools)
        model = self._model_factory(snapshot.model)
        agent = self._agent_factory(
            snapshot,
            model=model,
            extension_tools=resolved_extension_tools,
            include_gui_tools=False,
            skill_snapshot=resolved_extensions.skills,
            mcp_summaries=summarize_mcp_toolsets(list(mcp_build.toolsets)),
        )
        runner, session_service = self._runner_factory(
            agent=agent,
            app_name=agent.name,
            profile="snapshot",
            session_service=self.services.session_service,
            memory_service=self.services.memory_service,
            artifact_service=self.services.artifact_service,
            task_store=self.services.task_store,
            context_store=self.services.context_store,
        )
        metadata = RuntimeMetadata(
            node_id=snapshot.node.metadata.name,
            agent_id=snapshot.agent.metadata.name,
            model_profile_id=snapshot.model.profile_id,
            provider=snapshot.model.provider,
            model=snapshot.model.model,
            workspace=snapshot.agent.spec.workspace,
            snapshot_revision=snapshot.revision,
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
            extension_toolsets=mcp_build.toolsets,
        )


__all__ = [
    "AssembledRuntime",
    "RuntimeAssembler",
    "RuntimeMetadata",
    "RuntimeExtensionSnapshot",
    "RuntimeServices",
]
