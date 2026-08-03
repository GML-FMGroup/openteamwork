"""Architecture boundary tests for the long-term Config package."""

from __future__ import annotations

import ast
from pathlib import Path

import openppx.config


def test_config_package_does_not_depend_on_product_or_runtime_layers() -> None:
    package_dir = Path(openppx.config.__file__).parent
    forbidden_prefixes = (
        "openppx.app",
        "openppx.runtime",
        "openppx.channels",
        "openppx.core.config",
        "google.adk",
    )
    violations: list[str] = []

    for source_path in sorted(package_dir.glob("*.py")):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        for node in ast.walk(tree):
            imported: tuple[str, ...] = ()
            if isinstance(node, ast.Import):
                imported = tuple(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported = (node.module,)
            for module_name in imported:
                if module_name.startswith(forbidden_prefixes):
                    violations.append(f"{source_path.name}: {module_name}")

    assert violations == []
