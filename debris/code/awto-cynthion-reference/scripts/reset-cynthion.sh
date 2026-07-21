#!/usr/bin/env bash
# Reset Cynthion back to a state where `cynthion run facedancer` works.
#
# Hardware context: a CONTROL_SWITCH mux (controlled by the Apollo ARM MCU)
# connects the CONTROL USB port to either the FPGA/PHY or Apollo itself.
# When facedancer gateware loads, Apollo switches USB to the FPGA so moondancer
# can drive the CONTROL port.  Apollo MCU itself keeps running the whole time.
#
# When moondancer is running normally: the FPGA gateware USB interface is alive
# and responds to the force_offline handoff request, which tells Apollo to
# reclaim the switch and reconfigure the FPGA.
#
# When moondancer firmware has hung (panic, canary fire, stack overflow): the
# FPGA USB interface is unresponsive, so the force_offline handoff times out.
# Apollo is still running but cannot be reached via USB because the switch still
# points at the FPGA.  Apollo MUST assert the switch to reclaim USB — but that
# requires Apollo firmware support (watchdog / manual trigger) which is not yet
# implemented (see issue #17).
#
# Until #17 is implemented: power-cycle is required to reset a hung device.
#
# After a successful reset the device re-enumerates as 1d50:615c (Apollo) and
# then `cynthion run facedancer` reloads the gateware.
#
# Run from repo root: ./scripts/reset-cynthion.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VENV="$ROOT/venv"

if [ ! -f "$VENV/bin/python" ]; then
    echo "venv not found — run ./scripts/setup-venv.sh first"
    exit 1
fi

if ! lsusb | grep -q "1d50:"; then
    echo "No Cynthion detected on USB."
    echo "→ Power-cycle the device (unplug and replug the USB cable)."
    exit 1
fi

"$VENV/bin/python" - <<'EOF'
import sys
import usb.core
from apollo_fpga import ApolloDebugger

FPGA_VID, FPGA_PID = 0x1d50, 0x615b

if usb.core.find(idVendor=FPGA_VID, idProduct=FPGA_PID) is None:
    print("Facedancer interface not found — device may already be in Apollo mode.")
    sys.exit(0)

print("Requesting handoff from FPGA gateware to Apollo...")
try:
    device = ApolloDebugger(force_offline=True)
    device.soft_reset()
    device.allow_fpga_takeover_usb()
    device.close()
    print("Reset complete.")
    sys.exit(0)
except Exception as e:
    print(f"Handoff failed: {e}")
    print()
    print("The FPGA USB interface is unresponsive — moondancer firmware has hung.")
    print("Apollo MCU is still running but the CONTROL_SWITCH still points at the FPGA.")
    print()
    print("Fix (issue #17, not yet implemented): Apollo watchdog asserts CONTROL_SWITCH")
    print("to reclaim USB when it detects a hung VexRiscv.")
    print()
    print("For now: power-cycle the device (unplug and replug the USB cable).")
    print("         Then run:  cynthion run facedancer")
    sys.exit(1)
EOF
