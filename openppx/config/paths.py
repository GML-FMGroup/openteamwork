"""Controlled filesystem paths for Node-owned configuration resources."""

from __future__ import annotations

import re
from pathlib import Path
from typing import NoReturn

from .diagnostics import ConfigIssue, ConfigLoadError


_RESOURCE_NAME_PATTERN = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


class ConfigPaths:
    """Resolve resource files below one explicit Node root."""

    def __init__(self, node_root: Path) -> None:
        self.node_root = node_root.expanduser().resolve(strict=False)

    @property
    def node_file(self) -> Path:
        """Return the Node configuration file path."""
        return self._contained(self.node_root / "node.json", source="node")

    @property
    def agents_dir(self) -> Path:
        """Return the controlled Agent resource directory."""
        return self._contained(self.node_root / "agents", source="agents")

    def agent_file(self, agent_id: str) -> Path:
        """Return a contained Agent file path for a validated resource name."""
        source = f"agent:{agent_id}" if _RESOURCE_NAME_PATTERN.fullmatch(agent_id) else "agent"
        if not _RESOURCE_NAME_PATTERN.fullmatch(agent_id):
            self._outside_error(source)
        return self._contained(self.node_root / "agents" / agent_id / "agent.json", source=source)

    def _contained(self, candidate: Path, *, source: str) -> Path:
        resolved = candidate.resolve(strict=False)
        if not resolved.is_relative_to(self.node_root):
            self._outside_error(source)
        return resolved

    def _outside_error(self, source: str) -> NoReturn:
        issue = ConfigIssue(
            "path_outside_root",
            (),
            "Configuration path must remain inside the Node root.",
            source,
        )
        raise ConfigLoadError(
            self.node_root,
            "path_outside_root",
            "Configuration path is outside the Node root",
            (issue,),
        )
