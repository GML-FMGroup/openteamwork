"""Codex-compatible Plugin manifest and OpenPPX installed-record models."""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import Field, JsonValue, StrictBool, StringConstraints, field_validator, model_validator

from openppx.config.models import DisplayName, ResourceMetadata, ResourceName, StrictConfigModel

from .models import ExtensionPresentation, ExtensionSourceIdentity


PluginPath = Annotated[str, StringConstraints(min_length=3, max_length=512)]
PluginVersion = Annotated[str, StringConstraints(min_length=1, max_length=128)]
PluginDescription = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
PluginUrl = Annotated[str, StringConstraints(min_length=1, max_length=2048)]


def _validate_plugin_path(value: str) -> str:
    """Validate one Codex Plugin path relative to the package root."""
    if not value.startswith("./") or "\\" in value or "\x00" in value:
        raise ValueError("Plugin paths must start with './' and use POSIX separators")
    path = PurePosixPath(value.removeprefix("./"))
    if not path.parts or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("Plugin paths must stay below the Plugin root")
    return f"./{path.as_posix()}"


def _validate_public_url(value: str | None) -> str | None:
    """Keep Plugin metadata URLs absolute and free of embedded credentials."""
    if value is None:
        return None
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("Plugin URL must be an absolute HTTP(S) URL without credentials")
    return value


class PluginAuthor(StrictConfigModel):
    """Publisher metadata from the Codex Plugin manifest."""

    name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    email: Annotated[str, StringConstraints(min_length=3, max_length=320)] | None = None
    url: PluginUrl | None = None

    _url_is_public = field_validator("url")(_validate_public_url)


