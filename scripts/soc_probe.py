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
    `tmp/awto_soc/build/<variant>/top.bit`.

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
    7. clocks          LOCK, no MISMATCH  the PLL locked and `sync` measures right
    8. PHY reset       scratch cleared  RESETB reaches the pad (#241)
    9. scheduler       late <= gap      the two are the same measurement

Checks 1-6 ask "is this our design, and is it running". 7-9 ask "is what it
says about itself TRUE", which is a different question and the one this project
keeps getting wrong -- a dead PLL reported its intended rate from a constant, a
reset pad tied de-asserted answered its identity registers anyway, and a
scheduler measured its lateness against a zero instant and reported boot time.
None of the three produced a symptom, and none is visible to the emulator.

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

        # -- 7..9, what the running design REPORTS about itself. --------------
        #
        # Everything above answers "is this our design and is it running". These
        # answer "is what it says about itself true", which is a different
        # question and the one this project keeps getting wrong: a dead PLL
        # reported its intended rate from a constant, a reset pad tied
        # de-asserted answered its identity registers anyway, and a scheduler
        # measured its own lateness against a zero instant. None of the three
        # produced a symptom. All three are checkable in a second from here.
        #
        # They live in this script rather than in `soc_test.py` because the
        # emulator cannot see any of them: it has no PLL, no PHY, and a boot
        # short enough to hide the third.
        clock_checks(checks, output)
        phy_reset_check(checks)
        scheduler_check(checks)

    return checks.summary()


def clock_checks(checks, info):
    """The clocks are what the gateware says they are -- measured, not declared.

    `ClockMonitor` counts `sync` against the 60 MHz oscillator, the one clock on
    this board that cannot be wrong because it is a discrete part. `info` prints
    the measurement beside the declaration and says `CLOCK MISMATCH` when they
    disagree.

    Asserting on it here rather than leaving it to be read: `gateware_id` once
    reported `sync 30000000` from a constant while the PLL was not locked and
    `sync` was not oscillating at all. Nothing downstream noticed, because
    everything downstream took the constant's word for it.
    """
    measured = re.search(r"measured sync (\d+) kHz, pll (\w+)", info)

    # "No measurement yet" is its own answer and must not read as a pass: the
    # window is 1 ms and this runs seconds after boot, so an incomplete one means
    # the counter is not running, which is the same silence as a stopped clock.
    checks.check(
        "the fabric has measured `sync` at all"
        + (f" -- {measured.group(1)} kHz" if measured else ""),
        measured is not None,
        "`info` printed no clock measurement. `NO MEASUREMENT YET` means the "
        "1 ms window never completed, which seconds after boot means the "
        "counter is not being clocked.")

    checks.check(
        "the PLL is locked -- `sync` is a real clock, not a constant's claim",
        measured is not None and measured.group(2) == "locked",
        "`info` does not report the PLL locked. An unlocked PLL leaves `sync` "
        "held in reset for ever while every declared frequency still reads "
        "back correctly, because they are constants in the bitstream -- which "
        "is exactly how an unconnected CLKFB cost a two-rebuild bisect.")

    checks.check(
        "measured `sync` agrees with what the gateware declares",
        "CLOCK MISMATCH" not in info,
        "`info` reports a measured clock that differs from the declared one by "
        "more than 1%. The measurement is counted against the board's 60 MHz "
        "oscillator -- a discrete part, and the one clock here that cannot be "
        "wrong -- so it is the one to believe. The declaration is a constant "
        "somebody chose.")


def phy_reset_check(checks):
    """RESETB reaches the TARGET PHY (#241).

    Not "the command ran" -- the pad. `phy reset` writes 0x5a to the PHY's
    scratch register, verifies it took, resets, and reads back; the USB334x
    datasheet Rev 1.2 Table 7.1 gives the scratch default as 00h and section
    5.6.2 says a RESETB cycle restores it.

    This is a post-load check because the failure it catches is a WARM one:
    cold power-up works regardless, since the part's own POR runs before the
    FPGA is configured. What breaks is reconfiguration -- which is exactly what
    a load is, and exactly when this script runs.
    """
    output = shell("phy reset")
    reached = "RESET REACHED THE PHY" in output
    did_not = "RESET DID NOT REACH" in output
    checks.check(
        "the TARGET PHY's RESETB pad moves -- scratch returned to its default",
        reached,
        "`phy reset` reported "
        + ("that the pad never moved, so the PHY has no reset path (#241)."
           if did_not else
           f"neither outcome. Received: {output.strip()[-200:] or '(nothing)'}"))


def scheduler_check(checks):
    """The task's lateness and its achieved period are the same measurement.

    Lateness IS the achieved gap minus the period, so `late <= gap` is an
    identity. Asserting it catches a lateness measured from an origin the gap
    does not share -- which is what happened: the first poll's lateness was taken
    against a zero instant, so it reported the whole of boot, stood as the worst
    case for the session, and dominated the mean.

    Only the board can catch that one. Under the emulator boot is short enough
    that the bad first sample is indistinguishable from a good one, which is why
    `soc_test.py`'s version of this check passes either way.
    """
    output = shell("rtic")
    late = re.search(r"late\s+worst\s+(\d+) us", output)
    gap = re.search(r"gap\s+worst\s+(\d+) ms", output)

    if late is None or gap is None:
        checks.check("the scheduler reports its lateness and its period", False,
                     "`rtic` did not print both figures. Received: "
                     f"{output.strip()[-200:] or '(nothing)'}")
        return

    late_us, gap_us = int(late.group(1)), int(gap.group(1)) * 1000
    checks.check(
        f"scheduler lateness fits inside the achieved period "
        f"({late_us} us worst, in a {gap_us // 1000} ms gap)",
        late_us <= gap_us,
        f"the worst lateness ({late_us} us) exceeds the worst gap it was "
        f"measured within ({gap_us} us). These come from the same instants, so "
        f"this is not a tolerance being exceeded -- one of them is measured "
        f"from an origin the other does not share.")


if __name__ == "__main__":
    raise SystemExit(main())
