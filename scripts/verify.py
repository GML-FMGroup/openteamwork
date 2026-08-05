#!/usr/bin/env python3
"""Run the canonical OpenPPX verification gate without a Corepack download."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
import subprocess
import sys


@dataclass(frozen=True)
class VerificationStep:
    """One deterministic verification command and its working directory."""

    name: str
    cwd: Path
    command: tuple[str, ...]


def verification_steps(
    root: Path,
    *,
    include_python: bool,
    include_build: bool,
    include_package: bool = False,
) -> tuple[VerificationStep, ...]:
    """Return the fixed Python, Client, Desktop, Electron, build, and packaging gate."""
    client = root / "packages" / "client"
    desktop = root / "apps" / "desktop"
    steps: list[VerificationStep] = []
    if include_python:
        steps.extend(
            [
                VerificationStep("Python tests", root, (sys.executable, "-m", "pytest", "-q")),
                VerificationStep(
                    "Python compile",
                    root,
                    (sys.executable, "-m", "compileall", "-q", "openppx", "tests"),
                ),
            ]
        )
    steps.extend(
        [
            VerificationStep("Client tests", client, (str(client / "node_modules/.bin/vitest"), "run")),
            VerificationStep("Client types", client, (str(client / "node_modules/.bin/tsc"), "--noEmit")),
            VerificationStep("Desktop tests", desktop, (str(desktop / "node_modules/.bin/vitest"), "run")),
            VerificationStep("Desktop types", desktop, (str(desktop / "node_modules/.bin/tsc"), "--noEmit")),
            VerificationStep(
                "Electron preload tests",
                desktop,
                ("node", "--test", "scripts/verify-preload.node-test.mjs"),
            ),
        ]
    )
    if include_build:
        steps.extend(
            [
                VerificationStep("Desktop production build", desktop, (str(desktop / "node_modules/.bin/vite"), "build")),
                VerificationStep(
                    "Electron preload build check",
                    desktop,
                    ("node", "scripts/verify-preload.mjs", "dist-electron/preload/index.cjs"),
                ),
            ]
        )
    if include_package:
        steps.extend(
            [
                VerificationStep(
                    "macOS ARM64 directory package",
                    desktop,
                    (str(desktop / "node_modules/.bin/electron-builder"), "--dir", "--mac", "--arm64"),
                ),
                VerificationStep(
                    "Packaged Electron preload check",
                    desktop,
                    (
                        "node",
                        "scripts/verify-preload.mjs",
                        "--asar",
                        "release/mac-arm64/OpenPPX Desktop.app/Contents/Resources/app.asar",
                        "dist-electron/preload/index.cjs",
                    ),
                ),
            ]
        )
    return tuple(steps)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skip-python", action="store_true", help="Skip pytest and compileall.")
    parser.add_argument("--skip-build", action="store_true", help="Skip the production build and output check.")
    parser.add_argument(
        "--package",
        action="store_true",
        help="Also build and verify the unsigned macOS ARM64 directory package.",
    )
    parser.add_argument("--list", action="store_true", help="Print the commands without running them.")
    return parser.parse_args()


def main() -> int:
    """Execute every selected gate and stop at the first actionable failure."""
    args = _parse_args()
    root = Path(__file__).resolve().parents[1]
    if args.package and args.skip_build:
        print("--package requires the production build; remove --skip-build.", file=sys.stderr)
        return 2
    steps = verification_steps(
        root,
        include_python=not args.skip_python,
        include_build=not args.skip_build,
        include_package=args.package,
    )
    for index, step in enumerate(steps, 1):
        rendered = " ".join(step.command)
        print(f"[{index}/{len(steps)}] {step.name}: {rendered}", flush=True)
        if args.list:
            continue
        executable = Path(step.command[0]) if "/" in step.command[0] else None
        if executable is not None and not executable.exists():
            print(
                f"Missing local tool: {executable}. Install workspace dependencies before verification.",
                file=sys.stderr,
            )
            return 2
        try:
            subprocess.run(step.command, cwd=step.cwd, check=True)
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"Verification failed at '{step.name}': {exc}", file=sys.stderr)
            return 1
    print(f"Verification passed ({len(steps)} steps).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
