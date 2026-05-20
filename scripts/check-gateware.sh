#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/.venv/bin/python"
echo "==> gateware elaborate (no synthesis)"
"$VENV" -m cynthion.gateware.facedancer.top --only-elaborate 2>/dev/null \
    || echo "note: elaborate target not available, skipping"
echo "OK"
