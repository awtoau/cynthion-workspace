#!/usr/bin/env python3
#
# Negative control for the fabric test: prove the mismatch detector fires.
# See awtoau/pluribus#98.
# SPDX-License-Identifier: BSD-3-Clause

"""
Arms the fabric test's runtime negative control and requires every round to
mismatch.

A self-checking test that never reports a failure is indistinguishable from a
test that cannot report one. The passing run in `fabric_run.py` is only evidence
about the silicon if the sticky flag, the mismatch counter and the signature
comparison would all have lit up had the fabric got the answer wrong -- and
nothing in a passing run demonstrates that.

The harness complements the design's golden value at runtime, so the clean run
and the control use one configured 20k-LUT design. Every round must mismatch.
If they do:

  * the sticky flag latches and stays latched, so an intermittent fault would
    be recorded rather than missed between polls
  * the counter counts, so a *rate* is measurable and not just a boolean
  * the host-side comparison in `fabric_run.py` sees the same disagreement

which is exactly the machinery the real run depends on, exercised on the same
silicon through the same JTAG path.

This deliberately does not reconfigure. It verifies the loaded design has a
clean baseline before arming the control.

    ./scripts/fabric_control.py
"""

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "repos" / "apollo"))
sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from devlog import emit  # noqa: E402

from fabric.fabric_gateware import (APPLET_ID, REG_ID, REG_SIGNATURE,
                                    REG_ROUNDS, REG_STATUS, REG_GOLDEN,
                                    REG_MISMATCHES, REG_CONTROL)


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--polls", type=int, default=200,
                        help="JTAG polls; a count, not a duration")
    args = parser.parse_args()

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

    if built_golden != signature:
        emit("REFUSING: the loaded bitstream is already reporting a wrong "
             "answer; a negative control needs a clean baseline.")
        return 1

    # GO clears the old verdict while NEGATIVE complements the design's
    # own golden. The same configured design therefore supplies its clean
    # run and its control; a stale or different bitstream cannot impersonate
    # the pair.
    dut.registers.register_write(REG_CONTROL, 0)
    dut.registers.register_write(REG_CONTROL, 0b11)
    emit("negative control armed; every round must mismatch")
    emit()

    first_rounds = dut.registers.register_read(REG_ROUNDS)
    first_mismatches = dut.registers.register_read(REG_MISMATCHES)
    sticky_always = True

    for _ in range(args.polls):
        status = dut.registers.register_read(REG_STATUS)
        if not status & (1 << 2):
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
