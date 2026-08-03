"""Strict declarative Product Plugin manifest and installed-record models."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, JsonValue, StrictBool, StringConstraints, field_validator, model_validator

from openppx.config.models import DisplayName, ResourceMetadata, ResourceName, StrictConfigModel

from .models import ExtensionSourceIdentity


RelativeResourcePath = Annotated[str, StringConstraints(min_length=1, max_length=512)]
RuntimeCapability = Annotated[
    str,
    StringConstraints(pattern=r"^runtime\.[a-z](?:[a-z0-9.-]{0,126}[a-z0-9])?$"),
]


class PluginResourceRef(StrictConfigModel):
    """One namespaced, relative resource declaration inside a Plugin."""

    name: ResourceName
    path: RelativeResourcePath

    @field_validator("path")
    @classmethod
    def path_must_be_safe_relative_posix(cls, value: str) -> str:
        """Reject platform-dependent or escaping resource paths."""
        if "\\" in value or "\x00" in value:
            raise ValueError("Plugin resource path must use POSIX separators")
        path = PurePosixPath(value)
        if path.is_absolute() or ".." in path.parts or "." in path.parts or ":" in path.parts[0]:
            raise ValueError("Plugin resource path must remain below the Plugin root")
        return path.as_posix()


class PluginResources(StrictConfigModel):
    """Declarative resource inventory supported by Product Plugin v1."""

    skills: list[PluginResourceRef] = Field(default_factory=list, max_length=128)
    app_definitions: list[PluginResourceRef] = Field(default_factory=list, max_length=128)
    mcp_servers: list[PluginResourceRef] = Field(default_factory=list, max_length=128)
    agent_templates: list[PluginResourceRef] = Field(default_factory=list, max_length=128)
    config_schemas: list[PluginResourceRef] = Field(default_factory=list, max_length=128)
    documentation: list[PluginResourceRef] = Field(default_factory=list, max_length=128)

    @model_validator(mode="after")
    def identities_and_paths_must_be_unique(self) -> "PluginResources":
        """Keep each inventory deterministic and free of ambiguous aliases."""
        all_paths: list[str] = []
        for values in (
            self.skills,
            self.app_definitions,
            self.mcp_servers,
            self.agent_templates,
            self.config_schemas,
            self.documentation,
        ):
            names = [item.name for item in values]
            if len(names) != len(set(names)):
                raise ValueError("Plugin resource identities must be unique within each kind")
            all_paths.extend(item.path for item in values)
        if len(all_paths) != len(set(all_paths)):
            raise ValueError("Plugin resource paths must be unique")
        return self

    def count(self) -> int:
        """Return the total number of declared resources."""
        return sum(
            len(values)
            for values in (
                self.skills,
                self.app_definitions,
                self.mcp_servers,
                self.agent_templates,
                self.config_schemas,
                self.documentation,
            )
        )


class PluginManifestSpec(StrictConfigModel):
    """Product identity, risk, controlled capability, and resource declarations."""

    display_name: DisplayName
    description: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    developer: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    risk: Literal["low", "medium", "high"] = "medium"
    runtime_capabilities: list[RuntimeCapability] = Field(default_factory=list, max_length=64)
    resources: PluginResources = Field(default_factory=PluginResources)

    @field_validator("runtime_capabilities")
    @classmethod
    def capabilities_must_be_unique(cls, value: list[str]) -> list[str]:
        """Reject duplicate capability grants."""
        if len(value) != len(set(value)):
            raise ValueError("runtimeCapabilities entries must be unique")
        return value

    @model_validator(mode="after")
    def plugin_must_declare_a_useful_capability(self) -> "PluginManifestSpec":
        """Reject empty bundles that cannot change product behavior or documentation."""
        if self.resources.count() == 0 and not self.runtime_capabilities:
            raise ValueError("Plugin must declare at least one resource or runtime capability")
        return self


class PluginManifest(StrictConfigModel):
    """Root `.openppx-plugin/plugin.json` document."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["PluginManifest"]
    metadata: ResourceMetadata
    spec: PluginManifestSpec


class PluginAgentTemplateSpec(StrictConfigModel):
    """Declarative Agent composition hints without executable hooks."""

    display_name: DisplayName | None = None
    instruction: Annotated[str, StringConstraints(max_length=16_384)] | None = None
    model_role: Literal["fast", "reasoning", "vision"] | None = None
    skills: list[ResourceName] = Field(default_factory=list, max_length=128)
    app_definitions: list[ResourceName] = Field(default_factory=list, max_length=128)
    mcp_servers: list[ResourceName] = Field(default_factory=list, max_length=128)

    @field_validator("skills", "app_definitions", "mcp_servers")
    @classmethod
    def resource_lists_must_be_unique(cls, value: list[str]) -> list[str]:
        """Reject ambiguous duplicate composition references."""
        if len(value) != len(set(value)):
            raise ValueError("Agent template resource references must be unique")
        return value


class PluginAgentTemplate(StrictConfigModel):
    """Non-executable Agent template stored inside a Plugin."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["AgentTemplate"]
    spec: PluginAgentTemplateSpec = Field(default_factory=PluginAgentTemplateSpec)


class PluginConfigSchema(StrictConfigModel):
    """Bounded object-schema fragment exposed by a Plugin."""

    type: Literal["object"]
    properties: dict[str, JsonValue] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list, max_length=256)
    additional_properties: StrictBool = False

    @field_validator("properties")
    @classmethod
    def properties_are_bounded(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        """Keep Plugin configuration surfaces reviewable."""
        if len(value) > 256:
            raise ValueError("Plugin config schema has too many properties")
        return value


class PluginRecordSpec(StrictConfigModel):
    """Node-owned installed Plugin fact without executable implementation fields."""

    display_name: DisplayName
    description: Annotated[str, StringConstraints(min_length=1, max_length=2048)]
    version: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    developer: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    digest: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    source: ExtensionSourceIdentity
    trust: Literal["builtin", "local", "third_party"]
    risk: Literal["low", "medium", "high"]
    runtime_capabilities: list[RuntimeCapability] = Field(default_factory=list, max_length=64)
    resources: PluginResources
    enabled_agent_ids: list[ResourceName] = Field(default_factory=list)

    @field_validator("runtime_capabilities", "enabled_agent_ids")
    @classmethod
    def lists_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep installed Plugin state deterministic."""
        if len(value) != len(set(value)):
            raise ValueError("Plugin list entries must be unique")
        return value


class PluginRecord(StrictConfigModel):
    """Revisioned installed Product Plugin resource."""

    api_version: Literal["openppx.io/v1alpha1"]
    kind: Literal["Plugin"]
    metadata: ResourceMetadata
    spec: PluginRecordSpec


__all__ = [
    "PluginManifest",
    "PluginManifestSpec",
    "PluginRecord",
    "PluginRecordSpec",
    "PluginResourceRef",
    "PluginResources",
    "PluginAgentTemplate",
    "PluginAgentTemplateSpec",
    "PluginConfigSchema",
]
