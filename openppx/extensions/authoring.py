"""Standards-first local authoring helpers for portable OpenPPX extensions."""

from __future__ import annotations

import asyncio
import json
import os
import re
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Literal

from google.adk.evaluation.agent_evaluator import AgentEvaluator
from google.adk.evaluation.eval_set import EvalSet

from openppx.config.secrets import InMemorySecretStore
from openppx.product import PRODUCT

from .app_models import AppDefinition
from .models import ExtensionSourceRef
from .plugins import PluginManager
from .skills import SkillManager


AuthoringKind = Literal["skill", "plugin", "app"]
_RESOURCE_NAME = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")
_FIXED_ZIP_TIME = (1980, 1, 1, 0, 0, 0)


class ExtensionAuthoringError(ValueError):
    """Raised when a local authoring request is unsafe or invalid."""


def scaffold_extension(
    kind: AuthoringKind,
    name: str,
    destination: Path,
    *,
    description: str,
    display_name: str | None = None,
    developer: str = f"{PRODUCT.display_name} Developer",
) -> dict[str, Any]:
    """Create one minimal standards-compliant extension source tree.

    Authoring sources remain outside the Node-managed installation directory.
    The caller must explicitly preview and install the result afterwards.
    """
    _require_name(name)
    description = _require_visible(description, "description")
    destination = destination.expanduser().resolve(strict=False)
    destination.mkdir(parents=True, exist_ok=True)
    target = destination / (f"{name}.app.json" if kind == "app" else name)
    if target.exists():
        raise ExtensionAuthoringError(f"authoring target already exists: {target}")

    if kind == "skill":
        target.mkdir()
        (target / "SKILL.md").write_text(_skill_template(name, description), encoding="utf-8")
    elif kind == "plugin":
        manifest = {
            "name": name,
            "version": "0.1.0",
            "description": description,
            "author": {"name": _require_visible(developer, "developer")},
            "interface": {
                "displayName": display_name or _display_name(name),
                "shortDescription": description[:256],
                "developerName": developer,
            },
        }
        manifest_dir = target / ".agent-plugin"
        manifest_dir.mkdir(parents=True)
        _write_json(manifest_dir / "plugin.json", manifest)
    elif kind == "app":
        definition = _app_template(
            name,
            description,
            display_name=display_name or _display_name(name),
            developer=_require_visible(developer, "developer"),
        )
        _write_json(target, definition)
    else:  # pragma: no cover - guarded by CLI and Literal callers
        raise ExtensionAuthoringError(f"unsupported authoring kind: {kind}")

    validation = validate_extension_source(kind, target)
    return {"kind": kind, "name": name, "path": str(target), "validation": validation}


def validate_extension_source(kind: AuthoringKind, source: Path) -> dict[str, Any]:
    """Validate one authoring source with the production extension parsers."""
    source = source.expanduser().resolve(strict=True)
    if kind == "skill":
        if not source.is_dir():
            raise ExtensionAuthoringError("Skill source must be a directory containing SKILL.md")
        with tempfile.TemporaryDirectory(prefix="openppx-author-skill-") as temporary:
            manager = SkillManager(Path(temporary) / "node")
            staged = manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
            try:
                preview = manager.preview(staged)
                return {
                    "valid": True,
                    "kind": kind,
                    "name": preview.skill_id,
                    "version": preview.version,
                    "digest": preview.digest,
                    "risk": preview.risk,
                }
            finally:
                staged.extension.cleanup()
    if kind == "plugin":
        if not source.is_dir():
            raise ExtensionAuthoringError("Plugin source must be a directory containing .agent-plugin/plugin.json")
        with tempfile.TemporaryDirectory(prefix="openppx-author-plugin-") as temporary:
            manager = PluginManager(Path(temporary) / "node", InMemorySecretStore())
            staged = manager.stage(ExtensionSourceRef(type="local_directory", locator=str(source)))
            try:
                preview = manager.preview(staged)
                return {
                    "valid": True,
                    "kind": kind,
                    "name": preview.plugin_id,
                    "version": preview.version,
                    "digest": preview.digest,
                    "risk": preview.risk,
                    "resourceCounts": preview.resource_counts,
                }
            finally:
                staged.extension.cleanup()
    if kind == "app":
        if not source.is_file():
            raise ExtensionAuthoringError("App source must be one .app.json file")
        definition = AppDefinition.model_validate_json(source.read_text(encoding="utf-8"))
        return {
            "valid": True,
            "kind": kind,
            "name": definition.metadata.name,
            "version": definition.spec.version,
            "tools": len(definition.spec.tools),
            "auth": definition.spec.auth.type,
        }
    raise ExtensionAuthoringError(f"unsupported authoring kind: {kind}")


