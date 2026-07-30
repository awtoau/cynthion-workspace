#!/usr/bin/env python3
#
# Negative control for the fabric test: prove the mismatch detector fires.
# See awtoau/pluribus#98.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reads the fabric test's registers on a bitstream built with a *wrong* golden
constant, and requires the mismatch machinery to have fired.

A self-checking test that never reports a failure is indistinguishable from a
test that cannot report one. The passing run in `fabric_run.py` is only evidence
about the silicon if the sticky flag, the mismatch counter and the signature
comparison would all have lit up had the fabric got the answer wrong -- and
nothing in a passing run demonstrates that.

So this is the control. `fabric_build.py --golden 0xdeadbeef` produces the same
20k-LUT design with a constant that is not the right answer, and the gateware
then compares a correct signature against a wrong reference. Every round must
mismatch. If they do:

  * the sticky flag latches and stays latched, so an intermittent fault would
    be recorded rather than missed between polls
  * the counter counts, so a *rate* is measurable and not just a boolean
  * the host-side comparison in `fabric_run.py` sees the same disagreement

which is exactly the machinery the real run depends on, exercised on the same
silicon through the same JTAG path.

This deliberately does not reconfigure. It reads whatever is loaded, and refuses
to conclude anything if that is not a wrong-constant build -- so it cannot be
run by accident against the real bitstream and reported as a clean control.

    ./scripts/fabric_build.py --golden 0xdeadbeef
    ./scripts/fabric_run.py --rounds 100      # loads it; refuses to score it
    ./scripts/fabric_control.py               # then this
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "fabric_control.log"

sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from fabric.fabric_gateware import (APPLET_ID, REG_ID, REG_SIGNATURE,
                                    REG_ROUNDS, REG_STATUS, REG_GOLDEN,
                                    REG_MISMATCHES)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--polls", type=int, default=200,
                        help="JTAG polls; a count, not a duration")
    args = parser.parse_args()

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("a") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

        emit(f"=== negative control, {time.strftime('%Y-%m-%dT%H:%M:%S%z')} ===")

        from apollo_fpga import ApolloDebugger
        dut = ApolloDebugger()

        applet = dut.registers.register_read(REG_ID)
        if applet != APPLET_ID:
            emit(f"REG_ID {applet:#010x} is not the fabric test "
                 f"({APPLET_ID:#010x}) -- nothing to control for")
            return 1

        built_golden = dut.registers.register_read(REG_GOLDEN)
        signature = dut.registers.register_read(REG_SIGNATURE)
        emit(f"gateware's golden constant: {built_golden:#010x}")
        emit(f"gateware's computed signature: {signature:#010x}")

        if built_golden == signature:
            emit("REFUSING: the loaded bitstream's constant matches what the "
                 "fabric computed, so this is the real test and not a "
                 "control. Build with --golden 0xdeadbeef first.")
            return 1

        emit("the loaded bitstream compares a correct signature against a "
             "wrong reference, so every round must mismatch")
        emit()

        first_rounds = dut.registers.register_read(REG_ROUNDS)
        first_mismatches = dut.registers.register_read(REG_MISMATCHES)
        sticky_always = True

        for _ in range(args.polls):
            status = dut.registers.register_read(REG_STATUS)
            if not status & 1:
                sticky_always = False

        rounds = dut.registers.register_read(REG_ROUNDS) - first_rounds
        mismatches = (dut.registers.register_read(REG_MISMATCHES)
                      - first_mismatches)

        emit(f"over {args.polls} polls: {rounds} rounds, {mismatches} counted "
             f"as mismatched")
        emit(f"sticky flag set on every one of {args.polls} reads: "
             f"{sticky_always}")

        failures = []
        if rounds == 0:
            failures.append("the round counter did not advance")
        if mismatches != rounds:
            failures.append(f"{mismatches} mismatches counted for {rounds} "
                            f"rounds; every round should have mismatched")
        if not sticky_always:
            failures.append("the sticky flag was not set on every read")

        emit()
        if failures:
            for reason in failures:
                emit(f"CONTROL FAILED: {reason}")
            emit("  The detector does not reliably report a wrong answer, so a "
                 "clean run of the real test is not evidence about the fabric.")
            return 1

        emit("CONTROL PASSED -- the detector fires on a wrong answer: every "
             "round counted, and the sticky flag stayed latched across every "
             "read.")
        emit("  So a clean run of the real bitstream is a measurement, not an "
             "absence of measurement.")
        emit(f"log: {LOG}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
