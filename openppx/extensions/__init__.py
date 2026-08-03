"""OpenPPX Extension Platform domain boundary."""

from .errors import ExtensionError
from .mcp import McpManager, McpReadiness, McpSnapshot, McpSnapshotEntry, VersionedMcp
from .mcp_models import McpServer
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
    SkillSnapshot,
    SkillSnapshotEntry,
    StagedSkill,
    VersionedSkill,
    parse_skill_manifest,
)

__all__ = [
    "ExtensionError",
    "McpManager",
    "McpReadiness",
    "McpServer",
    "McpSnapshot",
    "McpSnapshotEntry",
    "ExtensionSourceIdentity",
    "ExtensionSourceRef",
    "ExtensionSourceType",
    "SkillDependencies",
    "SkillManager",
    "SkillManifest",
    "SkillPreview",
    "SkillRecord",
    "SkillRecordSpec",
    "SkillSnapshot",
    "SkillSnapshotEntry",
    "StagedSkill",
    "VersionedSkill",
    "VersionedMcp",
    "parse_skill_manifest",
]
