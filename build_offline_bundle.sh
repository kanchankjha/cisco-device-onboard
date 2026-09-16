#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PYTHON_BIN=${PYTHON_BIN:-python3}
WHEELHOUSE=${WHEELHOUSE:-"$ROOT_DIR/offline/wheelhouse"}
TARGET_PLATFORMS=${TARGET_PLATFORMS:-"manylinux2014_x86_64 manylinux2014_aarch64 macosx_10_9_x86_64 macosx_11_0_arm64 win_amd64"}
PYTHON_VERSION=${PYTHON_VERSION:-312}
PYTHON_ABI=${PYTHON_ABI:-cp312}
PYTHON_IMPLEMENTATION=${PYTHON_IMPLEMENTATION:-cp}

rm -rf "$ROOT_DIR/build" "$ROOT_DIR/dist" "$ROOT_DIR"/*.egg-info "$WHEELHOUSE"
mkdir -p "$WHEELHOUSE"

cat > "$WHEELHOUSE/README.md" <<'EOF'
# Offline wheelhouse

This directory is included in source archives so the package can be installed on an offline VM or workstation.

The committed wheel bundle targets common Linux, macOS, and Windows 64-bit hosts with CPython 3.12. To refresh it for different targets, run `./build_offline_bundle.sh` on a machine with internet access and set `TARGET_PLATFORMS`, `PYTHON_VERSION`, and `PYTHON_ABI` as needed.
EOF

for TARGET_PLATFORM in $TARGET_PLATFORMS; do
  echo "Downloading wheels for $TARGET_PLATFORM / $PYTHON_IMPLEMENTATION $PYTHON_VERSION / $PYTHON_ABI"
  "$PYTHON_BIN" -m pip download \
    --dest "$WHEELHOUSE" \
    --only-binary=:all: \
    --platform "$TARGET_PLATFORM" \
    --implementation "$PYTHON_IMPLEMENTATION" \
    --python-version "$PYTHON_VERSION" \
    --abi "$PYTHON_ABI" \
    'setuptools>=68' \
    'wheel>=0.41' \
    'pexpect>=4.9' \
    'PyYAML>=6.0' \
    'requests>=2.31'
done

"$PYTHON_BIN" -m pip install --upgrade build
"$PYTHON_BIN" -m build --sdist

echo "Offline wheelhouse: $WHEELHOUSE"
echo "Source tarball: $ROOT_DIR/dist/$(ls -1 "$ROOT_DIR/dist"/*.tar.gz | xargs -n 1 basename)"
echo "Targets: $TARGET_PLATFORMS / $PYTHON_IMPLEMENTATION $PYTHON_VERSION / $PYTHON_ABI"
