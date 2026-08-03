#!/usr/bin/env bash
set -euo pipefail

# Build and inspect the current OpenPPX wheel without downloading dependencies.
# Run from any directory after activating a development environment that already
# contains the project's runtime and build dependencies.

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SMOKE_DIR="$(mktemp -d "${TMPDIR:-/tmp}/openppx-install-smoke.XXXXXX")"

cleanup() {
  rm -rf "${SMOKE_DIR}"
}
trap cleanup EXIT

cd "${ROOT_DIR}"

echo "[smoke] building wheel"
python -m build --wheel --no-isolation --outdir "${SMOKE_DIR}/dist" .

WHEEL_PATH="$(find "${SMOKE_DIR}/dist" -maxdepth 1 -name 'openppx-*.whl' -print -quit)"
if [[ -z "${WHEEL_PATH}" ]]; then
  echo "OpenPPX wheel was not created." >&2
  exit 1
fi

echo "[smoke] installing wheel into isolated target"
python -m pip install --no-deps --target "${SMOKE_DIR}/site" "${WHEEL_PATH}"

cd "${SMOKE_DIR}"
export PYTHONPATH="${SMOKE_DIR}/site"

echo "[smoke] checking import and packaged Skills"
python -c "import importlib.resources as r, openppx; root = r.files('openppx').joinpath('skills'); names = {item.name for item in root.iterdir() if item.is_dir()}; assert {'cron', 'self-observe'} <= names; print(f'OpenPPX import: {openppx.__file__}; Skills: {len(names)}')"

echo "[smoke] checking CLI"
python -m openppx.cli --help >/dev/null
python -m openppx.cli node --help >/dev/null

echo "[smoke] passed"
