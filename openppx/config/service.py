"""Typed validation, preview, mutation, effect, and snapshot service."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Callable, Generic, Mapping, Sequence, TypeVar

from pydantic import BaseModel, ValidationError

from openppx.modeling.repository import ModelProfileRepository
from openppx.modeling.selection import ModelProfileSelector, ModelRequirements
from openppx.permissions import (
    AgentWorkspaceBoundary,
    PermissionChange,
    compile_permission_snapshot,
    diff_permission_snapshots,
)

from .diagnostics import ConfigDiagnostics, ConfigIssue, ConfigLoadError, ConfigRevisionConflict, validation_issues
from .layers import ConfigOrigin, ConfigSnapshot
from .models import AgentConfig, NodeConfig
from .repository import FilesystemConfigRepository, VersionedResource
from .revision import config_revision


DocumentT = TypeVar("DocumentT", bound=BaseModel)


class ConfigEffect(str, Enum):
    """Strongest lifecycle boundary required by a set of Config changes."""

    LIVE = "live"
    NEXT_RUN = "next_run"
    RESTART_REQUIRED = "restart_required"


@dataclass(frozen=True, slots=True)
class ConfigChange:
    """One redacted structural change without before/after values."""

    path: tuple[str | int, ...]
    change_kind: str


@dataclass(frozen=True, slots=True)
class ValidationResult(Generic[DocumentT]):
    """Non-raising strict validation result with redacted diagnostics."""

    ok: bool
    document: DocumentT | None
    diagnostics: ConfigDiagnostics


@dataclass(frozen=True, slots=True)
class ConfigPreview:
    """Pure mutation preview based on one explicit repository revision."""

    base_revision: str | None
    candidate_revision: str
    changes: tuple[ConfigChange, ...]
    effect: ConfigEffect
    candidate_permission_revision: str | None = None
    permission_changes: tuple[PermissionChange, ...] = ()


@dataclass(frozen=True, slots=True)
class ConfigApplyResult(Generic[DocumentT]):
    """Persisted resource and the exact diff/effect used before mutation."""

    resource: VersionedResource[DocumentT]
    changes: tuple[ConfigChange, ...]
    effect: ConfigEffect


class ConfigService:
    """Application-neutral Config management core for future control surfaces."""

    def __init__(
        self,
        repository: FilesystemConfigRepository,
        profiles: ModelProfileRepository,
        selector: ModelProfileSelector,
    ) -> None:
        self.repository = repository
        self.profiles = profiles
        self.selector = selector

    def validate_node(self, raw: Mapping[str, object]) -> ValidationResult[NodeConfig]:
        """Validate a raw Node payload without writing or mutating runtime state."""
        return self._validate(raw, NodeConfig, source="node")

    def validate_agent(
        self,
        raw: Mapping[str, object],
        *,
        agent_id: str | None = None,
    ) -> ValidationResult[AgentConfig]:
        """Validate a raw Agent payload without writing or mutating runtime state."""
        source = f"agent:{agent_id}" if agent_id is not None else "agent"
        result = self._validate(raw, AgentConfig, source=source)
        if result.document is None or agent_id is None or result.document.metadata.name == agent_id:
            return result
        issue = ConfigIssue(
            "name_mismatch",
            ("metadata", "name"),
            "Agent metadata.name must match its resource path.",
            source,
        )
        return ValidationResult(
            ok=False,
            document=None,
            diagnostics=ConfigDiagnostics(
                ok=False,
                source=source,
                issues=(issue,),
                error_kind="name_mismatch",
            ),
        )

    def preview_node(self, candidate: NodeConfig, *, expected_revision: str | None) -> ConfigPreview:
        """Return the structural Node diff and effect without persistence."""
        current = self._optional_current(self.repository.read_node)
        return self._preview(
            candidate,
            current=current,
            expected_revision=expected_revision,
            source="node",
            path=self.repository.paths.node_file,
            resource_kind="node",
        )

    def preview_agent(
        self,
        agent_id: str,
        candidate: AgentConfig,
        *,
        expected_revision: str | None,
    ) -> ConfigPreview:
        """Return the structural Agent diff and effect without persistence."""
        self._require_agent_identity(agent_id, candidate)
        current = self._optional_current(lambda: self.repository.read_agent(agent_id))
        preview = self._preview(
            candidate,
            current=current,
            expected_revision=expected_revision,
            source=f"agent:{agent_id}",
            path=self.repository.paths.agent_file(agent_id),
            resource_kind="agent",
        )
        node = self._optional_current(self.repository.read_node)
        if node is None:
            return preview
        candidate_permissions = compile_permission_snapshot(
            node=node.document,
            agent=candidate,
            agent_workspaces=self._agent_workspace_boundaries(override=candidate),
            source_revisions={
                node.resource_id: node.revision,
                f"agent/{agent_id}": config_revision(candidate),
            },
        )
        permission_changes: tuple[PermissionChange, ...] = ()
        if current is not None:
            current_permissions = compile_permission_snapshot(
                node=node.document,
                agent=current.document,
                agent_workspaces=self._agent_workspace_boundaries(),
                source_revisions={
                    node.resource_id: node.revision,
                    current.resource_id: current.revision,
                },
            )
            permission_changes = diff_permission_snapshots(current_permissions, candidate_permissions)
        return replace(
            preview,
            candidate_permission_revision=candidate_permissions.revision,
            permission_changes=permission_changes,
        )

    def apply_node(self, candidate: NodeConfig, *, expected_revision: str | None) -> ConfigApplyResult[NodeConfig]:
        """Preview then atomically persist a Node resource."""
        preview = self.preview_node(candidate, expected_revision=expected_revision)
        resource = self.repository.write_node(candidate, expected_revision=expected_revision)
        return ConfigApplyResult(resource=resource, changes=preview.changes, effect=preview.effect)

    def apply_agent(
        self,
        agent_id: str,
        candidate: AgentConfig,
        *,
        expected_revision: str | None,
    ) -> ConfigApplyResult[AgentConfig]:
        """Preview then atomically persist an Agent resource."""
        preview = self.preview_agent(agent_id, candidate, expected_revision=expected_revision)
        resource = self.repository.write_agent(agent_id, candidate, expected_revision=expected_revision)
        return ConfigApplyResult(resource=resource, changes=preview.changes, effect=preview.effect)

    def snapshot(
        self,
        agent_id: str,
        *,
        role: str | None = None,
        run_override: str | None = None,
        requirements: ModelRequirements | None = None,
    ) -> ConfigSnapshot:
        """Compose a deterministic immutable snapshot from current resource revisions."""
        node = self.repository.read_node()
        agent = self.repository.read_agent(agent_id)
        model = self.selector.select(
            agent.document,
            role=role,
            run_override=run_override,
            requirements=requirements,
        )
        origins = (
            ConfigOrigin(node.resource_id, node.revision),
            ConfigOrigin(agent.resource_id, agent.revision),
            ConfigOrigin(f"model-profile/{model.profile_id}", model.revision),
        )
        permissions = compile_permission_snapshot(
            node=node.document,
            agent=agent.document,
            source_revisions={origin.resource_id: origin.revision for origin in origins},
            agent_workspaces=self._agent_workspace_boundaries(),
        )
        payload = json.dumps(
            {
                "origins": [
                    {"resourceId": origin.resource_id, "revision": origin.revision}
                    for origin in origins
                ],
                "permissionRevision": permissions.revision,
            },
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        revision = f"sha256:{hashlib.sha256(payload).hexdigest()}"
        return ConfigSnapshot(
            node=node.document,
            agent=agent.document,
            model=model,
            permissions=permissions,
            origins=origins,
            revision=revision,
        )

    def _agent_workspace_boundaries(
        self,
        *,
        override: AgentConfig | None = None,
    ) -> tuple[AgentWorkspaceBoundary, ...]:
        """Return trusted Workspace ownership facts from versioned Agent resources."""

        agent_ids = set(self.repository.list_agent_ids())
        if override is not None:
            agent_ids.add(override.metadata.name)
        boundaries: list[AgentWorkspaceBoundary] = []
        for agent_id in sorted(agent_ids):
            document = (
                override
                if override is not None and override.metadata.name == agent_id
                else self.repository.read_agent(agent_id).document
            )
            boundaries.append(
                AgentWorkspaceBoundary(
                    agent_id=document.metadata.name,
                    privilege_level=document.spec.privilege_level,
                    workspace=document.spec.workspace,
                )
            )
        return tuple(boundaries)

    def _require_agent_identity(self, agent_id: str, candidate: AgentConfig) -> None:
        """Reject a path/resource identity mismatch before preview or persistence."""
        if candidate.metadata.name == agent_id:
            return
        source = f"agent:{agent_id}"
        issue = ConfigIssue(
            "name_mismatch",
            ("metadata", "name"),
            "Agent metadata.name must match its resource path.",
            source,
        )
        raise ConfigLoadError(
            self.repository.paths.agent_file(agent_id),
            "name_mismatch",
            "Agent identity does not match its path",
            (issue,),
        )

    @staticmethod
    def _validate(
        raw: Mapping[str, object],
        model_type: type[DocumentT],
        *,
        source: str,
    ) -> ValidationResult[DocumentT]:
        try:
            document = model_type.model_validate(raw)
        except ValidationError as exc:
            diagnostics = ConfigDiagnostics(
                ok=False,
                source=source,
                issues=validation_issues(exc, source=source),
                error_kind="invalid_schema",
            )
            return ValidationResult(ok=False, document=None, diagnostics=diagnostics)
        revision = config_revision(document)
        return ValidationResult(
            ok=True,
            document=document,
            diagnostics=ConfigDiagnostics(ok=True, source=source, revision=revision),
        )

    @staticmethod
    def _optional_current(
        reader: Callable[[], VersionedResource[DocumentT]],
    ) -> VersionedResource[DocumentT] | None:
        try:
            return reader()
        except ConfigLoadError as exc:
            if exc.kind == "not_found":
                return None
            raise

    @staticmethod
    def _preview(
        candidate: DocumentT,
        *,
        current: VersionedResource[BaseModel] | None,
        expected_revision: str | None,
        source: str,
        path: Path,
        resource_kind: str,
    ) -> ConfigPreview:
        actual_revision = current.revision if current is not None else None
        if actual_revision != expected_revision:
            raise ConfigRevisionConflict(
                path,  # type: ignore[arg-type]
                source=source,
                expected_revision=expected_revision,
                actual_revision=actual_revision,
            )
        before = current.document.model_dump(mode="json", by_alias=True) if current is not None else {}
        after = candidate.model_dump(mode="json", by_alias=True)
        changes = tuple(_structural_diff(before, after))
        effect = _effect_for_changes(resource_kind, changes)
        return ConfigPreview(
            base_revision=actual_revision,
            candidate_revision=config_revision(candidate),
            changes=changes,
            effect=effect,
        )


def _structural_diff(
    before: object,
    after: object,
    path: tuple[str | int, ...] = (),
) -> list[ConfigChange]:
    """Return deterministic structural changes without retaining changed values."""
    if isinstance(before, dict) and isinstance(after, dict):
        changes: list[ConfigChange] = []
        for key in sorted(before.keys() - after.keys()):
            changes.extend(_leaf_changes(before[key], (*path, key), "removed"))
        for key in sorted(after.keys() - before.keys()):
            changes.extend(_leaf_changes(after[key], (*path, key), "added"))
        for key in sorted(before.keys() & after.keys()):
            changes.extend(_structural_diff(before[key], after[key], (*path, key)))
        return changes
    if isinstance(before, list) and isinstance(after, list):
        return [] if before == after else [ConfigChange(path, "changed")]
    return [] if before == after else [ConfigChange(path, "changed")]


def _leaf_changes(value: object, path: tuple[str | int, ...], change_kind: str) -> list[ConfigChange]:
    """Expand added/removed objects so lifecycle effects see changed leaf paths."""
    if isinstance(value, dict):
        changes: list[ConfigChange] = []
        for key in sorted(value):
            changes.extend(_leaf_changes(value[key], (*path, key), change_kind))
        return changes or [ConfigChange(path, change_kind)]
    return [ConfigChange(path, change_kind)]


_EFFECT_ORDER = {
    ConfigEffect.LIVE: 0,
    ConfigEffect.NEXT_RUN: 1,
    ConfigEffect.RESTART_REQUIRED: 2,
}


def _effect_for_changes(resource_kind: str, changes: Sequence[ConfigChange]) -> ConfigEffect:
    effect = ConfigEffect.LIVE
    for change in changes:
        candidate = _effect_for_path(resource_kind, change.path)
        if _EFFECT_ORDER[candidate] > _EFFECT_ORDER[effect]:
            effect = candidate
    return effect


def _effect_for_path(resource_kind: str, path: tuple[str | int, ...]) -> ConfigEffect:
    if resource_kind == "node":
        if path[:2] == ("spec", "clientApi"):
            return ConfigEffect.RESTART_REQUIRED
        if path[:2] == ("spec", "enabledAgents"):
            return ConfigEffect.NEXT_RUN
        if path[:2] == ("spec", "permissions"):
            return ConfigEffect.NEXT_RUN
        return ConfigEffect.LIVE
    next_run_paths = {
        ("spec", "workspace"),
        ("spec", "ownerPrincipalId"),
        ("spec", "privilegeLevel"),
        ("spec", "permissionOverrides"),
        ("spec", "permissions"),
        ("spec", "modelPolicy"),
    }
    if path[:2] in next_run_paths:
        return ConfigEffect.NEXT_RUN
    return ConfigEffect.LIVE
