#!/usr/bin/env python3
#
# Simulate the interrupt controller and every source wired to it.
# SPDX-License-Identifier: BSD-3-Clause

"""
Proves `cpu.intc.Interrupts`, the 16550's `irq`, and every source in
`top.IRQ_TRIGGERS` behave before a bitstream is built.

    ./scripts/soc_intc_sim.py          # run every check
    ./scripts/soc_intc_sim.py -v       # and print each bus access

Exit status 0 if every assertion held. Output goes to the terminal and to
`tmp/logs/dev.log`.

## What it asserts

  * **Every wired source fires**, driven the way its own trigger says: a level
    is asserted, a `rise` source is stepped up, a `fall` source is stepped down.
  * **Every wired source masks.** With its enable bit clear the CPU line stays
    down while the pending bit sets, and enabling afterwards raises it -- which
    separates "masked" from "dead".
  * **A clear works while a source is masked.** This is the ordering hazard the
    PLIC had and this controller does not: completing after masking stranded
    the claim, and the source never fired again.
  * **A level source cannot be acknowledged while its line is asserted.** The
    set arm of `amaranth_soc.event.Monitor` wins over the clear arm, so the
    order is always "clear the peripheral, then clear the bit". A handler that
    clears the bit first has done nothing and will be re-entered.
  * **The DPO2036's real shape**, not a single pulse: a train of ~30-42 ms
    assertions separated by the recovery interval. The negative control is the
    50 ms poll it replaces, run over the same waveform, which misses events.
  * **The built table is the documented table.** `docs/soc-interrupts.md` is the
    authority on which sources exist and what trigger each takes; this parses it
    and compares.

## The DPO2036 train runs on a scaled clock

One simulated cycle is 1 ms, so a 30 ms assertion is 30 cycles. Nothing in the
path has a time constant -- the capture is one flop and one edge detector, and
the synchroniser is two more -- so cycle-scaling is exact rather than
approximate. What is being asked is whether an assertion can fall between two
samples, and that is a question about the *shape* of the waveform.

## What this cannot say

Nothing here runs the CPU. It drives the CSR bus directly, so it says the
peripheral is right and says nothing about `mie`, `mstatus`, riscv-rt's trap
entry, or whether the handler is reached.
"""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "gateware"))
sys.path.insert(0, str(ROOT / "gateware" / "soc"))
sys.path.insert(0, str(ROOT / "scripts"))

from sim_check_harness import Checks  # noqa: E402
from devlog import emit  # noqa: E402

from amaranth import Module                  # noqa: E402
from amaranth.hdl import Fragment            # noqa: E402
from amaranth.sim import Simulator           # noqa: E402

from cpu.intc import Interrupts, ENABLE_OFFSET, PENDING_OFFSET  # noqa: E402
from peripherals.i2c_mux import I2CBusMux                       # noqa: E402
from peripherals.uart16550 import Uart16550                     # noqa: E402

import top  # noqa: E402

# How many CSR bytes each mask register occupies. `alignment=2` in
# `cpu/intc.py` rounds it to a CPU word, so this is 4 for any source count up
# to 32 -- and the FOURTH write is the one that commits, which is the whole
# reason this constant is not `ceil(sources / 8)`.
MASK_BYTES = 4

# The DPO2036's own numbers, as cycles of the 1 ms simulated clock.
# `docs/chips/dpo2036-cc-sbu-protection.md`: `FAULTB` is low for ~30-42 ms per
# event, and the sampler it replaces runs at 50 ms.
FAULT_LOW_MS = 34
FAULT_GAP_MS = 18
FAULT_EVENTS = 8
POLL_MS = 50


