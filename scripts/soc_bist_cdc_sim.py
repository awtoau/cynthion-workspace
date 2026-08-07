#!/usr/bin/env python3
#
# The BIST register window across two clocks, with the failure it avoids. See #226.
# SPDX-License-Identifier: BSD-3-Clause

"""Does the `sync` <-> `hr` crossing survive unequal clocks?

## Why this exists before any bitstream

The BIST rig puts the HyperRAM engine in `hr`, off the second PLL, and the CPU in
`sync`. Those are deliberately different frequencies -- that is the whole point,
so a HyperRAM ladder rung stops dragging the CPU clock with it. Which means every
register write, every `go` and every counter read crosses a domain boundary.

This project has already paid for getting that wrong once. `stream_buffer.py`:

    A `SyncFIFOBuffered` between `sync` at 80 MHz and `usb` at 60 worked
    perfectly while both were 60 MHz, then produced a stream with correct
    counter VALUES and dropped CHARACTERS -- `tic 00000`, `tck 000001`.

**It worked while the clocks were equal.** A simulation at one ratio, or at 1:1,
would have passed and proved nothing. So every check here runs at several
ratios, in both directions, and the equal case is included precisely because it
is the one that hides the fault.

## The negative control, which is the point of the file

A `go` that crosses as a LEVEL is lost whenever the source pulse is shorter than
one destination clock period. That is not hypothetical here: `hr` is CK/2, so at
CK 100 with `sync` at 60 the destination is slower than the source, and a
one-`sync`-cycle pulse can fall entirely between two `hr` edges.

So this simulates both: the toggle-and-edge-detect crossing that ships, and a
deliberately-wrong level crossing. **The level one must FAIL** at a ratio where
the real one passes. Without that, "the crossing works" is a claim about a
harness that might not be able to detect a lost pulse at all -- which is the
shape of every wrong turn in the HyperRAM work.

    ./scripts/soc_bist_cdc_sim.py
    ./scripts/soc_bist_cdc_sim.py -v
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "gateware" / "soc" / "peripherals"))

from amaranth import ClockDomain, Elaboratable, Module, Signal  # noqa: E402
from amaranth.lib.cdc import FFSynchronizer  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

# Ratios to run every check at. `hr` is CK/2 on the DQS path, so with `sync`
# pinned at 60 MHz these span CK 100 (hr slower than sync) through CK 360 (much
# faster). 1:1 is included because it is the case that hid the last CDC bug.
RATIOS = [
    ("hr slower  (CK 100, sync 60)", 60.0, 50.0),
    ("hr equal   (CK 120, sync 60)", 60.0, 60.0),
    ("hr faster  (CK 180, sync 60)", 60.0, 90.0),
    ("hr much faster (CK 360)", 60.0, 180.0),
]


class PulseCross(Elaboratable):
    """Toggle-and-edge-detect: what ships. Ratio-independent by construction."""

    def __init__(self):
        self.i = Signal()
        self.o = Signal()

    def elaborate(self, platform):
        m = Module()
        toggle = Signal()
        with m.If(self.i):
            m.d.sync += toggle.eq(~toggle)
        synced = Signal()
        m.submodules += FFSynchronizer(toggle, synced, o_domain="hr")
        last = Signal()
        m.d.hr += last.eq(synced)
        m.d.comb += self.o.eq(synced ^ last)
        return m


class LevelCross(Elaboratable):
    """The deliberately-wrong one: a level, synchronised. THE CONTROL.

    Correct only while the source pulse outlives a destination clock period.
    It is here to be seen failing -- a crossing test that cannot show a lost
    pulse is not evidence that pulses arrive.
    """

    def __init__(self):
        self.i = Signal()
        self.o = Signal()

    def elaborate(self, platform):
        m = Module()
        m.submodules += FFSynchronizer(self.i, self.o, o_domain="hr")
        return m


class Fixture(Elaboratable):
    def __init__(self, crossing):
        self.crossing = crossing
        self.i = Signal()
        self.o = Signal()

    def elaborate(self, platform):
        m = Module()
        m.domains.sync = ClockDomain()
        m.domains.hr = ClockDomain()
        m.submodules.x = self.crossing
        m.d.comb += [self.crossing.i.eq(self.i), self.o.eq(self.crossing.o)]
        return m


def count_arrivals(crossing, sync_mhz, hr_mhz, pulses=8, gap=7):
    """Send `pulses` one-cycle pulses in `sync`; count arrivals in `hr`."""
    dut = Fixture(crossing)
    sim = Simulator(dut)
    # Periods in seconds; this Amaranth takes a float, not a Period.
    sim.add_clock(1e-6 / sync_mhz, domain="sync")
    sim.add_clock(1e-6 / hr_mhz, domain="hr")

    arrived = 0

    async def source(ctx):
        for _ in range(pulses):
            ctx.set(dut.i, 1)
            await ctx.tick("sync")
            ctx.set(dut.i, 0)
            for _ in range(gap):
                await ctx.tick("sync")
        for _ in range(gap * 4):
            await ctx.tick("sync")

    async def sink(ctx):
        nonlocal arrived
        # Sampled in `hr`, which is where the engine consumes it.
        for _ in range(int((pulses * (gap + 1) + gap * 4) * hr_mhz / sync_mhz) + 8):
            _, _, o = await ctx.tick("hr").sample(dut.o)
            if o:
                arrived += 1

    sim.add_testbench(source)
    sim.add_testbench(sink)
    sim.run()
    return arrived


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()

    emit("soc_bist_cdc_sim: the BIST register window across two clocks")
    emit("")
    checks = Checks(emit)
    SENT = 8

    for label, sync_mhz, hr_mhz in RATIOS:
        got = count_arrivals(PulseCross(), sync_mhz, hr_mhz, pulses=SENT)
        checks.check(f"pulse crossing, {label}: {got}/{SENT} arrive",
                     got == SENT)

    # The control. At least one ratio must lose pulses through a level crossing,
    # or this file proves nothing about the one that ships.
    lost_somewhere = False
    for label, sync_mhz, hr_mhz in RATIOS:
        got = count_arrivals(LevelCross(), sync_mhz, hr_mhz, pulses=SENT)
        if got != SENT:
            lost_somewhere = True
        checks.note(f"control (level crossing), {label}: {got}/{SENT} arrive")
    checks.check("NEGATIVE CONTROL: a level crossing loses pulses somewhere, "
                 "so this harness can detect a lost pulse",
                 lost_somewhere)

    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
