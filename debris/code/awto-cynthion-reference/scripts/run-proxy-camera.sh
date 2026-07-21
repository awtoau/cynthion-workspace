#!/usr/bin/env bash
# Load facedancer gateware onto Cynthion then run the UTi261M camera proxy.
#
# The Cynthion must be connected (any mode — Apollo 1d50:60e6 or
# Analyzer/Facedancer 1d50:615b). The script loads the facedancer gateware
# unconditionally, which also recovers from a bad proxy state (awtoau/cynthion#7):
# cynthion run facedancer uses ApolloDebugger(force_offline=True) which requests
# a USB handoff even when the moondancer interface is unresponsive.
#
# Run from repo root: ./scripts/run-proxy-camera.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/venv"
LOG="$ROOT/tmp/proxy-run.log"
mkdir -p "$ROOT/tmp"

if [ ! -f "$VENV/bin/python" ]; then
    echo "venv not found — run ./scripts/setup-venv.sh first"
    exit 1
fi

if ! lsusb | grep -q "1d50:"; then
    echo "No Cynthion detected on USB (expected 1d50:60e6 or 1d50:615b)."
    exit 1
fi

echo "Loading facedancer gateware onto Cynthion..." | tee "$LOG"
"$VENV/bin/cynthion" run facedancer 2>&1 | tee -a "$LOG"

echo "Waiting for Cynthion to reappear with moondancer interface (subclass 0x20)..." | tee -a "$LOG"
until "$VENV/bin/python" -c "
import usb1, sys
with usb1.USBContext() as ctx:
    for dev in ctx.getDeviceList():
        if dev.getVendorID() == 0x1d50 and dev.getProductID() == 0x615b:
            for cfg in dev:
                for iface in cfg:
                    for s in iface:
                        if s.getSubClass() == 0x20:
                            sys.exit(0)
    sys.exit(1)
" 2>/dev/null; do true; done

echo "Moondancer ready. Starting UTi261M camera proxy (0x0bda:0x5830)..." | tee -a "$LOG"
"$VENV/bin/python" "$ROOT/tmp/proxy-camera.py" 2>&1 | tee -a "$LOG"
