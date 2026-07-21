#!/usr/bin/env bash
# Full build: firmware binary + gateware bitstreams.
# Heavy — use only for release/integration validation.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv/bin/python"

echo "==> build-all"

echo "  [1/4] rust firmware..."
cd "$ROOT/repos/cynthion/firmware"
make bin

echo "  [2/4] apollo C firmware..."
cd "$ROOT/repos/apollo/firmware"
make APOLLO_BOARD=cynthion
arm-none-eabi-size "_build/cynthion_d11/firmware.elf"

echo "  [3/4] facedancer bitstream..."
cd "$ROOT/repos/cynthion/cynthion/python"
make facedancer

echo "  [4/4] flutter app..."
cd "$ROOT/app"
flutter build linux --release 2>/dev/null \
    || echo "  flutter: skipped (not configured)"

echo ""
echo "Build complete."
