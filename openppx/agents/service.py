"""Safe publication of new Agent resources into one OpenPPX Node."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openppx.config import (
    AgentConfig,
    ConfigLoadError,
    ConfigService,
    FilesystemConfigRepository,
    VersionedResource,
    config_revision,
)
from openppx.modeling import ModelProfileRepository


class AgentLifecycleError(RuntimeError):
    """Stable Agent lifecycle error suitable for an Action boundary."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class AgentCreateResult:
    """Published Agent plus the exact Node revision that made it visible."""

    agent: VersionedResource[AgentConfig]
    node_revision: str
    workspace: Path


@dataclass(frozen=True, slots=True)
class AgentMutationResult:
    """Updated Agent resource and the Node revision controlling enablement."""

    agent: VersionedResource[AgentConfig]
    enabled: bool
    node_revision: str
    effect: str = "next_run"


@dataclass(frozen=True, slots=True)
class AgentDeleteResult:
    """Recoverable Agent removal result that never deletes its workspace."""

    agent_id: str
    workspace: Path
    archive_path: Path
    node_revision: str


class AgentLifecycleService:
    """Create Agents through inactive-resource staging and Node publication."""

    def __init__(
        self,
        repository: FilesystemConfigRepository,
        config_service: ConfigService,
        profiles: ModelProfileRepository,
    ) -> None:
        self.repository = repository
        self.config_service = config_service
        self.profiles = profiles

    def create(
        self,
        *,
        agent_id: str,
        display_name: str,
        owner_principal_id: str,
        privilege_level: str,
        model_profile_id: str,
        workspace: str | None = None,
        instruction: str = "",
    ) -> AgentCreateResult:
        """Create and publish one Agent without exposing partial state as runnable."""
        node = self._read_node()
        if agent_id in node.document.spec.enabled_agents:
            raise AgentLifecycleError("agent_exists", "An enabled Agent already uses this ID.")
        self._require_profile(model_profile_id)
        resolved_workspace = self._resolve_workspace(agent_id, workspace)
        candidate = AgentConfig.model_validate(
            {
                "apiVersion": "openppx.io/v1alpha1",
                "kind": "AgentConfig",
                "metadata": {"name": agent_id},
                "spec": {
                    "displayName": display_name,
                    "workspace": str(resolved_workspace),
                    "instruction": instruction,
                    "ownerPrincipalId": owner_principal_id,
                    "privilegeLevel": privilege_level,
                    "controls": {},
                    "modelPolicy": {
                        "defaultProfile": model_profile_id,
                        "roleProfiles": {},
                    },
                },
            }
        )
        agent = self._stage_agent(candidate)
        self._ensure_workspace(resolved_workspace)

        enabled_agents = [*node.document.spec.enabled_agents, agent_id]
        node_candidate = node.document.model_copy(
            update={
                "spec": node.document.spec.model_copy(
                    update={"enabled_agents": enabled_agents}
                )
            }
        )
        published = self.config_service.apply_node(
            node_candidate,
            expected_revision=node.revision,
        )
        return AgentCreateResult(
            agent=agent,
            node_revision=published.resource.revision,
            workspace=resolved_workspace,
        )

    def list(self) -> tuple[AgentMutationResult, ...]:
        """List every configured Agent, including resources disabled on this Node."""
        node = self._read_node()
        enabled = frozenset(node.document.spec.enabled_agents)
        return tuple(
            AgentMutationResult(
                agent=resource,
                enabled=agent_id in enabled,
                node_revision=node.revision,
                effect="none",
            )
            for agent_id in self.repository.list_agent_ids()
            for resource in (self.repository.read_agent(agent_id),)
        )

    def update(
        self,
        *,
        agent_id: str,
        display_name: str,
        workspace: str,
        privilege_level: str,
        model_profile_id: str,
        instruction: str,
        expected_revision: str,
    ) -> AgentMutationResult:
        """Update editable Agent policy while preserving identity and ownership."""
        current = self.repository.read_agent(agent_id)
        self._require_profile(model_profile_id)
        resolved_workspace = self._resolve_workspace(agent_id, workspace)
        self._ensure_workspace(resolved_workspace)
        candidate = current.document.model_copy(
            update={
                "spec": current.document.spec.model_copy(
                    update={
                        "display_name": display_name,
                        "workspace": str(resolved_workspace),
                        "instruction": instruction,
                        "privilege_level": privilege_level,
                        "model_policy": current.document.spec.model_policy.model_copy(
                            update={"default_profile": model_profile_id}
                        ),
                    }
                )
            }
        )
        updated = self.config_service.apply_agent(
            agent_id,
            candidate,
            expected_revision=expected_revision,
        ).resource
        node = self._read_node()
        return AgentMutationResult(
            agent=updated,
            enabled=agent_id in node.document.spec.enabled_agents,
            node_revision=node.revision,
        )

    def set_enabled(self, *, agent_id: str, enabled: bool) -> AgentMutationResult:
        """Publish or withdraw one configured Agent without deleting its data."""
        agent = self.repository.read_agent(agent_id)
        node = self._read_node()
        current = list(node.document.spec.enabled_agents)
        is_enabled = agent_id in current
        if enabled == is_enabled:
            return AgentMutationResult(
                agent=agent,
                enabled=enabled,
                node_revision=node.revision,
                effect="none",
            )
        next_enabled = [*current, agent_id] if enabled else [item for item in current if item != agent_id]
        candidate = node.document.model_copy(
            update={"spec": node.document.spec.model_copy(update={"enabled_agents": next_enabled})}
        )
        applied = self.config_service.apply_node(candidate, expected_revision=node.revision)
        return AgentMutationResult(
            agent=agent,
            enabled=enabled,
            node_revision=applied.resource.revision,
        )

    def delete(self, *, agent_id: str, expected_revision: str) -> AgentDeleteResult:
        """Archive one disabled Agent config while retaining workspace and runtime data."""
        agent = self.repository.read_agent(agent_id)
        node = self._read_node()
        if agent_id in node.document.spec.enabled_agents:
            raise AgentLifecycleError(
                "agent_must_be_disabled",
                "Disable the Agent before removing its configuration.",
            )
        archive_path = self.repository.archive_agent(agent_id, expected_revision=expected_revision)
        return AgentDeleteResult(
            agent_id=agent_id,
            workspace=Path(agent.document.spec.workspace),
            archive_path=archive_path,
            node_revision=node.revision,
        )

    def _read_node(self):
        try:
            return self.repository.read_node()
        except ConfigLoadError as exc:
            if exc.kind == "not_found":
                raise AgentLifecycleError(
                    "node_not_configured",
                    "Configure the OpenPPX Node before creating another Agent.",
                ) from None
            raise

    def _require_profile(self, profile_id: str) -> None:
        try:
            profile = self.profiles.read_profile(profile_id)
        except ConfigLoadError as exc:
            if exc.kind == "not_found":
                raise AgentLifecycleError(
                    "model_profile_not_found",
                    "The selected Model Profile does not exist on this Node.",
                ) from None
            raise
        if not profile.document.spec.enabled:
            raise AgentLifecycleError(
                "model_profile_disabled",
                "The selected Model Profile is disabled.",
            )

    def _resolve_workspace(self, agent_id: str, workspace: str | None) -> Path:
        if workspace is None or not workspace.strip():
            return self.repository.paths.node_root / "workspaces" / agent_id
        resolved = Path(workspace).expanduser()
        if not resolved.is_absolute():
            raise AgentLifecycleError(
                "workspace_not_absolute",
                "A custom Agent workspace must be an absolute path on the Node.",
            )
        return resolved

    @staticmethod
    def _ensure_workspace(workspace: Path) -> None:
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise AgentLifecycleError(
                "workspace_unavailable",
                "The Agent workspace could not be created on the Node.",
            ) from exc
        if not workspace.is_dir():
            raise AgentLifecycleError(
                "workspace_unavailable",
                "The Agent workspace path is not a directory.",
            )

    def _stage_agent(self, candidate: AgentConfig) -> VersionedResource[AgentConfig]:
        agent_id = candidate.metadata.name
        try:
            current = self.repository.read_agent(agent_id)
        except ConfigLoadError as exc:
            if exc.kind != "not_found":
                raise
            current = None
        if current is not None:
            if current.revision != config_revision(candidate):
                raise AgentLifecycleError(
                    "agent_id_conflict",
                    "An unpublished Agent resource already uses this ID with different settings.",
                )
            return current
        return self.config_service.apply_agent(
            agent_id,
            candidate,
            expected_revision=None,
        ).resource
