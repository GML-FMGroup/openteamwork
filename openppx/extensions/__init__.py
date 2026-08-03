"""OpenPPX Extension Platform domain boundary."""

from .errors import ExtensionError
from .apps import (
    AppManager,
    AppReadiness,
    AppSnapshot,
    AppSnapshotEntry,
    VersionedAppConnection,
    VersionedAppDefinition,
)
from .mcp import (
    McpManager,
    McpReadiness,
    McpSnapshot,
    McpSnapshotEntry,
    VersionedMcp,
    merge_mcp_snapshots,
)
from .mcp_models import McpServer
from .plugin_models import PluginManifest, PluginRecord
from .plugins import (
    PluginManager,
    PluginPreview,
    PluginReadiness,
    PluginSnapshot,
    PluginSnapshotEntry,
    StagedPlugin,
    VersionedPlugin,
    parse_plugin_manifest,
)
from .registry import ExtensionDetail, ExtensionKind, ExtensionRegistry, ExtensionSummary
from .models import (
    ExtensionSourceIdentity,
    ExtensionSourceRef,
    ExtensionSourceType,
    SkillDependencies,
    SkillManifest,
    SkillRecord,
    SkillRecordSpec,
)
from .skills import (
    SkillManager,
    SkillPreview,
    SkillReadiness,
    SkillSnapshot,
    SkillSnapshotEntry,
    StagedSkill,
    VersionedSkill,
    merge_skill_snapshots,
    parse_skill_manifest,
)

__all__ = [
    "ExtensionError",
    "ExtensionDetail",
    "ExtensionKind",
    "ExtensionRegistry",
    "ExtensionSummary",
    "AppManager",
    "AppReadiness",
    "AppSnapshot",
    "AppSnapshotEntry",
    "McpManager",
    "McpReadiness",
    "McpServer",
    "McpSnapshot",
    "McpSnapshotEntry",
    "PluginManager",
    "PluginManifest",
    "PluginPreview",
    "PluginReadiness",
    "PluginRecord",
    "PluginSnapshot",
    "PluginSnapshotEntry",
    "ExtensionSourceIdentity",
    "ExtensionSourceRef",
    "ExtensionSourceType",
    "SkillDependencies",
    "SkillManager",
    "SkillManifest",
    "SkillPreview",
    "SkillReadiness",
    "SkillRecord",
    "SkillRecordSpec",
    "SkillSnapshot",
    "SkillSnapshotEntry",
    "StagedSkill",
    "StagedPlugin",
    "VersionedSkill",
    "VersionedPlugin",
    "VersionedMcp",
    "VersionedAppConnection",
    "VersionedAppDefinition",
    "merge_mcp_snapshots",
    "merge_skill_snapshots",
    "parse_plugin_manifest",
    "parse_skill_manifest",
]
