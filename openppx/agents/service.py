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
                    "ownerPrincipalId": owner_principal_id,
                    "privilegeLevel": privilege_level,
                    "permissionOverrides": {},
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
