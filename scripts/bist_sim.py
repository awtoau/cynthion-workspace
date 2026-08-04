#!/usr/bin/env python3
#
# Exercise the shared CPU-free BIST control and comparator.
# SPDX-License-Identifier: BSD-3-Clause

"""Checks the sticky verdict and negative control used by standalone tests.

Output goes to stdout and to ``tmp/logs/dev.log``.
"""

import sys
from pathlib import Path

from amaranth.sim import Simulator

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "ecp5-test"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

from bist import BISTAddresses, BISTHarness  # noqa: E402


def main():
    failed = 0

    def check(condition, what):
        nonlocal failed
        mark = "PASS" if condition else "FAIL"
        emit(f"  {mark} {what}")
        failed += not condition

    dut = BISTHarness(
        applet_id=0x42495354,
        addresses=BISTAddresses(1, 2, 3, 4, 5, 6, 7),
        width=16, simulate=True)

    async def bench(ctx):
        ctx.set(dut.busy, 1)
        ctx.set(dut.actual, 0x1234)
        ctx.set(dut.golden, 0x1234)
        ctx.set(dut.check, 1)
        await ctx.tick()
        check(ctx.get(dut.checks) == 1, "a qualified comparison is counted")
        check(ctx.get(dut.errors) == 0, "matching design values stay clean")

        ctx.set(dut.check, 0)
        ctx.set(dut.done, 1)
        await ctx.tick()
        ctx.set(dut.done, 0)
        check(ctx.get(dut.done_sticky) == 1, "a one-cycle done pulse is retained")

        ctx.set(dut.actual, 0x1235)
        ctx.set(dut.check, 1)
        await ctx.tick()
        check(ctx.get(dut.error) == 1, "a one-cycle mismatch sets sticky error")
        check(ctx.get(dut.errors) == 1, "the mismatch counter records the event")

        ctx.set(dut.actual, 0x1234)
        await ctx.tick()
        check(ctx.get(dut.error) == 1, "a later match cannot erase an error")

        ctx.set(dut.check, 0)
        ctx.set(dut.sim_go, 1)
        await ctx.tick()
        ctx.set(dut.sim_go, 0)
        check(ctx.get(dut.error) == 0 and ctx.get(dut.checks) == 0,
              "go starts a fresh measurement")

        ctx.set(dut.sim_negative, 1)
        ctx.set(dut.actual, 0x1234)
        ctx.set(dut.check, 1)
        await ctx.tick()
        check(ctx.get(dut.errors) == 1,
              "negative control rejects the otherwise-correct value")

        ctx.set(dut.check, 0)
        ctx.set(dut.sim_go, 1)
        await ctx.tick()
        ctx.set(dut.sim_go, 0)
        ctx.set(dut.actual, 0xEDCB)
        ctx.set(dut.check, 1)
        await ctx.tick()
        check(ctx.get(dut.errors) == 0,
              "negative control compares at the selected signal width")

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    sim.run()

    emit(f"summary: {9 - failed}/9 checks, {'failed' if failed else 'clean'}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
