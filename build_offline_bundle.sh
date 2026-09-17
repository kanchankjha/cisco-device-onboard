#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
WHEELHOUSE=${WHEELHOUSE:-"$ROOT_DIR/offline/wheelhouse"}
TARGET_PLATFORMS=${TARGET_PLATFORMS:-"manylinux2014_x86_64"}
DEFAULT_PYTHON_VERSIONS="36 37 38 39 310 311 312 313 314"
PYTHON_VERSIONS=${PYTHON_VERSIONS:-${PYTHON_VERSION:-$DEFAULT_PYTHON_VERSIONS}}
PYTHON_ABI_OVERRIDE=${PYTHON_ABI:-}
PYTHON_IMPLEMENTATION=${PYTHON_IMPLEMENTATION:-cp}

rm -rf "$ROOT_DIR/build" "$ROOT_DIR/dist" "$ROOT_DIR"/*.egg-info "$WHEELHOUSE"
mkdir -p "$WHEELHOUSE"

cat > "$WHEELHOUSE/README.md" <<'EOF'
# Offline wheelhouse

This directory is included in source archives so the package can be installed on an offline VM or workstation.

The wheel bundle is generated for the requested CPython versions and target platforms. Python 3.6 and 3.7 use legacy-compatible dependency versions; newer interpreters use current supported dependency lines.
EOF

for PYTHON_VERSION in $PYTHON_VERSIONS; do
  case "$PYTHON_VERSION" in
    36) DEFAULT_PYTHON_ABI=cp36m ;;
    37) DEFAULT_PYTHON_ABI=cp37m ;;
    *) DEFAULT_PYTHON_ABI="cp${PYTHON_VERSION}" ;;
  esac
  PYTHON_ABI_FOR_TARGET=${PYTHON_ABI_OVERRIDE:-$DEFAULT_PYTHON_ABI}

  if [[ "$PYTHON_VERSION" == "36" ]]; then
    PYTHON_REQUIREMENTS=(
      'setuptools>=58,<69'
      'wheel>=0.37.1,<0.48'
      'dataclasses>=0.8'
      'pexpect>=4.9'
      'PyYAML>=6.0.1,<6.0.2'
      'requests>=2.26,<2.27'
    )
  elif [[ "$PYTHON_VERSION" == "37" ]]; then
    PYTHON_REQUIREMENTS=(
      'setuptools>=58,<69'
      'wheel>=0.37.1,<0.48'
      'pexpect>=4.9'
      'PyYAML>=6.0.1,<6.0.2'
      'requests>=2.31'
    )
  else
    PYTHON_REQUIREMENTS=(
      'setuptools>=58,<69'
      'wheel>=0.37.1,<0.48'
      'pexpect>=4.9'
      'PyYAML>=6.0'
      'requests>=2.31'
    )
  fi

  for TARGET_PLATFORM in $TARGET_PLATFORMS; do
    echo "Downloading wheels for $TARGET_PLATFORM / $PYTHON_IMPLEMENTATION $PYTHON_VERSION / $PYTHON_ABI_FOR_TARGET"
    "$PYTHON_BIN" -m pip download \
      --dest "$WHEELHOUSE" \
      --only-binary=:all: \
      --platform "$TARGET_PLATFORM" \
      --implementation "$PYTHON_IMPLEMENTATION" \
      --python-version "$PYTHON_VERSION" \
      --abi "$PYTHON_ABI_FOR_TARGET" \
      "${PYTHON_REQUIREMENTS[@]}"
  done
done

"$PYTHON_BIN" setup.py sdist --dist-dir "$ROOT_DIR/dist"

echo "Offline wheelhouse: $WHEELHOUSE"
echo "Source tarball: $ROOT_DIR/dist/$(ls -1 "$ROOT_DIR/dist"/*.tar.gz | xargs -n 1 basename)"
echo "Targets: $TARGET_PLATFORMS / $PYTHON_IMPLEMENTATION / $PYTHON_VERSIONS"
