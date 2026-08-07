#!/usr/bin/env python3
#
# Can the console's RX buffer be emptied? See #239.
# SPDX-License-Identifier: BSD-3-Clause

"""Does a `StreamBuffer` across two domains actually clear on reset?

    python3 scripts/soc_stream_buffer_sim.py
    python3 scripts/soc_stream_buffer_sim.py -v      # print every beat

Exit status 0 if every check passes. Output goes to the terminal and to
`tmp/logs/dev.log`.

## The defect this is about

Issue #239, from the peripheral audit in #229.

`console_rx_buf` is a `StreamBuffer` with `i_domain="usb"` and
`o_domain="sync"`, so inside it is an `AsyncFIFOBuffered`. **Amaranth puts reset
control entirely in the WRITE domain** and marks the read-side counters
`reset_less`, because a Gray-coded pointer that one side reset and the other did
not would break the invariant the whole crossing depends on.

For a while `usb` had no reset at all: `clocks.py` tied `ResetSignal("usb")` to
0, deliberately, because `usb` is the board oscillator and runs before any PLL is
asked for anything. The consequence was that **neither side could clear the
FIFO**. Nothing was corrupted -- the Gray invariant held precisely because
nothing reset -- but a byte the host sent while `sync` was still waiting for PLL
lock survived into the CPU's first read, so the shell could receive a keystroke
from before it existed.

## Why it is fixed, and why that is not enough on its own

`usb` has a real reset again: the ULPI PHYs' power-on sequence in #241 drives it
from `~phy_ready`, asserted for 128 + 72000 cycles at power-up. That clears the
write domain, and with it the FIFO.

So #239 was fixed as a SIDE EFFECT of an unrelated change, and a side effect is
exactly the kind of fix that gets undone by the next unrelated change. Nothing
was asserting it. This file asserts it.

## The negative control is the point

A check that a FIFO is empty after a reset passes trivially if the testbench
never managed to put anything in it. So every check here comes in a pair:

  * fill the buffer, prove it is NOT empty -- the arrangement can hold data
  * reset the write domain, prove it IS empty
  * and separately, hold the write reset at zero as `clocks.py` used to, and
    prove the data SURVIVES

The third is #239 reproduced. Without it, "the buffer clears" is a claim about a
testbench that might be unable to detect a buffer that does not.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth import ClockDomain, Elaboratable, Module, ResetSignal, Signal  # noqa: E402
from amaranth.hdl import Fragment  # noqa: E402
from amaranth.sim import Simulator  # noqa: E402

from peripherals.stream_buffer import StreamBuffer  # noqa: E402


# The console's depth, from `CONSOLE_RX_DEPTH` in `gateware/soc/top.py`. The
# behaviour does not depend on it; using the real one means a check that passes
# here is a statement about the buffer that ships.
DEPTH = 16

# Domain periods. DIFFERENT, and deliberately so: this is an asynchronous FIFO
# and every crossing bug this project has had was invisible at 1:1. 60 MHz and
# the SoC's `sync` are the same today, which is exactly why the simulation must
# not be.
USB_PERIOD = 1 / 60e6
SYNC_PERIOD = 1 / 71e6


class Harness(Elaboratable):
    """A `StreamBuffer` with its two domains brought out so a testbench can
    reset either one independently.

    The domains are created here rather than by `SocClocks`, because what is
    under test is the FIFO's response to a reset and not how that reset is
    produced. `clocks.py` has its own checks for the second question.
    """

    def __init__(self, *, resettable_write=True):
        self.resettable_write = resettable_write
        self.buffer = StreamBuffer(depth=DEPTH, i_domain="usb", o_domain="sync")
        # Driven by the testbench. Held at 0 in the `resettable_write=False`
        # arrangement, which is what `clocks.py` did before #241.
        self.usb_rst = Signal()

    def elaborate(self, platform):
        m = Module()
        m.domains.usb = ClockDomain()
        m.domains.sync = ClockDomain()
        m.submodules.buffer = self.buffer
        m.d.comb += ResetSignal("usb").eq(
            self.usb_rst if self.resettable_write else 0)
        return m


async def fill(ctx, dut, count, verbose):
    """Push `count` bytes in through the `usb` side."""
    for index in range(count):
        ctx.set(dut.buffer.sink.payload, 0xA0 + index)
        ctx.set(dut.buffer.sink.valid, 1)
        # Wait for the beat to be accepted rather than assuming one tick is
        # enough: `AsyncFIFOBuffered`'s `ready` is a synchronised flag and its
        # timing is not this file's business.
        for _ in range(200):
            await ctx.tick("usb")
            if ctx.get(dut.buffer.sink.ready):
                break
        if verbose:
            emit(f"      wrote {0xA0 + index:#04x}")
    ctx.set(dut.buffer.sink.valid, 0)
    await ctx.tick("usb")


async def occupancy(ctx, dut):
    """Is anything readable on the `sync` side?

    `source.valid` and not a count: the buffered variants present one beat at a
    time, so `valid` is what a consumer sees and is therefore what "the shell can
    receive a keystroke from before it existed" actually means.
    """
    # Two `sync` edges, because the read side's view of a write is two
    # synchroniser stages behind it. Sampling immediately would report an empty
    # buffer that is merely not yet visible, which would make every check here
    # pass for the wrong reason.
    for _ in range(8):
        await ctx.tick("sync")
    return ctx.get(dut.buffer.source.valid)


def build(dut, testbench):
    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(USB_PERIOD, domain="usb")
    sim.add_clock(SYNC_PERIOD, domain="sync")
    sim.add_testbench(testbench)
    return sim


def run_checks(checks, verbose):
    # --- 1. the buffer can hold data at all ---------------------------------
    #
    # First, and separately asserted, because every check below is about data
    # NOT being there and would pass on a buffer that never accepted any.
    dut = Harness()
    seen = {}

    async def testbench(ctx):
        await fill(ctx, dut, DEPTH // 2, verbose)
        seen["filled"] = await occupancy(ctx, dut)

    build(dut, testbench).run()

    checks.check(
        "the buffer holds what the usb side wrote",
        seen.get("filled") == 1,
        f"after {DEPTH // 2} writes the sync side sees valid="
        f"{seen.get('filled')!r}. Every check below asserts that data is GONE, "
        f"and all of them pass on a buffer that never took any -- so this one "
        f"is what stops them being vacuous.")

    # --- 2. resetting the write domain clears it ----------------------------
    dut = Harness()
    seen = {}

    async def testbench(ctx):
        await fill(ctx, dut, DEPTH // 2, verbose)
        seen["before"] = await occupancy(ctx, dut)

        # Held for several `usb` cycles. `AsyncFIFOBuffered`'s reset has to
        # propagate to the read side through a synchroniser, so a single-cycle
        # pulse would be testing the synchroniser's latency rather than the
        # clear. The real one is 1.202 ms.
        ctx.set(dut.usb_rst, 1)
        for _ in range(16):
            await ctx.tick("usb")
        ctx.set(dut.usb_rst, 0)

        seen["after"] = await occupancy(ctx, dut)

    build(dut, testbench).run()

    checks.check(
        "a reset of the WRITE domain empties the buffer",
        seen.get("before") == 1 and seen.get("after") == 0,
        f"held {seen.get('before')!r} before the reset and "
        f"{seen.get('after')!r} after; expected 1 then 0.\n"
        f"Amaranth puts an AsyncFIFO's reset control entirely in the write "
        f"domain, so `usb` is the only side that can clear this one -- and "
        f"`usb` is the ULPI PHYs' power-on reset (#241). If this fails, host "
        f"input from before the CPU existed reaches the shell (#239).")

    # --- 3. the negative control: #239 reproduced ---------------------------
    #
    # `usb` reset tied to 0, which is what `gateware/soc/clocks.py` did between
    # the clocking rework and #241. The read side is `reset_less` by
    # construction, so with the write side unable to reset either, NOTHING can
    # clear the buffer -- and the byte survives.
    #
    # This must FAIL to clear. A check that only ever sees the fixed arrangement
    # cannot tell a buffer that clears from a testbench that cannot fill one.
    dut = Harness(resettable_write=False)
    seen = {}

    async def testbench(ctx):
        await fill(ctx, dut, DEPTH // 2, verbose)
        seen["before"] = await occupancy(ctx, dut)
        # Asserted, and ignored, because the harness ties the domain's reset low
        # exactly as `clocks.py` used to.
        ctx.set(dut.usb_rst, 1)
        for _ in range(16):
            await ctx.tick("usb")
        ctx.set(dut.usb_rst, 0)
        seen["after"] = await occupancy(ctx, dut)

    build(dut, testbench).run()

    checks.check(
        "with the write domain's reset tied off, the data SURVIVES (#239)",
        seen.get("before") == 1 and seen.get("after") == 1,
        f"held {seen.get('before')!r} before and {seen.get('after')!r} after.\n"
        f"This check asserts the DEFECT, deliberately: it is the control for "
        f"the one above it.\n"
        f"If this now reports the buffer clearing, then either the arrangement "
        f"no longer\n"
        f"reproduces #239 -- in which case the check above proves less than it "
        f"claims -- or\n"
        f"`StreamBuffer` has gained a reset path that does not come from its "
        f"write domain,\n"
        f"which would be worth knowing and is not what Amaranth's AsyncFIFO "
        f"does.")

    # --- 4. and it works again afterwards -----------------------------------
    #
    # A clear that left the FIFO unusable would pass every check above. The
    # pointers are Gray-coded and reset from one side only, so "empty" and
    # "wedged" are the same reading until something writes again.
    dut = Harness()
    seen = {}

    async def testbench(ctx):
        await fill(ctx, dut, DEPTH // 2, verbose)
        ctx.set(dut.usb_rst, 1)
        for _ in range(16):
            await ctx.tick("usb")
        ctx.set(dut.usb_rst, 0)
        await occupancy(ctx, dut)

        await fill(ctx, dut, 1, verbose)
        seen["refilled"] = await occupancy(ctx, dut)
        seen["byte"] = ctx.get(dut.buffer.source.payload)

    build(dut, testbench).run()

    checks.check(
        "and the buffer still works after being cleared",
        seen.get("refilled") == 1 and seen.get("byte") == 0xA0,
        f"after the reset a single write gave valid={seen.get('refilled')!r} "
        f"payload={seen.get('byte')!r}; expected 1 and 0xa0.\n"
        f"An empty buffer and a wedged one read the same until something writes "
        f"to it, so\n"
        f"the check above cannot distinguish them on its own.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every beat written")
    args = parser.parse_args()

    emit("stream_buffer.StreamBuffer -- can the console's RX buffer be emptied?")
    emit(f"  usb {1 / USB_PERIOD / 1e6:.0f} MHz, sync {1 / SYNC_PERIOD / 1e6:.0f} MHz "
         f"-- deliberately unequal, because every crossing fault this project "
         f"has had was invisible at 1:1")
    emit("")
    checks = Checks(emit)
    run_checks(checks, args.verbose)
    return checks.summary()


if __name__ == "__main__":
    raise SystemExit(main())
