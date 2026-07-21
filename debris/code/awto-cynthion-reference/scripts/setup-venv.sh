#!/usr/bin/env bash
# Recreate the Python 3.11 venv and install cynthion + facedancer.
#
# Installs cynthion from PyPI so the pre-built bitstreams (facedancer.bit,
# moondancer.bin) are available for 'cynthion run facedancer'.
# When making code changes to the cynthion Python package, reinstall from
# local source afterwards: venv/bin/pip install cynthion/python
#
# Run from the repo root: ./scripts/setup-venv.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/venv"
LOG="$ROOT/tmp/setup-venv.log"
mkdir -p "$ROOT/tmp"

echo "Removing old venv..." | tee "$LOG"
rm -rf "$VENV"

echo "Creating venv with python3.11..." | tee -a "$LOG"
python3.11 -m venv "$VENV" 2>&1 | tee -a "$LOG"

echo "Installing cynthion from PyPI (includes pre-built bitstreams)..." | tee -a "$LOG"
"$VENV/bin/pip" install --upgrade pip 2>&1 | tee -a "$LOG"
"$VENV/bin/pip" install cynthion 2>&1 | tee -a "$LOG"

echo "Installing facedancer..." | tee -a "$LOG"
"$VENV/bin/pip" install facedancer 2>&1 | tee -a "$LOG"

echo "Done. Activate with: source venv/bin/activate" | tee -a "$LOG"
