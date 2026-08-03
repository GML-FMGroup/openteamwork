"""Read-only filesystem repository for strict configuration resources."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Generic, Literal, Protocol, TypeVar

from pydantic import BaseModel, ValidationError

from .diagnostics import (
    ConfigDiagnostics,
    ConfigIssue,
    ConfigLoadError,
    read_json_object,
    validation_issues,
)
from .models import AgentConfig, NodeConfig
from .paths import ConfigPaths
from .revision import config_revision


ResourceT = TypeVar("ResourceT", bound=BaseModel)


@dataclass(frozen=True, slots=True)
class ConfigSource:
    """Filesystem provenance for one loaded configuration resource."""

    kind: Literal["defaults", "node_file", "agent_file"]
    path: Path


@dataclass(frozen=True, slots=True)
class VersionedResource(Generic[ResourceT]):
    """Validated resource plus its content identity and provenance."""

    resource_id: str
    document: ResourceT
    revision: str
    source: ConfigSource
    loaded_at: datetime
    diagnostics: ConfigDiagnostics


class ConfigRepository(Protocol):
    """Read boundary shared by filesystem and future remote repositories."""

    def read_node(self) -> VersionedResource[NodeConfig]: ...

    def read_agent(self, agent_id: str) -> VersionedResource[AgentConfig]: ...

    def list_agent_ids(self) -> tuple[str, ...]: ...

    def diagnose_node(self) -> ConfigDiagnostics: ...

    def diagnose_agent(self, agent_id: str) -> ConfigDiagnostics: ...


class FilesystemConfigRepository:
    """Load strict Node and Agent resources from one explicit Node root."""

    def __init__(self, node_root: Path) -> None:
        self.paths = ConfigPaths(node_root)

    def read_node(self) -> VersionedResource[NodeConfig]:
        """Load and validate the Node resource without runtime side effects."""
        source = ConfigSource("node_file", self.paths.node_file)
        document = self._read_model(source, "node", NodeConfig)
        return self._versioned(f"node/{document.metadata.name}", document, source)

    def read_agent(self, agent_id: str) -> VersionedResource[AgentConfig]:
        """Load and validate one Agent resource and enforce path identity."""
        path = self.paths.agent_file(agent_id)
        source = ConfigSource("agent_file", path)
        document = self._read_model(source, f"agent:{agent_id}", AgentConfig)
        if document.metadata.name != agent_id:
            issue = ConfigIssue(
                "name_mismatch",
                ("metadata", "name"),
                "Agent metadata.name must match its resource path.",
                f"agent:{agent_id}",
            )
            raise ConfigLoadError(path, "name_mismatch", "Agent identity does not match its path", (issue,))
        return self._versioned(f"agent/{agent_id}", document, source)

    def list_agent_ids(self) -> tuple[str, ...]:
        """Return sorted IDs for every valid Agent resource under the Node root."""
        agents_dir = self.paths.agents_dir
        if not agents_dir.exists():
            return ()
        try:
            entries = sorted((entry for entry in agents_dir.iterdir() if entry.is_dir()), key=lambda item: item.name)
        except OSError as exc:
            issue = ConfigIssue("io_error", (), "Agent configuration directory could not be read.", "agents")
            raise ConfigLoadError(
                agents_dir,
                "io_error",
                "Agent configuration directory could not be read",
                (issue,),
            ) from exc
        identifiers: list[str] = []
        for entry in entries:
            self.read_agent(entry.name)
            identifiers.append(entry.name)
        return tuple(identifiers)

    def diagnose_node(self) -> ConfigDiagnostics:
        """Return a non-raising diagnostic snapshot for the Node resource."""
        return self._diagnose("node", self.read_node)

    def diagnose_agent(self, agent_id: str) -> ConfigDiagnostics:
        """Return a non-raising diagnostic snapshot for one Agent resource."""
        return self._diagnose(f"agent:{agent_id}", lambda: self.read_agent(agent_id))

    @staticmethod
    def _read_model(
        source: ConfigSource,
        diagnostic_source: str,
        model_type: type[ResourceT],
    ) -> ResourceT:
        raw = read_json_object(source.path, source=diagnostic_source)
        try:
            return model_type.model_validate(raw)
        except ValidationError as exc:
            issues = validation_issues(exc, source=diagnostic_source)
            raise ConfigLoadError(
                source.path,
                "invalid_schema",
                "Configuration resource does not match its schema",
                issues,
            ) from exc

    @staticmethod
    def _versioned(
        resource_id: str,
        document: ResourceT,
        source: ConfigSource,
    ) -> VersionedResource[ResourceT]:
        revision = config_revision(document)
        diagnostics = ConfigDiagnostics(
            ok=True,
            source=resource_id,
            revision=revision,
        )
        return VersionedResource(
            resource_id=resource_id,
            document=document,
            revision=revision,
            source=source,
            loaded_at=datetime.now(timezone.utc),
            diagnostics=diagnostics,
        )

    @staticmethod
    def _diagnose(
        source: str,
        reader: Callable[[], VersionedResource[ResourceT]],
    ) -> ConfigDiagnostics:
        try:
            resource = reader()
        except ConfigLoadError as exc:
            return ConfigDiagnostics(
                ok=False,
                source=source,
                issues=exc.issues,
                error_kind=exc.kind,
            )
        return resource.diagnostics
