#!/usr/bin/env bash
set -euo pipefail

# Build and inspect the current OpenTeamwork wheel without downloading dependencies.
# Run from any directory after activating a development environment that already
# contains the project's runtime and build dependencies.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openteamwork-install-smoke.XXXXXX")"
PYTHON_BIN="${OPENTEAMWORK_PYTHON:-python}"

cleanup() {
  rm -rf "${SMOKE_DIR}"
}
trap cleanup EXIT

cd "${ROOT_DIR}"

echo "[smoke] building wheel"
"${PYTHON_BIN}" -m build --wheel --no-isolation --outdir "${SMOKE_DIR}/dist" .

WHEEL_PATH="$(find "${SMOKE_DIR}/dist" -maxdepth 1 -name 'openteamwork-*.whl' -print -quit)"
if [[ -z "${WHEEL_PATH}" ]]; then
  echo "OpenTeamwork wheel was not created." >&2
  exit 1
fi

echo "[smoke] installing wheel into isolated target"
"${PYTHON_BIN}" -m pip install --no-deps --target "${SMOKE_DIR}/site" "${WHEEL_PATH}"

cd "${SMOKE_DIR}"
export PYTHONPATH="${SMOKE_DIR}/site"

echo "[smoke] checking import and packaged Skills"
"${PYTHON_BIN}" -c "import importlib.resources as r, openppx; root = r.files('openppx').joinpath('skills'); names = {item.name for item in root.iterdir() if item.is_dir()}; assert {'cron', 'self-observe'} <= names; print(f'OpenTeamwork runtime import: {openppx.__file__}; Skills: {len(names)}')"

echo "[smoke] checking CLI"
"${PYTHON_BIN}" -c "import importlib.metadata as m; dist = m.distribution('openteamwork'); scripts = {ep.name for ep in dist.entry_points if ep.group == 'console_scripts'}; assert scripts == {'otw', 'openteamwork', 'otw-egress-proxy'}, scripts"
"${PYTHON_BIN}" -c "from openppx.command.parser import build_parser; from openppx.product import PRODUCT; assert build_parser().prog == 'otw'; assert PRODUCT.cli_aliases == ('openteamwork',)"

echo "[smoke] passed"
