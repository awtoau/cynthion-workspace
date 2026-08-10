#!/usr/bin/env python3
#
# Raise the RISC-V SoC clock until it stops computing correctly.
# See awtoau/cynthion-workspace#110.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds, flashes and verifies the SoC at a series of clock frequencies.

The SoC is constrained to 60 MHz and already meets 76.71 MHz, so 60 is a constraint rather
than a limit. The die is also a 25F rather than the 12F it is marked as -- 20,143 LUT4s
placed and computed correctly at 86.43 MHz, and the two parts share a speed grade
(#116, pluribus#98). Both say there is headroom; neither says
how much, which is what this measures.

## What counts as passing

**Not** "the bitstream built" and **not** "the tty appeared". The firmware prints
`0x12345678 * 3` and a counter, so a passing run means:

- the product reads `369d0368` -- real multiplication, not a stored constant
- the counter **advances between two separate reads** -- executing, not replaying a buffer

A CPU with marginal timing does not stop; it computes the wrong answer. Checking the
arithmetic is what distinguishes "faster" from "faster and wrong", and a bitstream that
merely enumerates proves neither.

## Two things that must move with the clock

**The sideband.** Its bit period is a cycle count fixed at build time, so a design that
raises `sync` and leaves the responder's `clk_freq_hz` alone gets a dead debug link rather
than a slow one. `top.py` derives both from `SYNC_MHZ`, so this only has to
rewrite one constant.

**The `usb` domain must stay at 60 MHz.** The ULPI PHY requires it. Only `sync` and `fast`
are swept.

## Cost

A clean build and a configure per point, a couple of minutes each. The FPGA is
reconfigured repeatedly; nothing is written to flash, so a power cycle restores whatever
was loaded before.

    ./scripts/riscv_clock_ladder.py
    ./scripts/riscv_clock_ladder.py --frequencies 60 80 100
"""

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / "tmp" / "riscv_clock_ladder.json"
GATEWARE = ROOT / "gateware" / "soc" / "top.py"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402
from soc import variant  # noqa: E402

# This variant's build directory (#351).
BUILD = variant.build_dir(ROOT)
BITSTREAM = BUILD / "top.bit"

# 0x12345678 * 3, which the firmware prints. Checking the value rather than merely
# checking that something arrived is what separates "faster" from "faster and wrong".
EXPECTED_PRODUCT = "369d0368"

# Waiting for the tty needs a real wait, not a spin.
#
# An earlier version polled find_tty() 600 times in a bare loop, which completes in
# microseconds and always fails: the FPGA has to reconfigure, the device has to
# re-enumerate, and the kernel has to bind the CDC driver, none of which is instant. It
# reported "no console tty appeared" for a board that was working perfectly, at every
# frequency -- a false negative that would have condemned frequencies that were fine.
#
# Each iteration now runs one `udevadm settle`, which blocks until the kernel's device
# queue drains, so the loop advances only when something has actually changed. The count
# bounds how many settles to wait through before giving up.
TTY_SETTLE_LIMIT = 20


def set_clock(mhz):
    text = GATEWARE.read_text()
    GATEWARE.write_text(re.sub(r"SYNC_MHZ = \d+", f"SYNC_MHZ = {mhz}", text))


def build():
    """Returns (ok, achieved_mhz or None, detail)."""
    result = subprocess.run(
        ["bash", "-c",
         f'source "$HOME/opt/oss-cad-suite/environment" && '
         f'python3.15t {GATEWARE} --build'],
        cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or result.stdout).strip().splitlines()
        return False, None, tail[-1][:90] if tail else "build failed"

    # nextpnr reports achieved frequency per clock; the sync domain is the one being
    # swept. A design can fail timing and still run -- nextpnr's estimate is
    # conservative -- so this is recorded rather than treated as pass/fail.
    timing = BUILD / "top.tim"
    achieved = None
    if timing.exists():
        for line in timing.read_text().splitlines():
            found = re.search(r"Max frequency for clock.*?:\s*([\d.]+)\s*MHz", line)
            if found:
                value = float(found.group(1))
                achieved = value if achieved is None else min(achieved, value)
    return True, achieved, None


def configure():
    result = subprocess.run(
        [sys.executable,
         str(ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"),
         "configure", str(BITSTREAM)],
        cwd=ROOT, capture_output=True, text=True)
    return result.returncode == 0


def verify():
    """Read the console twice. Returns (ok, detail)."""
    import serial
    import usb_ids

    # Settles, and confirms the port opens -- immediately after a reconfigure find_tty()
    # can still return the node from before it, which then fails to open.
    node = usb_ids.wait_for_tty("riscv_console", settles=TTY_SETTLE_LIMIT)
    if not node:
        return False, "no console tty appeared"

    try:
        port = serial.Serial(node, 115200, timeout=3)
        first = port.read(256).decode("ascii", "replace")
        second = port.read(256).decode("ascii", "replace")
        port.close()
    except Exception as error:
        return False, f"{type(error).__name__} reading {node}"

    if EXPECTED_PRODUCT not in first + second:
        return False, f"product {EXPECTED_PRODUCT} not seen -- arithmetic is wrong"

    ticks = re.findall(r"tick ([0-9a-f]{8})", first + second)
    if len(ticks) < 2:
        return False, f"only {len(ticks)} tick(s) seen"
    if ticks[-1] == ticks[0]:
        return False, "counter did not advance -- not executing"
    return True, f"product ok, ticks {ticks[0]} -> {ticks[-1]}"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--frequencies", type=int, nargs="+",
                        default=[60, 70, 80, 90, 100])
    args = parser.parse_args()

    original = re.search(r"SYNC_MHZ = (\d+)", GATEWARE.read_text())
    original = int(original.group(1)) if original else 60

    emit("RISC-V SoC clock ladder")
    emit(f"restoring SYNC_MHZ = {original} when done")
    emit()
    emit("passing means the printed product is correct AND the counter advances;")
    emit("a marginal CPU computes wrong answers rather than stopping.")
    emit()

    results = {}
    try:
        for mhz in args.frequencies:
            set_clock(mhz)
            ok, achieved, detail = build()
            if not ok:
                emit(f"  {mhz:3d} MHz  build failed: {detail}")
                results[mhz] = {"built": False, "detail": detail}
                continue

            margin = f"nextpnr {achieved:.1f} MHz" if achieved else "timing unknown"
            if not configure():
                emit(f"  {mhz:3d} MHz  {margin}  configure failed")
                results[mhz] = {"built": True, "configured": False}
                continue

            good, detail = verify()
            emit(f"  {mhz:3d} MHz  {margin}  "
                 f"{'PASS' if good else '*** FAIL'}  {detail}")
            results[mhz] = {"built": True, "configured": True,
                            "achieved_mhz": achieved, "pass": good,
                            "detail": detail}
    finally:
        set_clock(original)
        emit()
        emit(f"restored SYNC_MHZ = {original}; rebuild to put it back on the board")

    RESULTS.write_text(json.dumps(results, indent=2) + "\n")
    passed = [m for m, r in results.items() if r.get("pass")]
    emit()
    if passed:
        emit(f"highest verified: {max(passed)} MHz")
    else:
        emit("nothing verified")

    return 0


if __name__ == "__main__":
    sys.exit(main())