class PluginInterface(StrictConfigModel):
    """Optional install-surface metadata defined by the Codex Plugin standard."""

    display_name: DisplayName | None = None
    short_description: Annotated[str, StringConstraints(min_length=1, max_length=256)] | None = None
    long_description: PluginDescription | None = None
    developer_name: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    category: Annotated[str, StringConstraints(min_length=1, max_length=80)] | None = None
    capabilities: list[Annotated[str, StringConstraints(min_length=1, max_length=80)]] = Field(
        default_factory=list,
        max_length=64,
    )
    website_url: PluginUrl | None = Field(default=None, alias="websiteURL")
    privacy_policy_url: PluginUrl | None = Field(default=None, alias="privacyPolicyURL")
    terms_of_service_url: PluginUrl | None = Field(default=None, alias="termsOfServiceURL")
    support_url: PluginUrl | None = Field(default=None, alias="supportURL")
    default_prompt: list[Annotated[str, StringConstraints(min_length=1, max_length=1024)]] = Field(
        default_factory=list,
        max_length=32,
    )
    brand_color: Annotated[str, StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$")] | None = None
    composer_icon: PluginPath | None = None
    logo: PluginPath | None = None
    logo_dark: PluginPath | None = None
    screenshots: list[PluginPath] = Field(default_factory=list, max_length=32)

    @field_validator(
        "website_url",
        "privacy_policy_url",
        "terms_of_service_url",
        "support_url",
    )
    @classmethod
    def urls_must_be_public(cls, value: str | None) -> str | None:
        """Validate optional install-surface links."""
        return _validate_public_url(value)

    @field_validator("composer_icon", "logo", "logo_dark")
    @classmethod
    def asset_path_must_be_safe(cls, value: str | None) -> str | None:
        """Validate optional single-asset paths."""
        return _validate_plugin_path(value) if value is not None else None

    @field_validator("screenshots")
    @classmethod
    def screenshot_paths_must_be_safe(cls, value: list[str]) -> list[str]:
        """Validate screenshot paths and reject duplicates."""
        normalized = [_validate_plugin_path(item) for item in value]
        if len(normalized) != len(set(normalized)):
            raise ValueError("Plugin screenshot paths must be unique")
        return normalized

    @field_validator("capabilities", "default_prompt")
    @classmethod
    def lists_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep presentation metadata deterministic."""
        if len(value) != len(set(value)):
            raise ValueError("Plugin interface entries must be unique")
        return value


class PluginManifest(StrictConfigModel):
    """Root `.agent-plugin/plugin.json` document using Codex manifest fields."""

    name: ResourceName
    version: PluginVersion
    description: PluginDescription
    author: PluginAuthor | None = None
    homepage: PluginUrl | None = None
    repository: PluginUrl | None = None
    license: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None
    keywords: list[Annotated[str, StringConstraints(min_length=1, max_length=80)]] = Field(
        default_factory=list,
        max_length=64,
    )
    skills: PluginPath | None = None
    mcp_servers: PluginPath | None = None
    apps: PluginPath | None = None
    hooks: JsonValue | None = None
    interface: PluginInterface | None = None
    bundled_content_variant: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None

    @field_validator("homepage", "repository")
    @classmethod
    def metadata_urls_must_be_public(cls, value: str | None) -> str | None:
        """Validate optional publisher links."""
        return _validate_public_url(value)

    @field_validator("skills", "mcp_servers", "apps")
    @classmethod
    def component_path_must_be_safe(cls, value: str | None) -> str | None:
        """Validate standard component pointers."""
        return _validate_plugin_path(value) if value is not None else None

    @field_validator("keywords")
    @classmethod
    def keywords_must_be_unique(cls, value: list[str]) -> list[str]:
        """Reject duplicate discovery metadata."""
        if len(value) != len(set(value)):
            raise ValueError("Plugin keywords must be unique")
        return value

    @field_validator("hooks")
    @classmethod
    def hooks_must_match_codex_shape(cls, value: JsonValue | None) -> JsonValue | None:
        """Accept Codex hook paths, inline hook objects, or arrays of either."""
        if value is None:
            return None
        values = value if isinstance(value, list) else [value]
        if not values:
            raise ValueError("Plugin hooks cannot be an empty array")
        for item in values:
            if isinstance(item, str):
                _validate_plugin_path(item)
            elif not isinstance(item, dict):
                raise ValueError("Plugin hooks must contain paths or inline hook objects")
        return value


class PluginResourceRef(StrictConfigModel):
    """One internally namespaced standard component discovered from a Plugin."""

    name: ResourceName
    path: PluginPath

    @field_validator("path")
    @classmethod
    def path_must_be_safe(cls, value: str) -> str:
        """Keep discovered component paths below the Plugin root."""
        return _validate_plugin_path(value)


class PluginRegisteredApp(StrictConfigModel):
    """One standard `.app.json` registered MCP connection mapping."""

    name: ResourceName
    technical_id: Annotated[str, StringConstraints(min_length=1, max_length=256)] = Field(alias="id")
    required: StrictBool = False


class PluginResources(StrictConfigModel):
    """Installed inventory derived from standard package component files."""

    skills: list[PluginResourceRef] = Field(default_factory=list, max_length=128)
    mcp_servers: list[PluginResourceRef] = Field(default_factory=list, max_length=128)
    apps: list[PluginRegisteredApp] = Field(default_factory=list, max_length=128)
    hook_paths: list[PluginPath] = Field(default_factory=list, max_length=64)
    inline_hook_count: int = Field(default=0, ge=0, le=64)

    @model_validator(mode="after")
    def identities_and_paths_must_be_unique(self) -> "PluginResources":
        """Keep installed component inventory deterministic."""
        for values in (self.skills, self.mcp_servers):
            names = [item.name for item in values]
            if len(names) != len(set(names)):
                raise ValueError("Plugin component identities must be unique within each kind")
        skill_paths = [item.path for item in self.skills]
        if len(skill_paths) != len(set(skill_paths)):
            raise ValueError("Plugin Skill paths must be unique")
        app_names = [item.name for item in self.apps]
        if len(app_names) != len(set(app_names)):
            raise ValueError("Plugin registered App names must be unique")
        if len(self.hook_paths) != len(set(self.hook_paths)):
            raise ValueError("Plugin hook paths must be unique")
        return self

    def count(self) -> int:
        """Return the total number of discovered standard components."""
        return (
            len(self.skills)
            + len(self.mcp_servers)
            + len(self.apps)
            + len(self.hook_paths)
            + self.inline_hook_count
        )


class PluginRecordSpec(StrictConfigModel):
    """Node-owned installed Plugin fact derived from a portable package."""

    display_name: DisplayName
    description: PluginDescription
    version: PluginVersion
    developer: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    digest: Annotated[str, StringConstraints(pattern=r"^sha256:[0-9a-f]{64}$")]
    source: ExtensionSourceIdentity
    trust: Annotated[str, StringConstraints(pattern=r"^(builtin|local|third_party)$")]
    risk: Annotated[str, StringConstraints(pattern=r"^(low|medium|high)$")]
    presentation: ExtensionPresentation = Field(
        default_factory=lambda: ExtensionPresentation(icon="plugin")
    )
    resources: PluginResources
    enabled_agent_ids: list[ResourceName] = Field(default_factory=list)

    @field_validator("enabled_agent_ids")
    @classmethod
    def enabled_agents_must_be_unique(cls, value: list[str]) -> list[str]:
        """Keep Plugin enablement deterministic."""
        if len(value) != len(set(value)):
            raise ValueError("Plugin enabled Agent entries must be unique")
        return value


class PluginRecord(StrictConfigModel):
    """Revisioned installed Plugin resource."""

    api_version: Annotated[str, StringConstraints(pattern=r"^openppx\.io/v1alpha1$")]
    kind: Annotated[str, StringConstraints(pattern=r"^Plugin$")]
    metadata: ResourceMetadata
    spec: PluginRecordSpec


__all__ = [
    "PluginAuthor",
    "PluginInterface",
    "PluginManifest",
    "PluginRecord",
    "PluginRecordSpec",
    "PluginRegisteredApp",
    "PluginResourceRef",
    "PluginResources",
]
