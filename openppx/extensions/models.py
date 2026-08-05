"""Strict common Extension and Skill resource models."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, StringConstraints, field_validator

from openppx.config.models import ResourceMetadata, ResourceName, StrictConfigModel


ExtensionSourceType: TypeAlias = Literal[
    "builtin",
    "local_directory",
    "local_archive",
    "git",
    "npm",
    "catalog",
]
Digest: TypeAlias = Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
VisibleText: TypeAlias = Annotated[str, StringConstraints(min_length=1, max_length=512)]


class ExtensionPresentation(StrictConfigModel):
    """Client-safe visual identity shared by every Extension surface."""

    icon: Annotated[
        str,
        StringConstraints(pattern=r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$"),
    ]
    brand_color: Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")] | None = None


class ExtensionSourceRef(StrictConfigModel):
    """User-supplied immutable reference to one Extension source."""

    type: ExtensionSourceType
    locator: VisibleText
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    revision: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    provider: Annotated[str, StringConstraints(pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")] | None = None
    subpath: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None

    @field_validator("locator", "version", "revision", "subpath")
    @classmethod
    def values_must_be_visible(cls, value: str | None) -> str | None:
        """Reject blank or control-bearing source values."""
        if value is None:
            return None
        if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
            raise ValueError("source values must contain visible characters")
        return value.strip()


class ExtensionSourceIdentity(StrictConfigModel):
    """Pinned, client-safe provenance retained after staging."""

    type: ExtensionSourceType
    locator: VisibleText
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    revision: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    digest: Digest


class SkillDependencies(StrictConfigModel):
    """Declarative readiness dependencies for one Skill."""

    executables: list[Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9._+-]{1,64}$")]] = Field(
        default_factory=list
    )
    environment: list[Annotated[str, StringConstraints(pattern=r"^[A-Z][A-Z0-9_]{0,127}$")]] = Field(
        default_factory=list
    )

    @field_validator("executables", "environment")
    @classmethod
    def entries_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep dependency diagnostics deterministic."""
        if len(value) != len(set(value)):
            raise ValueError("dependency entries must be unique")
        return value


class SkillManifest(StrictConfigModel):
    """Validated Skill semantics parsed from root SKILL.md frontmatter."""

    name: ResourceName
    description: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)] = "0.0.0"
    risk: Literal["low", "medium", "high"] = "medium"
    dependencies: SkillDependencies = Field(default_factory=SkillDependencies)
    capabilities: list[Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_.-]{0,127}$")]] = Field(
        default_factory=list
    )

    @field_validator("capabilities")
    @classmethod
    def capabilities_must_be_unique(cls, value: list[str]) -> list[str]:
        """Reject ambiguous duplicate capability declarations."""
        if len(value) != len(set(value)):
            raise ValueError("capabilities must be unique")
        return value


class SkillRecordSpec(StrictConfigModel):
    """Persistent installed Skill state owned by one Node."""

    description: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    digest: Digest
    source: ExtensionSourceIdentity
    risk: Literal["low", "medium", "high"]
    presentation: ExtensionPresentation = Field(
        default_factory=lambda: ExtensionPresentation(icon="skill")
    )
    dependencies: SkillDependencies
    capabilities: list[str] = Field(default_factory=list)
    enabled_agent_ids: list[ResourceName] = Field(default_factory=list)

    @field_validator("capabilities", "enabled_agent_ids")
    @classmethod
    def lists_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep lifecycle mutations deterministic and conflict-free."""
        if len(value) != len(set(value)):
            raise ValueError("entries must be unique")
        return value


class SkillRecord(StrictConfigModel):
    """Versioned Skill installation resource."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["Skill"]
    metadata: ResourceMetadata
    spec: SkillRecordSpec


__all__ = [
    "Digest",
    "ExtensionPresentation",
    "ExtensionSourceIdentity",
    "ExtensionSourceRef",
    "ExtensionSourceType",
    "SkillDependencies",
    "SkillManifest",
    "SkillRecord",
    "SkillRecordSpec",
]
