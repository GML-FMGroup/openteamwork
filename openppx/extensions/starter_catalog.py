"""Validated first-party discovery catalog for OpenPPX Extensions."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Any, Literal

from openppx.product import PRODUCT

from .errors import ExtensionError
from .models import ExtensionPresentation
from .registry import ExtensionKind


StarterAvailability = Literal["ready", "needs_auth", "needs_dependency", "planned"]
StarterInstallMode = Literal["direct_app", "direct_mcp", "source", "builtin", "reference", "unavailable"]
StarterAuth = Literal["none", "secret", "oauth"]

_STARTER_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_ALLOWED_KEYS = {
    "id",
    "kind",
    "runtimeKind",
    "displayName",
    "description",
    "category",
    "developer",
    "availability",
    "installMode",
    "auth",
    "requirements",
    "note",
    "featured",
    "provenance",
    "presentation",
    "template",
}


@dataclass(frozen=True, slots=True)
class ExtensionStarter:
    """One safe discovery entry that can route into an existing lifecycle."""

    starter_id: str
    kind: ExtensionKind
    runtime_kind: ExtensionKind
    display_name: str
    description: str
    category: str
    developer: str
    availability: StarterAvailability
    install_mode: StarterInstallMode
    auth: StarterAuth
    requirements: tuple[str, ...]
    note: str
    featured: bool
    provenance: dict[str, str]
    presentation: ExtensionPresentation
    template: dict[str, Any]

    def to_payload(self) -> dict[str, Any]:
        """Project one entry without sharing mutable catalog data."""
        return {
            "id": self.starter_id,
            "kind": self.kind,
            "runtimeKind": self.runtime_kind,
            "displayName": self.display_name,
            "description": self.description,
            "category": self.category,
            "developer": self.developer,
            "availability": self.availability,
            "installMode": self.install_mode,
            "auth": self.auth,
            "requirements": list(self.requirements),
            "note": self.note,
            "featured": self.featured,
            "provenance": dict(self.provenance),
            "presentation": self.presentation.model_dump(mode="json", by_alias=True),
            "template": deepcopy(self.template),
        }


class ExtensionStarterCatalog:
    """Deterministic, read-only starter discovery owned by one Node process."""

    def __init__(self, entries: tuple[ExtensionStarter, ...]) -> None:
        by_id: dict[str, ExtensionStarter] = {}
        for entry in entries:
            if entry.starter_id in by_id:
                raise ValueError(f"duplicate Extension starter id: {entry.starter_id}")
            by_id[entry.starter_id] = entry
        self._entries = by_id

    def list(
        self,
        *,
        kind: ExtensionKind | None = None,
        query: str | None = None,
    ) -> tuple[ExtensionStarter, ...]:
        """Return stable entries filtered by user-facing type and free-text query."""
        needle = (query or "").strip().casefold()
        values = [
            entry
            for entry in self._entries.values()
            if (kind is None or entry.kind == kind)
            and (
                not needle
                or needle
                in " ".join(
                    (
                        entry.display_name,
                        entry.description,
                        entry.category,
                        entry.developer,
                        *entry.requirements,
                    )
                ).casefold()
            )
        ]
        return tuple(
            sorted(
                values,
                key=lambda item: (
                    not item.featured,
                    item.display_name.casefold(),
                    item.starter_id,
                ),
            )
        )

    def get(self, starter_id: str) -> ExtensionStarter:
        """Return one starter or a stable domain error."""
        entry = self._entries.get(starter_id)
        if entry is None:
            raise ExtensionError("not_found", f"Extension starter '{starter_id}' does not exist.")
        return entry


@lru_cache(maxsize=1)
def default_extension_starter_catalog() -> ExtensionStarterCatalog:
    """Load and validate the catalog bundled with the installed OpenPPX package."""
    resource = files("openppx.extensions").joinpath("data", "starter_catalog.json")
    with resource.open("r", encoding="utf-8") as handle:
        raw = _productize_visible_copy(json.load(handle))
    if not isinstance(raw, list):
        raise ValueError("Extension starter catalog must be a JSON array.")
    return ExtensionStarterCatalog(tuple(_parse_entry(item) for item in raw))


def _productize_visible_copy(value: Any) -> Any:
    """Replace shared-catalog product copy without changing protocol identifiers."""
    if isinstance(value, str):
        return value.replace("OpenPPX", PRODUCT.display_name)
    if isinstance(value, list):
        return [_productize_visible_copy(item) for item in value]
    if isinstance(value, dict):
        return {key: _productize_visible_copy(item) for key, item in value.items()}
    return value


def _parse_entry(raw: object) -> ExtensionStarter:
    if not isinstance(raw, dict) or set(raw) - _ALLOWED_KEYS:
        raise ValueError("Extension starter catalog entry has unsupported fields.")
    required = _ALLOWED_KEYS - {"note", "featured", "presentation", "template"}
    if not required.issubset(raw):
        raise ValueError("Extension starter catalog entry is incomplete.")
    starter_id = _text(raw, "id", limit=63)
    if _STARTER_ID_RE.fullmatch(starter_id) is None:
        raise ValueError(f"invalid Extension starter id: {starter_id}")
    kind = _choice(raw, "kind", {"plugin", "app", "mcp", "skill"})
    runtime_kind = _choice(raw, "runtimeKind", {"plugin", "app", "mcp", "skill"})
    availability = _choice(raw, "availability", {"ready", "needs_auth", "needs_dependency", "planned"})
    install_mode = _choice(raw, "installMode", {"direct_app", "direct_mcp", "source", "builtin", "reference", "unavailable"})
    auth = _choice(raw, "auth", {"none", "secret", "oauth"})
    requirements_raw = raw.get("requirements")
    if not isinstance(requirements_raw, list) or len(requirements_raw) > 16:
        raise ValueError(f"starter {starter_id} requirements must be a bounded list")
    requirements = tuple(_visible(item, limit=256) for item in requirements_raw)
    provenance_raw = raw.get("provenance")
    if not isinstance(provenance_raw, dict) or not provenance_raw:
        raise ValueError(f"starter {starter_id} requires provenance")
    provenance = {
        _visible(key, limit=64): _visible(value, limit=2048)
        for key, value in provenance_raw.items()
    }
    presentation_raw = raw.get("presentation", {"icon": kind})
    try:
        presentation = ExtensionPresentation.model_validate(presentation_raw)
    except Exception as exc:
        raise ValueError(f"starter {starter_id} presentation is invalid") from exc
    template = raw.get("template", {})
    if not isinstance(template, dict):
        raise ValueError(f"starter {starter_id} template must be an object")
    _reject_sensitive_defaults(template, path=f"starter:{starter_id}.template")
    if kind == "plugin" and runtime_kind != "plugin":
        raise ValueError(f"starter {starter_id} Plugin must use the Plugin runtime")
    if kind == "skill" and runtime_kind != "skill":
        raise ValueError(f"starter {starter_id} Skill must use the Skill runtime")
    if kind == "mcp" and runtime_kind != "mcp":
        raise ValueError(f"starter {starter_id} MCP must use the MCP runtime")
    if install_mode == "source" and kind not in {"plugin", "skill"}:
        raise ValueError(f"starter {starter_id} source installs are reserved for Plugins and Skills")
    if install_mode == "direct_mcp":
        if runtime_kind != "mcp" or not {"serverId", "displayName", "risk", "transport"}.issubset(template):
            raise ValueError(f"starter {starter_id} requires a complete direct MCP template")
    if install_mode == "direct_app":
        if kind != "app" or runtime_kind != "app" or not isinstance(template.get("definition"), dict):
            raise ValueError(f"starter {starter_id} requires a complete direct App definition")
    if install_mode == "source" and not isinstance(template.get("source"), dict):
        raise ValueError(f"starter {starter_id} requires an Extension source template")
    featured = raw.get("featured", False)
    if not isinstance(featured, bool):
        raise ValueError(f"starter {starter_id} featured must be boolean")
    return ExtensionStarter(
        starter_id=starter_id,
        kind=kind,  # type: ignore[arg-type]
        runtime_kind=runtime_kind,  # type: ignore[arg-type]
        display_name=_text(raw, "displayName", limit=80),
        description=_text(raw, "description", limit=2048),
        category=_text(raw, "category", limit=64),
        developer=_text(raw, "developer", limit=128),
        availability=availability,  # type: ignore[arg-type]
        install_mode=install_mode,  # type: ignore[arg-type]
        auth=auth,  # type: ignore[arg-type]
        requirements=requirements,
        note=_optional_text(raw.get("note"), limit=1024),
        featured=featured,
        provenance=provenance,
        presentation=presentation,
        template=deepcopy(template),
    )


def _reject_sensitive_defaults(value: object, *, path: str) -> None:
    """Reject catalog templates that could smuggle credential values to clients."""
    if isinstance(value, dict):
        forbidden = {"secretValues", "credentialValues", "accessToken", "refreshToken", "password"}
        if forbidden.intersection(value):
            raise ValueError(f"{path} contains a protected value field")
        for key, item in value.items():
            _reject_sensitive_defaults(item, path=f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _reject_sensitive_defaults(item, path=f"{path}[{index}]")


def _text(raw: dict[str, object], key: str, *, limit: int) -> str:
    return _visible(raw.get(key), limit=limit)


def _optional_text(value: object, *, limit: int) -> str:
    if value in (None, ""):
        return ""
    return _visible(value, limit=limit)


def _visible(value: object, *, limit: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Extension starter text is invalid.")
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ValueError("Extension starter text contains control characters.")
    return value.strip()


def _choice(raw: dict[str, object], key: str, choices: set[str]) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"Extension starter {key} is invalid.")
    return value


__all__ = [
    "ExtensionStarter",
    "ExtensionStarterCatalog",
    "default_extension_starter_catalog",
]
