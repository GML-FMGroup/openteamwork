"""Long-term strict configuration boundary for OpenPPX."""

from .diagnostics import (
    ConfigDiagnostics,
    ConfigErrorKind,
    ConfigIssue,
    ConfigLoadError,
    read_json_object,
    validation_issues,
)
from .models import AgentConfig, NodeConfig, ResourceName
from .repository import ConfigRepository, ConfigSource, FilesystemConfigRepository, VersionedResource
from .revision import config_revision
from .schema import export_config_schemas

__all__ = [
    "AgentConfig",
    "ConfigDiagnostics",
    "ConfigErrorKind",
    "ConfigIssue",
    "ConfigLoadError",
    "ConfigRepository",
    "ConfigSource",
    "FilesystemConfigRepository",
    "NodeConfig",
    "ResourceName",
    "VersionedResource",
    "config_revision",
    "export_config_schemas",
    "read_json_object",
    "validation_issues",
]
