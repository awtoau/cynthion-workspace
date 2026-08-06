#!/usr/bin/env python3
#
# After a load: is the FPGA alive, is it OUR design, is the CPU running? See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Verify what is actually on the board, in the order that isolates faults.

## Why this exists

"The board is dead" is not one state, and telling them apart by looking at the
console cannot work -- the console is the last thing in the chain and the first
thing to go quiet. This session lost time to two failures that look identical
from a terminal:

  * the FPGA had lost its SRAM configuration entirely;
  * a **stale bitstream** was configured by hand from a plausible-looking path
    (`tmp/soc/build/top.bit`, an orphan) while the real build sat in
    `tmp/vexii_hello/build/top.bit`.

Both present as silence. Neither is a CPU fault, and neither needs the console to
diagnose.

## The order, and why it is this order

Each check needs only the layers below it, so the FIRST failure names the layer:

    1. JTAG chain      IDCODE           the part answers at all
    2. fabric          ER1 signature    OUR design is configured -- no CPU needed
    3. CPU reset       cpu_reset bit    the CPU is not being held
    4. USB             console tty      the gateware's USB stack enumerated
    5. CPU executing   `info` replies   the core is fetching and running
    6. versions        no MISMATCH      the gateware and firmware are THIS tree

Step 2 is the one that matters most and is the one nothing did before. The ER1
staging sink answers from a constant in the fabric, clocked by TCK, so it
replies whether or not `sync` is running, whether or not the PLL locked, and
whether or not the CPU works. A correct signature proves the bitstream reached
the fabric; a wrong one proves it did not, without any inference.

Step 6 catches the stale-bitstream case by comparing the gateware's USERCODE
against the firmware's own build stamp. The firmware already prints `MISMATCH`
when they disagree -- this asserts on it rather than leaving it to be noticed.

    ./scripts/soc_probe.py
    ./scripts/soc_probe.py --no-console     # JTAG only, no USB or shell
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))

from devlog import emit  # noqa: E402
from sim_check_harness import Checks  # noqa: E402

# The ER1 sink's constant, from `gateware/soc/bus/jtag_stage.py`. It is a
# `Const` in the fabric, so it answers on TCK alone.
WANT_SIGNATURE = 0x4A53


def jtag_status():
    """The ER1 sink's status word, or None if JTAG itself is unreachable."""
    from apollo_fpga import ApolloDebugger
    from soc_jtag_stage import Sink

    debugger = ApolloDebugger()
    with debugger.jtag as chain:
        return Sink(chain).status()


def shell(*commands):
    """Run shell commands through soc_shell and return its output."""
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "soc_shell.py"), *commands],
        capture_output=True, text=True, cwd=ROOT)
    return result.stdout + result.stderr


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-console", action="store_true",
                        help="JTAG checks only; do not touch USB or the shell")
    args = parser.parse_args()

    emit("soc_probe: what is actually on the board")
    emit("")
    checks = Checks(emit)

    # -- 1..3, JTAG only. None of this needs the CPU, USB, or a locked PLL. ----
    status = None
    try:
        status = jtag_status()
        reachable = True
    except Exception as exc:                      # noqa: BLE001 -- reported, not raised
        reachable = False
        emit(f"  JTAG unreachable: {exc}")
    checks.check("JTAG chain answers -- the part is present and Apollo can reach it",
                 reachable)

    if not reachable:
        emit("")
        emit("Nothing below can be tested without JTAG. Check the cable and that")
        emit("no other process holds the Apollo debug interface.")
        return checks.summary()

    signature = status["signature"]
    checks.check(
        f"fabric carries OUR design: ER1 signature {signature:#06x} "
        f"(want {WANT_SIGNATURE:#06x}) -- answers on TCK, so this is true "
        f"whether or not the CPU runs",
        signature == WANT_SIGNATURE)

    if signature != WANT_SIGNATURE:
        emit("")
        emit("The FPGA is not running this design. Either it lost its SRAM")
        emit("configuration, or something else was loaded. Configure the build")
        emit("`soc_run.py` produces -- do NOT hand-type a bitstream path, which")
        emit("is how a stale one from an orphaned build directory gets loaded.")
        return checks.summary()

    checks.check("CPU is not held in reset (`cpu_reset` clear in the JTAG sink)",
                 status["cpu_reset"] == 0)

    if args.no_console:
        emit("")
        emit("console checks skipped (--no-console)")
        return checks.summary()

    # -- 4..6, the USB and CPU layers. ---------------------------------------
    tty = list((Path("/dev/serial/by-id")).glob("*VexiiRiscv_console*")) \
        if Path("/dev/serial/by-id").exists() else []
    checks.check("USB console enumerated -- the gateware's USB stack is running "
                 "and `usb` is close enough to 60.000 MHz to be seen",
                 bool(tty))

    output = shell("info") if tty else ""
    executing = "cpu " in output and "misa" in output
    checks.check("CPU is executing -- the shell answered `info` with a decoded "
                 "`misa`, which it can only do by running code",
                 executing)

    if executing:
        # The stale-bitstream check. The firmware compares the gateware's
        # USERCODE against its own build stamp and says so; this asserts on it.
        mismatch = re.search(r"MISMATCH: this firmware was built from (\S+)", output)
        gateware = re.search(r"gateware\s+(\S+)", output)
        checks.check(
            "gateware and firmware are from the SAME commit"
            + (f" -- gateware {gateware.group(1)}, firmware "
               f"{mismatch.group(1)}" if mismatch and gateware else ""),
            mismatch is None)
        if mismatch:
            emit("")
            emit("The bitstream on the board was built from a different commit")
            emit("than the firmware in flash. Every gateware constant the firmware")
            emit("reads -- clock rates, cache geometry, peripheral bases -- is")
            emit("potentially wrong, and nothing else here would notice.")

    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
