#!/usr/bin/env python3
#
# Configure a RISC-V SoC bitstream and prove the CPU computes correctly.
# See awtoau/cynthion-workspace#110.
# SPDX-License-Identifier: BSD-3-Clause

"""
Put a bitstream on the FPGA and check that the CPU is doing real work.

## What passing means, and why "it enumerated" is not enough

A CPU with marginal timing does not stop -- it computes the wrong answer. So the
test is arithmetic, not liveness. The firmware prints `0x12345678 * 3` from a
`volatile` (forcing a real load and multiply rather than a folded constant) and
then a counter. Passing requires both:

- the product reads `369d0368`
- the tick counter advances between two separate reads

## The banner is transient, and that is what broke the earlier ladder

The firmware prints its product line **once, at boot**. Enumeration after a
reconfigure takes about 0.47 s on this machine (measured, not assumed), and the
banner is emitted inside that window -- so any check that configures, waits for
the tty, and only then opens the port has already missed it. The port opens on
a stream that begins mid-`tick`, the product is absent, and the run is reported
as "arithmetic is wrong" for a board that is working perfectly.

That false negative is not hypothetical: it is why an earlier ladder reported
`*** FAIL` at 90, 100 and 110 MHz, and also why the same ladder failed a
*known-good 60 MHz control*. A test that fails its own control is measuring the
test, not the design.

**The fix is ordering.** The tty from the previous bitstream survives a
reconfigure long enough to be opened, so this opens the port *first*, then
configures, then reads. The banner therefore lands in an already-open buffer.
When no previous device exists the port is opened as soon as one appears, and
the product check is reported as unobserved rather than as a failure -- absence
of evidence gets said out loud instead of being scored as a wrong answer.

## The stale-node trap

`find_tty()` resolves by VID:PID, never by node number -- eleven `ttyACM` nodes
across four vendors live on this workstation and one of them is an ST-LINK. But
immediately after a reconfigure it can still return the node from *before* it,
which then fails to open. So a resolved node is confirmed openable before it is
trusted.

    ./scripts/riscv_verify_bitstream.py tmp/vexii_hello/build/top.bit
"""

import argparse
import re
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "riscv_verify_bitstream.log"

sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "repos" / "apollo"))

# 0x12345678 * 3. Checking the value rather than merely that bytes arrived is
# what separates "runs faster" from "runs faster and wrong".
EXPECTED_PRODUCT = "369d0368"

# Enumeration after an FPGA reconfigure was measured at 0.47 s on this machine.
# 30 s is therefore ~60x the observed figure: long enough that a slow bind is
# never mistaken for a dead design, and bounded so a design that will never
# enumerate still fails in finite time. The loop returns as soon as the device
# appears, so this ceiling costs nothing on a healthy board.
ENUMERATION_CEILING_S = 30.0

# How long to listen for the boot banner. The firmware's inter-tick delay is
# ~1 s at 60 MHz, so 4 s spans several ticks -- enough to see the counter
# advance twice over regardless of where in the cycle the read began.
READ_WINDOW_S = 4.0


def configure(bit):
    result = subprocess.run(
        [sys.executable,
         str(ROOT / "repos" / "apollo" / "apollo_fpga" / "commands" / "cli.py"),
         "configure", str(bit)],
        cwd=str(ROOT), capture_output=True, text=True)
    return result.returncode == 0, (result.stderr or result.stdout).strip()


def wait_for_tty(deadline):
    """Resolve and open the console tty, or return None at the deadline."""
    import serial
    import usb_ids
    while time.monotonic() < deadline:
        node = usb_ids.find_tty("riscv_console")
        if node:
            try:
                return serial.Serial(node, 115200, timeout=READ_WINDOW_S), node
            except Exception:
                pass
        # Drains the kernel's uevent queue, so the loop turns on device
        # activity rather than burning CPU on a bare counter.
        subprocess.run(["udevadm", "settle"], capture_output=True)
    return None, None


def verify(bit, emit):
    """Returns (passed, detail). `passed` is None when the CPU ran but the
    banner was missed, which is a different thing from computing wrongly."""
    ok, detail = configure(bit)
    if not ok:
        return False, f"configure failed: {detail[:120]}"

    # Holding a descriptor open across the reconfigure does not work: the device
    # re-enumerates, the kernel tears the old node down, and the next read
    # raises SerialException on a file that no longer backs a device. So the
    # port must be reopened afterwards -- and quickly, because the banner is
    # printed once and enumeration completes in about 0.47 s.
    #
    # This loop does no waiting of its own beyond `udevadm settle`, so it
    # reopens within milliseconds of the node appearing, which is what puts the
    # descriptor in place before the firmware's first line. When it loses that
    # race the result is reported as INCONCLUSIVE rather than as a failure.
    deadline = time.monotonic() + ENUMERATION_CEILING_S
    port, node = wait_for_tty(deadline)
    if not port:
        return False, "no console tty appeared -- design did not enumerate"
    emit(f"  port {node} opened {'quickly' if time.monotonic() < deadline else ''}")

    text = ""
    deadline = time.monotonic() + READ_WINDOW_S * 2
    try:
        while time.monotonic() < deadline:
            chunk = port.read(256)
            if chunk:
                text += chunk.decode("ascii", "replace")
            if EXPECTED_PRODUCT in text and len(
                    re.findall(r"tick ([0-9a-f]{8})", text)) >= 2:
                break
    except Exception as error:
        return False, f"{type(error).__name__} reading {node}"
    finally:
        try:
            port.close()
        except Exception:
            pass

    ticks = re.findall(r"tick ([0-9a-f]{8})", text)
    advancing = len(ticks) >= 2 and ticks[0] != ticks[-1]

    if "prod " in text and EXPECTED_PRODUCT not in text:
        wrong = re.search(r"prod ([0-9a-f]{8})", text)
        return False, (f"product is {wrong.group(1) if wrong else '?'}, "
                       f"expected {EXPECTED_PRODUCT} -- CPU computing WRONGLY")

    if EXPECTED_PRODUCT in text and advancing:
        return True, f"product ok, ticks {ticks[0]} -> {ticks[-1]}"

    if advancing:
        # The CPU is demonstrably executing; the banner simply predated the
        # open. Saying so beats scoring it as a wrong answer.
        return None, (f"ticks {ticks[0]} -> {ticks[-1]} (advancing), "
                      f"but boot banner was missed -- product unobserved")

    if ticks:
        return False, f"counter static at {ticks[0]} -- not executing"
    return False, f"no output ({len(text)} bytes read)"


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("bitstreams", nargs="+", type=Path)
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    worst = 0
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        for bit in args.bitstreams:
            emit(f"=== {bit}")
            if not bit.exists():
                emit("  missing")
                worst = 1
                continue
            passed, detail = verify(bit, emit)
            label = {True: "PASS", False: "*** FAIL",
                     None: "INCONCLUSIVE"}[passed]
            emit(f"  {label}  {detail}")
            if passed is False:
                worst = 1
        emit(f"log {LOG}")
    return worst


if __name__ == "__main__":
    sys.exit(main())
