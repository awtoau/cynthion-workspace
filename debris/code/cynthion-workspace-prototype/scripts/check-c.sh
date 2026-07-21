#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
echo "==> apollo C firmware build"
cd "$ROOT/repos/apollo/firmware"
make APOLLO_BOARD=cynthion
arm-none-eabi-size "_build/cynthion_d11/firmware.elf"
echo "OK"
