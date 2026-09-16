# Offline wheelhouse

This directory is included in source archives so the package can be installed on an offline VM or workstation.

The committed wheel bundle targets common Linux, macOS, and Windows 64-bit hosts with CPython 3.12. To refresh it for different targets, run `./build_offline_bundle.sh` on a machine with internet access and set `TARGET_PLATFORMS`, `PYTHON_VERSION`, and `PYTHON_ABI` as needed.
