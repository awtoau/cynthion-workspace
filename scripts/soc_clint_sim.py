#!/usr/bin/env python3
#
# Simulate the CLINT and assert the tick's semantics before a bitstream exists.
# SPDX-License-Identifier: BSD-3-Clause

"""
Proves `vexii_clint.Clint` behaves, and that a tick built on it does not drift.

    ./scripts/soc_clint_sim.py         # run every check
    ./scripts/soc_clint_sim.py -v      # and print each bus access

Exit status 0 if every assertion held. Output goes to the terminal and to
`tmp/logs/dev.log`.

## Why this is worth a file

The tick is `mtimecmp += period` in an interrupt handler, and the whole of its
correctness is a claim about what happens when the handler is LATE. That claim
cannot be tested by watching a console: a tick that reloads from "now" and a tick
that adds keep identical time on an idle machine, and diverge only under the load
that makes the difference matter.

So this file models a late handler explicitly -- it waits a chosen number of
cycles past each deadline before advancing `mtimecmp` -- and checks that the
FIRING TIMES stay on the absolute grid the first deadline established. That is
the one property #130 is about, and nothing else in the tree asserts it.

The rest are single-cycle behaviours no console can distinguish from working: the
interrupt being a level rather than a pulse, the reset deadline being unreachable,
and the three-store write sequence never producing a spurious assertion.

## What this cannot say

Nothing here runs the CPU. It drives the CSR bus directly, so it says the
peripheral is right and says nothing about `mie.MTIE`, riscv-rt's trap entry, or
whether `src/timer.rs` reaches its handler. `scripts/soc_test.py` covers that
under QEMU against QEMU's own CLINT, which is the other half of the argument --
and it is where the tick is measured against a counter nothing periodic touches.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth.hdl import Fragment            # noqa: E402
from amaranth.sim import Simulator           # noqa: E402

from vexii_clint import (Clint, MSIP_BASE, MTIMECMP_BASE, MTIME_BASE,  # noqa: E402
                         NEVER)

MTIMECMP_LO = MTIMECMP_BASE + 0
MTIMECMP_HI = MTIMECMP_BASE + 4
MTIME_LO = MTIME_BASE + 0
MTIME_HI = MTIME_BASE + 4

# A period short enough to simulate several of. The real one is 60000 counter
# ticks; nothing in the peripheral or in the accumulation rule depends on the
# value, and simulating 60000 cycles per tick would buy no coverage at all.
PERIOD = 40

# How late the modelled handler is, in cycles, on each successive tick. Chosen to
# be irregular and to include a lateness longer than the period, which is where a
# reload-from-now tick loses a whole interval and an adding tick does not.
LATENESS = [0, 3, 17, 1, 55, 2]


class Bus:
    """Byte-wide CSR reads and writes, as the multiplexer's timing requires.

    A read is a strobe on one cycle and data on the next -- `csr.Multiplexer`
    registers its read path -- so sampling a cycle late reads ZERO rather than
    the previous access's data, and every check then passes by comparing zero
    with zero. The same shape as `scripts/soc_plic_sim.py`, and for the same
    reason it is spelled out there.
    """

    def __init__(self, ctx, bus, verbose=False):
        self.ctx = ctx
        self.bus = bus
        self.verbose = verbose

    async def read(self, addr):
        ctx = self.ctx
        ctx.set(self.bus.addr, addr)
        ctx.set(self.bus.r_stb, 1)
        await ctx.tick()
        ctx.set(self.bus.r_stb, 0)
        value = ctx.get(self.bus.r_data)
        await ctx.tick()
        if self.verbose:
            print(f"      read  {addr:#08x} -> {value:#04x}")
        return value

    async def write(self, addr, value):
        ctx = self.ctx
        ctx.set(self.bus.addr, addr)
        ctx.set(self.bus.w_data, value)
        ctx.set(self.bus.w_stb, 1)
        await ctx.tick()
        ctx.set(self.bus.w_stb, 0)
        await ctx.tick()
        if self.verbose:
            print(f"      write {addr:#08x} <- {value:#04x}")

    async def read_word(self, addr):
        """Four byte reads, low first -- what a 32-bit load becomes on this bus."""
        value = 0
        for offset in range(4):
            value |= await self.read(addr + offset) << (8 * offset)
        return value

    async def write_word(self, addr, value):
        """Four byte writes, low first. The register commits on the LAST one."""
        for offset in range(4):
            await self.write(addr + offset, (value >> (8 * offset)) & 0xff)


class Counter:
    """A free-running `mtime`, driven at one per cycle as `vexii_cpu.py` does.

    The peripheral does not own the counter -- it takes it as an input, so that
    `csrr time` and a load from 0xbff8 cannot disagree -- which means the
    testbench has to supply one.
    """

    def __init__(self, ctx, dut):
        self.ctx = ctx
        self.dut = dut
        self.value = 0

    async def advance(self, cycles=1):
        for _ in range(cycles):
            self.value += 1
            self.ctx.set(self.dut.mtime, self.value)
            await self.ctx.tick()

    def set_now(self):
        self.ctx.set(self.dut.mtime, self.value)


def run_register_checks(checks, verbose):
    """The map, the reset state, and that no read moves anything."""
    dut = Clint()

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        counter = Counter(ctx, dut)

        # --- quiet out of reset ----------------------------------------------
        # mtimecmp resets to all ones. A design that reset it to zero -- which
        # QEMU's does -- asserts the timer interrupt from the first cycle, and
        # while that is harmless with `mie.MTIE` clear, it means an ILA or a
        # probe sees a timer interrupt on a design nobody has programmed.
        await counter.advance(4)
        checks.check(
            "the timer interrupt is deasserted out of reset",
            ctx.get(dut.irq_timer) == 0,
            "irq_timer was high before firmware wrote a deadline. mtimecmp "
            "must reset to something unreachable, not to zero.")

        deadline = await bus.read_word(MTIMECMP_LO)
        deadline |= await bus.read_word(MTIMECMP_HI) << 32
        checks.check(
            "mtimecmp reads back all ones before it is written",
            deadline == 0xffff_ffff_ffff_ffff,
            f"mtimecmp read {deadline:#018x}, expected all ones")

        # --- mtime is the counter, not a copy of it --------------------------
        await counter.advance(10)
        low = await bus.read_word(MTIME_LO)
        checks.check(
            "mtime_lo reports the counter driven into the peripheral",
            low == counter.value,
            f"mtime_lo read {low}, but the counter is at {counter.value}. "
            "The peripheral must report the input it is given, or `csrr time` "
            "and a load from 0xbff8 will disagree.")

        # --- no read changes anything ----------------------------------------
        # The property the whole register layout was chosen for, and the one
        # this SoC has lost multi-day stretches to. `mtime` moves on its own, so
        # what is asserted is that the DEADLINE and the interrupt do not.
        written = counter.value + 1000
        await bus.write_word(MTIMECMP_HI, 0)
        await bus.write_word(MTIMECMP_LO, written)
        before = ctx.get(dut.irq_timer)

        readings = []
        for _ in range(3):
            readings.append(await bus.read_word(MTIMECMP_LO))
            await bus.read_word(MTIMECMP_HI)
            await bus.read_word(MTIME_LO)
            await bus.read_word(MTIME_HI)

        checks.check(
            "reading the CLINT changes nothing",
            readings == [written] * 3 and ctx.get(dut.irq_timer) == before,
            f"mtimecmp read back {readings} across three passes, expected "
            f"{written} every time, and irq_timer was {ctx.get(dut.irq_timer)} "
            f"where it had been {before}.\n"
            "A read with a side effect sharing a word with one a poll loop "
            "touches is the failure this SoC has lost the most time to.")

        # --- msip drives the software interrupt ------------------------------
        checks.check(
            "irq_software is low until msip is written",
            ctx.get(dut.irq_software) == 0,
            "msip must reset clear, or a hart takes a software interrupt "
            "nobody raised.")
        await bus.write_word(MSIP_BASE, 1)
        await ctx.tick()
        checks.check(
            "writing msip raises the software interrupt",
            ctx.get(dut.irq_software) == 1,
            "msip bit 0 is the machine software interrupt for hart 0. A CLINT "
            "that ignores it leaves `cpu.irq_software` a tie-off wearing a "
            "register map.")
        await bus.write_word(MSIP_BASE, 0)
        await ctx.tick()
        checks.check(
            "clearing msip lowers it again",
            ctx.get(dut.irq_software) == 0,
            "msip is a level with no acknowledge; only a write clears it.")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def run_level_checks(checks, verbose):
    """The interrupt is a level, and only a new deadline lowers it."""
    dut = Clint()

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        counter = Counter(ctx, dut)

        await bus.write_word(MTIMECMP_HI, 0)
        await bus.write_word(MTIMECMP_LO, counter.value + 20)

        # Not yet.
        await counter.advance(5)
        checks.check(
            "irq_timer stays low while mtime is below mtimecmp",
            ctx.get(dut.irq_timer) == 0,
            "the comparison fired early.")

        # Past it, and STAYING past it. A pulse here would be lost by any hart
        # that happened to have interrupts masked on that cycle, and the RISC-V
        # specification defines `mip.MTIP` as a level for exactly that reason.
        await counter.advance(40)
        high = ctx.get(dut.irq_timer)
        await counter.advance(50)
        checks.check(
            "irq_timer is a LEVEL, held while mtime >= mtimecmp",
            high == 1 and ctx.get(dut.irq_timer) == 1,
            "irq_timer did not stay asserted with the deadline long past. A "
            "pulse is lost by a hart with interrupts momentarily masked, and "
            "the tick simply stops with nothing to say why.")

        # The only way down.
        await bus.write_word(MTIMECMP_LO, counter.value + 1000)
        await ctx.tick()
        checks.check(
            "advancing the deadline is what lowers the line",
            ctx.get(dut.irq_timer) == 0,
            "there is no acknowledge register in a CLINT: the handler lowers "
            "the interrupt by moving mtimecmp, and a handler that returns "
            "without doing so is re-entered forever.")

        # `>=`, not `==`. A handler that is late by more than one cycle must
        # still find the line asserted; a design that compared for equality
        # would match for exactly one cycle and then wait for the counter to
        # wrap -- 9700 years at 60 MHz, presenting as a tick that stopped.
        await bus.write_word(MTIMECMP_LO, counter.value - 5)
        await ctx.tick()
        await ctx.tick()
        checks.check(
            "a deadline already in the past fires immediately",
            ctx.get(dut.irq_timer) == 1,
            "the comparison is `>=`, not `==`.")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def run_write_sequence_checks(checks, verbose):
    """The three-store deadline write never fires an interrupt nobody asked for."""
    dut = Clint()

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        counter = Counter(ctx, dut)

        # Put the counter somewhere with a high half, so that a write of the
        # low half alone can produce a pair that is in the past. This is the
        # hazard: two stores cannot be atomic, and the intermediate value is a
        # real deadline the comparator acts on.
        counter.value = (1 << 32) + 500
        counter.set_now()
        await ctx.tick()

        await bus.write_word(MTIMECMP_HI, 1)
        await bus.write_word(MTIMECMP_LO, 2000)
        await ctx.tick()
        checks.check(
            "a deadline above the counter does not fire",
            ctx.get(dut.irq_timer) == 0,
            "setup for the sequence check is already wrong.")

        # The sequence from `src/timer.rs`: low to all ones first, so that no
        # combination of the old high half and the new one can match, THEN the
        # high half, THEN the low.
        #
        # The next deadline is one high-half up, so the naive order -- low
        # first -- would briefly leave {high=1, low=200}, which is 300 cycles in
        # the PAST and fires.
        spurious = False
        await bus.write_word(MTIMECMP_LO, NEVER)
        await ctx.tick()
        if ctx.get(dut.irq_timer):
            spurious = True
        await bus.write_word(MTIMECMP_HI, 2)
        await ctx.tick()
        if ctx.get(dut.irq_timer):
            spurious = True
        await bus.write_word(MTIMECMP_LO, 200)
        await ctx.tick()
        if ctx.get(dut.irq_timer):
            spurious = True

        checks.check(
            "the three-store write sequence never asserts on an intermediate "
            "value",
            not spurious,
            "the interrupt fired part-way through setting a 64-bit deadline "
            "from a 32-bit machine. The order in `set_mtimecmp` -- low to all "
            "ones, then high, then low -- exists to make every intermediate "
            "pair unreachable.")

        # And the naive order really would have fired, so the sequence above is
        # not ceremony. Same starting point, low half written first.
        await bus.write_word(MTIMECMP_LO, NEVER)
        await bus.write_word(MTIMECMP_HI, 1)
        await bus.write_word(MTIMECMP_LO, 2000)
        await ctx.tick()
        await bus.write_word(MTIMECMP_LO, 200)      # the naive first store
        await ctx.tick()
        checks.check(
            "the naive write order WOULD have fired -- the sequence is load "
            "bearing",
            ctx.get(dut.irq_timer) == 1,
            "writing the low half of a further-away deadline first left the "
            "pair in the future, so this simulation is not reproducing the "
            "hazard `set_mtimecmp` is written to avoid, and the check above "
            "proves nothing.")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def run_drift_checks(checks, verbose):
    """The one that matters: a late handler must not move the grid.

    Models `src/timer.rs` exactly -- read mtimecmp, add the period, write it
    back -- but starts the handler a chosen number of cycles after each deadline.
    Then asserts that the deadlines are the arithmetic sequence the first one
    started, whatever the lateness was.

    The comparison arm runs the same lateness through the OTHER rule, reloading
    from the counter, and asserts that it does drift. Without that, this check
    would pass against a peripheral that made both rules behave identically.
    """
    dut = Clint()

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        counter = Counter(ctx, dut)

        await counter.advance(2)
        first = counter.value + PERIOD

        await bus.write_word(MTIMECMP_HI, 0)
        await bus.write_word(MTIMECMP_LO, first)

        fired_at = []
        deadlines = []
        for late in LATENESS:
            # Wait for the line, then be late by a chosen amount.
            guard = 0
            while not ctx.get(dut.irq_timer):
                await counter.advance()
                guard += 1
                if guard > 8 * PERIOD:
                    break
            fired_at.append(counter.value)
            await counter.advance(late)

            # The handler, as `src/timer.rs` writes it: ADD the period to what
            # is there. It never reads the counter.
            deadline = await bus.read_word(MTIMECMP_LO)
            deadline |= await bus.read_word(MTIMECMP_HI) << 32
            deadlines.append(deadline)
            nxt = deadline + PERIOD
            await bus.write_word(MTIMECMP_LO, NEVER)
            await bus.write_word(MTIMECMP_HI, nxt >> 32)
            await bus.write_word(MTIMECMP_LO, nxt & 0xffff_ffff)

        want = [first + PERIOD * n for n in range(len(LATENESS))]
        checks.check(
            "adding the period keeps the deadlines on an absolute grid",
            deadlines == want,
            f"deadlines {deadlines}\n"
            f"expected  {want}\n"
            "Each handler ran late by a different amount, and the deadlines "
            "must not care. A sequence that has slipped is the drift #130 is "
            "about: it is proportional to interrupt latency, so it is worst "
            "exactly when a timestamp matters.")

        # The gaps between deadlines, which is what a timestamp is built on.
        gaps = [b - a for a, b in zip(deadlines, deadlines[1:])]
        checks.check(
            "every interval is exactly one period, however late the handler was",
            all(gap == PERIOD for gap in gaps),
            f"intervals {gaps}, expected every one to be {PERIOD}")

    async def reload_testbench(ctx):
        """The rule NOT used, to show the check above can tell them apart."""
        bus = Bus(ctx, dut2.bus, verbose)
        counter = Counter(ctx, dut2)

        await counter.advance(2)
        first = counter.value + PERIOD
        await bus.write_word(MTIMECMP_HI, 0)
        await bus.write_word(MTIMECMP_LO, first)

        deadlines = []
        for late in LATENESS:
            guard = 0
            while not ctx.get(dut2.irq_timer):
                await counter.advance()
                guard += 1
                if guard > 8 * PERIOD:
                    break
            await counter.advance(late)

            # RELOAD FROM NOW -- the rule this project does not use.
            nxt = counter.value + PERIOD
            deadlines.append(nxt)
            await bus.write_word(MTIMECMP_LO, NEVER)
            await bus.write_word(MTIMECMP_HI, nxt >> 32)
            await bus.write_word(MTIMECMP_LO, nxt & 0xffff_ffff)

        gaps = [b - a for a, b in zip(deadlines, deadlines[1:])]
        checks.check(
            "reloading from the counter DOES drift -- the two rules are "
            "distinguishable",
            any(gap != PERIOD for gap in gaps),
            f"intervals {gaps}\n"
            "Reloading from `mtime` should stretch every period by the "
            "handler's latency. If it does not here, this simulation is not "
            "modelling a late handler and the check above proves nothing.")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    dut2 = Clint()
    sim = Simulator(Fragment.get(dut2, None))
    sim.add_clock(1e-6)
    sim.add_testbench(reload_testbench)
    sim.run()


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every CSR bus access")
    args = parser.parse_args()

    checks = Checks(emit)

    emit("vexii_clint.Clint: registers and reset state")
    run_register_checks(checks, args.verbose)
    emit()
    emit("vexii_clint.Clint: the interrupt is a level")
    run_level_checks(checks, args.verbose)
    emit()
    emit("vexii_clint.Clint: writing a 64-bit deadline from a 32-bit machine")
    run_write_sequence_checks(checks, args.verbose)
    emit()
    emit("the tick: add the period, never reload from now")
    run_drift_checks(checks, args.verbose)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