def package_extension(kind: AuthoringKind, source: Path, output: Path) -> dict[str, Any]:
    """Validate and write one deterministic, symlink-free ZIP package."""
    source = source.expanduser().resolve(strict=True)
    validation = validate_extension_source(kind, source)
    output = output.expanduser().resolve(strict=False)
    if output.exists():
        raise ExtensionAuthoringError(f"package output already exists: {output}")
    if source.is_dir() and (output == source or source in output.parents):
        raise ExtensionAuthoringError("package output must stay outside the extension source")
    output.parent.mkdir(parents=True, exist_ok=True)

    files = [source] if source.is_file() else sorted(path for path in source.rglob("*") if path.is_file())
    for path in files:
        if path.is_symlink():
            raise ExtensionAuthoringError(f"extension packages cannot contain symlinks: {path}")
    with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in files:
            relative = Path(path.name) if source.is_file() else path.relative_to(source)
            info = zipfile.ZipInfo(relative.as_posix(), date_time=_FIXED_ZIP_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes(), compress_type=zipfile.ZIP_DEFLATED, compresslevel=9)
    return {
        "kind": kind,
        "name": validation["name"],
        "output": str(output),
        "bytes": output.stat().st_size,
        "validation": validation,
    }


def validate_adk_evalset(path: Path) -> dict[str, Any]:
    """Validate a Skill/Agent eval file with ADK's official EvalSet schema."""
    path = path.expanduser().resolve(strict=True)
    eval_set = EvalSet.model_validate_json(path.read_text(encoding="utf-8"))
    return {
        "valid": True,
        "evalSetId": eval_set.eval_set_id,
        "cases": len(eval_set.eval_cases),
        "turns": sum(len(case.conversation) for case in eval_set.eval_cases),
    }


def run_adk_evaluation(
    agent_module: str,
    evalset: Path,
    *,
    num_runs: int = 1,
    agent_name: str | None = None,
) -> dict[str, Any]:
    """Run an extension eval through Google ADK's official AgentEvaluator."""
    if num_runs < 1 or num_runs > 20:
        raise ExtensionAuthoringError("num_runs must be between 1 and 20")
    validation = validate_adk_evalset(evalset)
    asyncio.run(
        AgentEvaluator.evaluate(
            agent_module=agent_module,
            eval_dataset_file_path_or_dir=str(evalset.expanduser().resolve(strict=True)),
            num_runs=num_runs,
            agent_name=agent_name,
            print_detailed_results=True,
        )
    )
    return {**validation, "evaluated": True, "numRuns": num_runs, "agentModule": agent_module}


def _skill_template(name: str, description: str) -> str:
    return (
        "---\n"
        f"name: {name}\n"
        f"description: {json.dumps(description, ensure_ascii=False)}\n"
        "metadata:\n"
        "  openppx:\n"
        "    version: 0.1.0\n"
        "    risk: medium\n"
        "    dependencies:\n"
        "      executables: []\n"
        "      environment: []\n"
        "    capabilities: []\n"
        "---\n\n"
        f"# {_display_name(name)}\n\n"
        f"{description}\n\n"
        "## Instructions\n\n"
        "Describe the bounded workflow, required checks, and expected output here.\n"
    )


def _app_template(name: str, description: str, *, display_name: str, developer: str) -> dict[str, Any]:
    return {
        "apiVersion": "openppx.io/v1alpha1",
        "kind": "AppDefinition",
        "metadata": {"name": name},
        "spec": {
            "displayName": display_name,
            "description": description,
            "version": "0.1.0",
            "category": "developer",
            "developer": developer,
            "presentation": {"icon": "app"},
            "source": {
                "type": "local_directory",
                "locator": f"authoring:{name}",
                "version": "0.1.0",
                "revision": "draft",
                "digest": f"sha256:{'0' * 64}",
            },
            "auth": {"type": "none", "credentials": []},
            "implementation": {
                "type": "mcp",
                "transport": {"type": "streamable_http", "url": "http://127.0.0.1:3000/mcp", "headers": {}},
            },
            "tools": [
                {
                    "name": f"{name.replace('-', '_')}_read",
                    "title": f"Read {display_name}",
                    "description": f"Read data exposed by the {display_name} MCP server.",
                    "access": "read",
                    "risk": "low",
                    "enabledByDefault": True,
                }
            ],
            "policy": {
                "requireConfirmation": False,
                "progressEvents": True,
                "longTaskProxy": True,
                "inlineBudgetMs": 5000,
            },
        },
    }


def _require_name(name: str) -> None:
    if _RESOURCE_NAME.fullmatch(name) is None:
        raise ExtensionAuthoringError("name must be a lowercase resource identifier")


def _require_visible(value: str, label: str) -> str:
    if not value.strip() or any(ord(character) < 32 or ord(character) == 127 for character in value):
        raise ExtensionAuthoringError(f"{label} must contain visible characters")
    return value.strip()


def _display_name(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split("-"))


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + os.linesep, encoding="utf-8")


__all__ = [
    "ExtensionAuthoringError",
    "package_extension",
    "run_adk_evaluation",
    "scaffold_extension",
    "validate_adk_evalset",
    "validate_extension_source",
]
