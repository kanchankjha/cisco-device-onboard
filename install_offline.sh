#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
VENV_DIR=${1:-"$ROOT_DIR/.venv"}
WHEELHOUSE=${WHEELHOUSE:-"$ROOT_DIR/offline/wheelhouse"}

if [[ ! -d "$WHEELHOUSE" ]] || [[ -z "$(find "$WHEELHOUSE" -maxdepth 1 -type f -name '*.whl' -print -quit)" ]]; then
  echo "ERROR: offline wheelhouse is empty: $WHEELHOUSE" >&2
  echo "Build or download a tarball that includes offline/wheelhouse/*.whl." >&2
  exit 1
fi

echo "Installing from offline wheelhouse: $WHEELHOUSE"
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --no-index --find-links "$WHEELHOUSE" 'setuptools>=68' 'wheel>=0.41'
"$VENV_DIR/bin/python" -m pip install --no-index --find-links "$WHEELHOUSE" --no-build-isolation "$ROOT_DIR"

"$VENV_DIR/bin/console-upgrade" --help >/dev/null
"$VENV_DIR/bin/dashboard-config" --help >/dev/null

echo "Installed cisco-device-onboard into $VENV_DIR"
echo "Run: source $VENV_DIR/bin/activate"
echo "Commands: console-upgrade, dashboard-config"