class Bus:
    """Byte-wide CSR reads and writes, as the multiplexer's timing requires.

    A read is a strobe on one cycle and data on the next -- `csr.Multiplexer`
    registers its read path -- and sampling a cycle late reads ZERO rather than
    the previous access's data, so every check would compare zero against zero.
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
            print(f"      read  {addr:#06x} -> {value:#04x}")
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
            print(f"      write {addr:#06x} <- {value:#04x}")

    async def read_mask(self, base):
        """One mask register, low byte first -- the low byte latches the shadow."""
        value = 0
        for index in range(MASK_BYTES):
            value |= (await self.read(base + index)) << (8 * index)
        return value

    async def write_mask(self, base, value):
        """One mask register. The FOURTH write commits; three write nothing."""
        for index in range(MASK_BYTES):
            await self.write(base + index, (value >> (8 * index)) & 0xff)

    async def pending(self):
        return await self.read_mask(PENDING_OFFSET)

    async def enable(self, mask):
        await self.write_mask(ENABLE_OFFSET, mask)

    async def clear(self, mask):
        """Write 1s to acknowledge. A 0 bit clears nothing, so this is safe to
        aim at one source without reading the register first."""
        await self.write_mask(PENDING_OFFSET, mask)


async def assert_source(ctx, dut, number, trigger, high=2):
    """Drive one source the way its trigger demands, and leave it idle after."""
    lines = ctx.get(dut.lines)
    if trigger == "fall":
        # Idle high, so the assertion is the fall. The 0 -> 1 that gets there is
        # a rise, which a `fall` source ignores.
        ctx.set(dut.lines, lines | (1 << number))
        await ctx.tick().repeat(2)
        ctx.set(dut.lines, ctx.get(dut.lines) & ~(1 << number))
        await ctx.tick().repeat(high)
        ctx.set(dut.lines, ctx.get(dut.lines) | (1 << number))
    else:
        ctx.set(dut.lines, lines | (1 << number))
        await ctx.tick().repeat(high)
        if trigger != "level":
            ctx.set(dut.lines, ctx.get(dut.lines) & ~(1 << number))
    await ctx.tick().repeat(2)


async def idle_lines(ctx, dut, triggers):
    """The value of `lines` with nothing asserting: `fall` sources idle high."""
    idle = 0
    for number, trigger in triggers.items():
        if trigger == "fall":
            idle |= 1 << number
    ctx.set(dut.lines, idle)
    await ctx.tick().repeat(3)
    return idle


def run_controller_checks(checks, verbose):
    """The three trigger modes, on a controller built with one of each."""
    triggers = {1: "level", 2: "rise", 3: "fall"}
    dut = Interrupts(triggers)
    every = (1 << 1) | (1 << 2) | (1 << 3)

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await idle_lines(ctx, dut, triggers)

        checks.check(
            "nothing is enabled out of reset",
            await bus.read_mask(ENABLE_OFFSET) == 0 and ctx.get(dut.irq_out) == 0,
            "the enable register must reset to zero. A controller that "
            "interrupts before firmware has a handler hangs the boot it is "
            "there to serve.")

        # --- three byte writes commit nothing --------------------------------
        for index in range(MASK_BYTES - 1):
            await bus.write(ENABLE_OFFSET + index, 0xff)
        checks.check(
            "an enable write of three bytes does not commit",
            await bus.read_mask(ENABLE_OFFSET) == 0,
            "`alignment=2` makes the FOURTH access the one that writes the "
            "register. A driver that writes only the bytes it has data for has "
            "written nothing at all, and every source stays masked with the "
            "register reading zero -- see `src/gpio.rs` for the same trap.")

        # --- a masked source is pending and silent ---------------------------
        await assert_source(ctx, dut, 1, "level")
        pending = await bus.pending()
        checks.check(
            "a masked source goes pending and does not reach the CPU",
            pending & (1 << 1) and ctx.get(dut.irq_out) == 0,
            f"pending {pending:#x}, irq_out {ctx.get(dut.irq_out)}. Masking "
            f"belongs in the enable and nowhere else: a driver reads pending "
            f"to find out what is going on, including sources it has "
            f"deliberately switched off.")

        await bus.enable(every)
        checks.check(
            "enabling a source that is already pending raises the CPU line",
            ctx.get(dut.irq_out) == 1,
            "the condition did not go away while it was masked, so unmasking "
            "must deliver it. An edge detector on the enable would lose every "
            "interrupt that arrived during setup.")

        # --- a level cannot be acknowledged while it is asserted -------------
        await bus.clear(1 << 1)
        pending = await bus.pending()
        checks.check(
            "a level source ignores a clear while its line is asserted",
            pending & (1 << 1) and ctx.get(dut.irq_out) == 1,
            f"pending {pending:#x} after writing the clear with the line still "
            f"high. The set arm wins over the clear arm, which is what makes "
            f"the order 'drain the peripheral, then clear the bit' the only "
            f"one that works -- and what stops a handler acknowledging a FIFO "
            f"that still holds bytes.")

        ctx.set(dut.lines, ctx.get(dut.lines) & ~(1 << 1))
        await ctx.tick().repeat(2)
        checks.check(
            "a level source stays pending after its line drops",
            (await bus.pending()) & (1 << 1) and ctx.get(dut.irq_out) == 1,
            "the pending bit LATCHES for a level source too. A handler that "
            "drains the peripheral and returns without writing the clear is "
            "re-entered forever, with the peripheral idle and nothing to see.")

        await bus.clear(1 << 1)
        checks.check(
            "a level source clears once its line is down",
            not (await bus.pending()) & (1 << 1) and ctx.get(dut.irq_out) == 0,
            "with the condition gone the acknowledgement must take, or there "
            "is no way to leave the handler at all.")

        # --- edges -----------------------------------------------------------
        await assert_source(ctx, dut, 2, "rise")
        checks.check(
            "a rise source latches a pulse that is over before it is read",
            (await bus.pending()) & (1 << 2) and ctx.get(dut.irq_out) == 1,
            "the line went up and came back down before any read. An edge "
            "source exists to catch exactly that -- the PAC1954's "
            "conversion-complete alert is 5 us and sets no status bit.")

        await bus.clear(1 << 2)
        checks.check(
            "a rise source clears immediately, its line being idle",
            not (await bus.pending()) & (1 << 2) and ctx.get(dut.irq_out) == 0,
            "an edge has no condition left to hold the bit up, so the "
            "acknowledgement is unconditional.")

        await assert_source(ctx, dut, 3, "fall")
        checks.check(
            "a fall source latches its line going down",
            (await bus.pending()) & (1 << 3) and ctx.get(dut.irq_out) == 1,
            "`fall` is for a pin that asserts LOW -- the PAC1954's ALERT and "
            "nothing else on this board. Wiring one as `rise` reports the "
            "release rather than the assertion, which is one event late "
            "forever and looks like a working source.")

        # --- a clear works while the source is masked ------------------------
        #
        # The hazard the PLIC had: `complete` was ignored for a source the
        # context had disabled, so completing after masking stranded the claim
        # and that source never fired again.
        await bus.enable(every & ~(1 << 3))
        await bus.clear(1 << 3)
        checks.check(
            "a masked source can still be acknowledged",
            not (await bus.pending()) & (1 << 3),
            "this is the deferral: the handler masks, a task does the slow "
            "clear, and the task acknowledges and unmasks. If the clear "
            "needed the source to be enabled, the order would matter and "
            "getting it wrong would kill the source for the session -- which "
            "is what the PLIC's claim/complete did.")

        await bus.enable(every)
        checks.check(
            "unmasking an acknowledged source raises nothing",
            ctx.get(dut.irq_out) == 0,
            "the event was acknowledged while masked, so there is nothing "
            "left to deliver. A line that comes up here would make every "
            "deferral fire twice.")

        # --- one source's traffic never touches another ----------------------
        await assert_source(ctx, dut, 2, "rise")
        pending = await bus.pending()
        checks.check(
            "one source going pending leaves the others alone",
            pending == (1 << 2),
            f"pending {pending:#x}, expected only source 2. Sources share one "
            f"register and nothing else.")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def run_wired_source_checks(checks, verbose):
    """Every source `top.py` wires: it fires, and it masks.

    Built from `top.IRQ_TRIGGERS` rather than from a list here, so a source
    added to the SoC without a check is not possible.
    """
    triggers = top.IRQ_TRIGGERS
    dut = Interrupts(triggers)
    every = sum(1 << number for number in triggers)

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await idle_lines(ctx, dut, triggers)
        await bus.enable(0)

        for number, trigger in sorted(triggers.items()):
            name = SOURCE_NAMES.get(number, f"source {number}")

            # --- masked: pending sets, the CPU line does not -----------------
            await assert_source(ctx, dut, number, trigger)
            pending = await bus.pending()
            checks.check(
                f"{name} (#{number}, {trigger}) is captured while masked",
                pending == (1 << number) and ctx.get(dut.irq_out) == 0,
                f"pending {pending:#x}, irq_out {ctx.get(dut.irq_out)}. "
                f"Expected only bit {number} set and the CPU line down.\n"
                f"A source that cannot be shown to fire is not wired, and one "
                f"that reaches the CPU while masked cannot be deferred.")

            # --- unmask: it reaches the CPU ----------------------------------
            await bus.enable(1 << number)
            checks.check(
                f"{name} (#{number}, {trigger}) reaches the CPU once enabled",
                ctx.get(dut.irq_out) == 1,
                "the pending bit was set and the enable was the only thing "
                "holding it back, so this separates a masked source from a "
                "dead one.")

            # --- and back to quiet -------------------------------------------
            if trigger == "level":
                ctx.set(dut.lines, ctx.get(dut.lines) & ~(1 << number))
                await ctx.tick().repeat(2)
            await bus.clear(1 << number)
            await bus.enable(0)
            checks.check(
                f"{name} (#{number}, {trigger}) goes quiet when acknowledged",
                not (await bus.pending()) and ctx.get(dut.irq_out) == 0,
                "an acknowledged source with an idle line must leave nothing "
                "behind, or the next check is reading this one's residue.")

        # --- every source at once, and only the wired bits --------------------
        await bus.enable(every)
        for number, trigger in sorted(triggers.items()):
            await assert_source(ctx, dut, number, trigger, high=1)
        pending = await bus.pending()
        checks.check(
            "every wired source can be pending at once",
            pending == every,
            f"pending {pending:#x}, expected {every:#x}. The gaps in the "
            f"numbering are sources with their lines tied low; a bit set "
            f"outside the table is one of them picking up a neighbour.")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def run_fault_train_checks(checks, verbose):
    """The DPO2036's `FAULTB`, in the shape the part actually produces.

    A train of ~30-42 ms assertions separated by the recovery interval. The
    control is the 50 ms poll this source replaces, run over the same waveform:
    an assertion that begins and ends between two samples is invisible to it and
    is not invisible to a latch.
    """
    triggers = {top.IRQ_TARGET_FAULT: "rise", top.IRQ_AUX_FAULT: "rise"}
    dut = Interrupts(triggers)
    target = top.IRQ_TARGET_FAULT
    seen_by_poll = 0
    events = 0

    async def testbench(ctx):
        nonlocal seen_by_poll, events
        bus = Bus(ctx, dut.bus, verbose)
        await bus.enable(1 << target)
        await bus.clear(1 << target)

        # The waveform, one cycle per millisecond. The phase is deliberately not
        # aligned to the poll: the two periods are 52 ms and 50 ms, which is the
        # aliasing the issue is about.
        elapsed = 0
        for _ in range(FAULT_EVENTS):
            ctx.set(dut.lines, 1 << target)
            for _ in range(FAULT_LOW_MS):
                if elapsed % POLL_MS == 0 and ctx.get(dut.lines) & (1 << target):
                    seen_by_poll += 1
                await ctx.tick()
                elapsed += 1
            ctx.set(dut.lines, 0)

            # The latch caught this one and can be acknowledged now, which is
            # what a handler does. Each event is counted separately only
            # because the acknowledgement is unconditional for an edge.
            for _ in range(FAULT_GAP_MS):
                if elapsed % POLL_MS == 0 and ctx.get(dut.lines) & (1 << target):
                    seen_by_poll += 1
                await ctx.tick()
                elapsed += 1
            if (await bus.pending()) & (1 << target):
                events += 1
                await bus.clear(1 << target)

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-3)
    sim.add_testbench(testbench)
    sim.run()

    checks.check(
        "the fault latch catches every assertion in the train",
        events == FAULT_EVENTS,
        f"caught {events} of {FAULT_EVENTS} assertions of {FAULT_LOW_MS} ms "
        f"separated by {FAULT_GAP_MS} ms. The part auto-recovers, so a "
        f"repeating fault IS a train; catching the first and missing the rest "
        f"reports one event where there were eight.")

    checks.check(
        "the 50 ms poll it replaces misses assertions in the same train",
        seen_by_poll < FAULT_EVENTS,
        f"the poll saw {seen_by_poll} of {FAULT_EVENTS}, which is not a "
        f"failure of this design but the CONTROL for it: if a periodic sampler "
        f"caught them all, this waveform would not be exercising the aliasing "
        f"the source exists to remove, and the check above would prove "
        f"nothing. Widen the gap or change the phase until it misses.")


def run_uart_checks(checks, verbose):
    """The 16550 raises `irq` only when IER says to, and holds it.

    A level, and the reason source 1 and source 2 are levels: the line stays
    high while the FIFO holds a byte, so draining is what clears it and an edge
    would lose everything after the first burst.
    """
    dut = Uart16550()
    RBR_THR, IER, IIR, LCR, LSR = 0, 1, 2, 3, 5

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await bus.write(LCR, 0x03)

        checks.check(
            "irq is low out of reset",
            ctx.get(dut.irq) == 0,
            "IER resets to 0, so nothing can be enabled yet.")

        ctx.set(dut.sink.payload, ord("x"))
        ctx.set(dut.sink.valid, 1)
        await ctx.tick()
        ctx.set(dut.sink.valid, 0)
        await ctx.tick()

        checks.check(
            "a received byte does not raise irq while IER.ERBFI is clear",
            ctx.get(dut.irq) == 0 and (await bus.read(LSR)) & 0x01,
            "LSR.DR must be set and irq must not be. Interrupting on a byte "
            "nobody asked to be told about is how a polled driver ends up in "
            "a trap handler it never installed.")

        await bus.write(IER, 0x01)
        await ctx.tick()
        checks.check(
            "setting IER.ERBFI with a byte waiting raises irq immediately",
            ctx.get(dut.irq) == 1,
            "the condition is a level, not an edge, so enabling it while it "
            "already holds must interrupt. An edge detector here loses the "
            "byte that arrived during setup, and the console appears to "
            "swallow the first keystroke.")

        iir = await bus.read(IIR)
        checks.check(
            "IIR reports a pending receive interrupt",
            iir & 0x01 == 0 and (iir >> 1) & 0x07 == 0b010,
            f"IIR read {iir:#04x}: bit 0 clear means pending, bits 3:1 should "
            f"be 0b010 for 'received data available'")

        # A read of IIR clears the transmit-empty interrupt on a real NS16550A,
        # and IIR is at +2 -- the same 32-bit word as RBR at +0.
        before = ctx.get(dut.irq)
        again = await bus.read(IIR)
        checks.check(
            "reading IIR changes nothing",
            (before, again) == (ctx.get(dut.irq), iir),
            f"irq was {before} and is now {ctx.get(dut.irq)}; IIR read "
            f"{iir:#04x} then {again:#04x}.")

        await bus.read(RBR_THR)
        await ctx.tick()
        checks.check(
            "reading the byte out of RBR drops irq",
            ctx.get(dut.irq) == 0,
            "the receive FIFO is empty, so LSR.DR is clear and there is "
            "nothing to interrupt about.")

        ctx.set(dut.sink.payload, ord("y"))
        ctx.set(dut.sink.valid, 1)
        await ctx.tick()
        ctx.set(dut.sink.valid, 0)
        await ctx.tick()
        checks.check(
            "a second byte raises irq again",
            ctx.get(dut.irq) == 1,
            "the level must re-assert for every byte, not once per session")

        await bus.write(IER, 0x00)
        await ctx.tick()
        checks.check(
            "clearing IER.ERBFI silences a byte that is still waiting",
            ctx.get(dut.irq) == 0 and (await bus.read(LSR)) & 0x01,
            "LSR.DR is still set -- the byte has not been read -- but the "
            "line must drop. This is how the firmware stops a full receive "
            "ring livelocking the CPU: mask, return, let the consumer run.")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def run_type_c_checks(checks, verbose):
    """One source per FUSB302B and one per DPO2036, on the mux that raises them.

    The mux alone is checked in `scripts/soc_board_sim.py`; what is checked here
    is the composition `top.py` builds -- four pins, four sources -- and the
    property the split exists for: a handler services the device that fired and
    touches nothing belonging to the other.
    """
    target, aux = top.IRQ_TYPE_C_TARGET, top.IRQ_TYPE_C_AUX
    target_fault, aux_fault = top.IRQ_TARGET_FAULT, top.IRQ_AUX_FAULT
    triggers = {target: "level", aux: "level",
                target_fault: "rise", aux_fault: "rise"}

    m = Module()
    m.submodules.mux = mux = I2CBusMux()
    m.submodules.intc = intc = Interrupts(triggers)
    m.d.comb += [
        intc.lines[target].eq(mux.target_irq),
        intc.lines[aux].eq(mux.aux_irq),
        intc.lines[target_fault].eq(mux.target_fault_irq),
        intc.lines[aux_fault].eq(mux.aux_fault_irq),
    ]

    async def testbench(ctx):
        bus = Bus(ctx, intc.bus, verbose)

        # Two synchroniser stages inside the mux, so give every pad level four
        # edges to reach the controller.
        async def settle():
            await ctx.tick().repeat(4)

        await bus.enable((1 << target) | (1 << aux)
                         | (1 << target_fault) | (1 << aux_fault))

        # --- one device asserting is that device's bit ------------------------
        ctx.set(mux.aux_int, 1)
        await settle()
        pending = await bus.pending()
        checks.check(
            "the pending bit names the device that asserted",
            pending == (1 << aux),
            f"pending {pending:#x}, expected only bit {aux} with AUX's `int` "
            f"asserting.\n"
            "One OR-ed source would have set the same bit whichever "
            "controller it was, and the handler would have had to read the "
            "mux over the I2C bus to find out which.")

        # --- masking one does not mask the other ------------------------------
        await bus.enable((1 << target) | (1 << target_fault) | (1 << aux_fault))
        await settle()
        checks.check(
            "a deferred device masks only itself",
            ctx.get(intc.irq_out) == 0,
            "AUX is still asserting -- the firmware has not cleared it yet -- "
            "and disabling its source must silence it, or the deferral spins.")

        ctx.set(mux.target_int, 1)
        await settle()
        pending = await bus.pending()
        checks.check(
            "the other device still interrupts while one is deferred",
            pending & (1 << target) and ctx.get(intc.irq_out) == 1,
            f"pending {pending:#x}. TARGET asserted while AUX was masked "
            f"awaiting its I2C clear; on the OR-ed source that event was "
            f"invisible until AUX had been serviced.")

        checks.check(
            "a masked device is still visible in pending",
            pending & (1 << aux),
            f"pending {pending:#x}; AUX is asserting and disabled. The `irq` "
            f"command reads this to show which source is mid-deferral.")

        # --- a fault is an edge on the same peripheral ------------------------
        #
        # The `int` lines are levels and the `fault` lines are edges, on one
        # device, at the same time. Nothing about one may depend on the other.
        ctx.set(mux.target_fault, 1)
        await settle()
        ctx.set(mux.target_fault, 0)
        await settle()
        pending = await bus.pending()
        checks.check(
            "a fault latches while that device's `int` is asserting",
            pending & (1 << target_fault) and pending & (1 << target),
            f"pending {pending:#x}: both of TARGET's bits should be set. The "
            f"level and the edge on one device are two sources and neither "
            f"gates the other.")

        ctx.set(mux.target_int, 0)
        ctx.set(mux.aux_int, 0)
        await settle()
        await bus.enable((1 << target) | (1 << aux)
                         | (1 << target_fault) | (1 << aux_fault))
        await bus.clear((1 << target) | (1 << aux) | (1 << target_fault))
        await settle()
        checks.check(
            "re-enabling a cleared device raises nothing",
            ctx.get(intc.irq_out) == 0 and (await bus.pending()) == 0,
            "the devices were cleared before their sources came back, so "
            "there is nothing left to deliver. A re-enable that immediately "
            "re-fires is the storm, and here it can only mean the clear did "
            "not take.")

        ctx.set(mux.aux_int, 1)
        await settle()
        checks.check(
            "a device still asserting when re-enabled fires again",
            (await bus.pending()) & (1 << aux),
            "a level that is still high must re-interrupt -- there is still "
            "work -- and the handler masks again on the way in, so this is a "
            "loop with the CPU making progress rather than a storm.")

    sim = Simulator(Fragment.get(m, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


# What each source number is called, for the check lines. From the constants in
# `top.py`, so a renamed source renames its check.
SOURCE_NAMES = {number: name[4:].lower()
                for name, number in vars(top).items()
                if name.startswith("IRQ_") and isinstance(number, int)}

# `| 6 | **PAC1954** `U1` | `GPIO/ALERT2`, pin 15 | **edge** (...) | wired... |`
DOC_ROW = re.compile(r"^\|\s*(\d+)\s*\|(.+)$")


def read_doc_table():
    """The source table out of `docs/soc-interrupts.md`.

    Returns `{number: (trigger, built)}`, where trigger is "level" or the
    controller's own "rise"/"fall" when the doc names a direction. The doc is
    the authority on which sources exist and what each takes, so it is parsed
    rather than transcribed.
    """
    text = (ROOT / "docs" / "soc-interrupts.md").read_text()
    table = {}
    for line in text.splitlines():
        match = DOC_ROW.match(line.strip())
        if not match:
            continue
        cells = [cell.strip() for cell in match.group(2).split("|")]
        if len(cells) < 4:
            continue
        trigger, built = cells[2], cells[3]
        words = trigger.replace("*", "").split("(")[0].replace(",", " ").split()
        words = [word.lower() for word in words]
        if not words or words[0] not in ("level", "edge"):
            continue
        kind = words[0]
        # "edge, rise" and "edge, fall on `locked`" name the direction; a bare
        # "edge" is a source with no hardware yet and does not.
        for direction in ("rise", "fall"):
            if direction in words:
                kind = direction
        table[int(match.group(1))] = (
            kind, built.replace("*", "").lower().startswith("yes"))
    return table


def run_doc_checks(checks):
    """The built table against the documented one."""
    doc = read_doc_table()
    built = top.IRQ_TRIGGERS

    checks.check(
        "the design doc's table parsed",
        len(doc) >= 17,
        f"read {len(doc)} rows out of docs/soc-interrupts.md, expected at "
        f"least 17. The table is the authority and this check is worth "
        f"nothing if it silently reads none of it.")

    wrong = {number: (trigger, doc.get(number, ("-", False))[0])
             for number, trigger in built.items()
             if doc.get(number, ("-", False))[0] != trigger}
    checks.check(
        "every built source has the trigger and direction the doc gives it",
        not wrong,
        "\n        ".join(f"source {number}: built {b}, doc says {d}"
                          for number, (b, d) in sorted(wrong.items()))
        or "-")

    claimed = {number for number, (_, is_built) in doc.items() if is_built}
    checks.check(
        "the doc's 'built today' column names exactly what is built",
        claimed == set(built),
        f"doc says built: {sorted(claimed)}\n        gateware wires: "
        f"{sorted(built)}\n"
        "A design doc that claims a source is built when it is not is worse "
        "than one that says nothing: the next reader wires firmware to it.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every CSR bus access")
    args = parser.parse_args()

    checks = Checks(emit)

    emit("cpu.intc.Interrupts -- level, rise and fall")
    run_controller_checks(checks, args.verbose)
    emit()
    emit("every source top.py wires -- it fires, and it masks")
    run_wired_source_checks(checks, args.verbose)
    emit()
    emit("DPO2036 FAULTB -- the assertion train, against the poll it replaces")
    run_fault_train_checks(checks, args.verbose)
    emit()
    emit("uart16550.Uart16550.irq -- why sources 1 and 2 are levels")
    run_uart_checks(checks, args.verbose)
    emit()
    emit("i2c_mux.I2CBusMux -> Interrupts -- a source per pin")
    run_type_c_checks(checks, args.verbose)
    emit()
    emit("docs/soc-interrupts.md -- the table this is built from")
    run_doc_checks(checks)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
