from __future__ import annotations

import importlib.util
from pathlib import Path
import sys


def _verification_module():
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("openppx_verify", root / "scripts" / "verify.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_verification_gate_covers_every_product_layer_without_pnpm() -> None:
    module = _verification_module()
    root = Path(__file__).resolve().parents[2]

    steps = module.verification_steps(
        root,
        include_python=True,
        include_build=True,
        include_package=True,
    )

    assert [step.name for step in steps] == [
        "Python tests",
        "Python compile",
        "Client tests",
        "Client types",
        "Desktop tests",
        "Desktop types",
        "Electron preload tests",
        "Desktop production build",
        "Electron preload build check",
        "macOS ARM64 directory package",
        "Packaged Electron preload check",
    ]
    assert all("pnpm" not in " ".join(step.command) for step in steps)
