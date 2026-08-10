#!/usr/bin/env python3
#
# Simulate the PLIC and the 16550's interrupt output, and assert their semantics.
# SPDX-License-Identifier: BSD-3-Clause

"""
Proves `vexii_plic.Plic`, `Uart16550.irq` and the Type-C sources behave before a
bitstream is built.

    ./scripts/soc_plic_sim.py          # run every check
    ./scripts/soc_plic_sim.py -v       # and print each bus access

Exit status 0 if every assertion held. Output goes to the terminal and to
`tmp/logs/dev.log`.

## Why this is worth a file

A ~60 s synthesis, a reconfigure and a USB enumeration is the cost of asking this
design one question, and an interrupt controller is mostly questions of the form
"what happens on the cycle after". Two of the checks below -- that a claim
returns the lower source number on a priority tie, and that `complete` is ignored
for a source that is not enabled -- are single-cycle behaviours that no amount of
typing at a console can distinguish from working.

The third section is the one thing here that is about a design choice rather than
a specification: each FUSB302B has its own source, and the checks assert that a
handler can service the device it claimed without reading, scanning or clearing
anything belonging to the other. `docs/architecture.md` decision 8 is the argument;
these are the assertions that it is built that way.

The fourth section is not a simulation of gateware at all: it reads the priority
levels the FIRMWARE configures and asserts they match this file's ranking table.
Priorities were stored, read back and decided nothing for the life of the
project -- every source claimed at 1, so the order was the source-number
tie-break, i.e. wiring order. A table that lives only in a comment goes flat
again the first time someone adds a source; this is what makes that a failure.
See #344.

The one that actually matters most is `pending_read_has_no_side_effect`. This
SoC has lost two multi-day stretches to registers whose reads changed state, so
"reading it twice gives the same answer, and nothing else moved" is asserted here
rather than assumed. It is cheap to assert and it is the property the whole
register layout was chosen for.

## What this cannot say

Nothing here runs the CPU. It drives the CSR bus directly, so it says the
peripheral is right and says nothing about `mie`, `mstatus`, riscv-rt's trap
entry, or whether the handler is reached. `scripts/soc_test.py` covers that under
QEMU, against QEMU's own PLIC, which is the other half of the argument.
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

from peripherals.i2c_mux import I2CBusMux                # noqa: E402
from peripherals.uart16550 import Uart16550              # noqa: E402
from cpu.plic import Plic, CONTEXT_BASE, ENABLE_BASE, PENDING_BASE  # noqa: E402

# Source numbers, matching what gateware/soc/top.py wires up.
CONSOLE = 1
APOLLO = 2
TYPE_C_TARGET = 4
TYPE_C_AUX = 5

CLAIM = CONTEXT_BASE + 0x4
THRESHOLD = CONTEXT_BASE + 0x0


def priority_addr(source):
    return 4 * source


class Bus:
    """Byte-wide CSR reads and writes, as the multiplexer's timing requires.

    A read is a strobe on one cycle and data on the next -- `csr.Multiplexer`
    registers its read path so the bus does not carry a combinational path into
    every register. Sampling a cycle late reads ZERO, not the previous access's
    data, because the multiplexer gates its output on a registered `r_en` that
    is high for exactly one cycle. Every check then "passes" by comparing zero
    against zero, which is how the first run of this file reported nine
    failures and four accidental passes.
    """

    def __init__(self, ctx, bus, verbose=False):
        self.ctx = ctx
        self.bus = bus
        self.verbose = verbose

    async def read(self, addr):
        ctx = self.ctx
        ctx.set(self.bus.addr, addr)
        ctx.set(self.bus.r_stb, 1)
        # This edge is the one that captures the register into the read shadow
        # and raises the multiplexer's internal `r_en`, so `r_data` is valid on
        # the cycle we land in -- and only on that cycle.
        await ctx.tick()
        ctx.set(self.bus.r_stb, 0)
        value = ctx.get(self.bus.r_data)
        # Back to a quiescent cycle, so a caller's `ctx.get` of some other
        # signal afterwards sees the state the read left behind rather than the
        # state during it. The claim's side effect lands on this edge.
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
        # The multiplexer registers the write strobe on its way to the register,
        # so the effect is visible one cycle later.
        await ctx.tick()
        if self.verbose:
            print(f"      write {addr:#08x} <- {value:#04x}")


def run_plic_checks(checks, verbose):
    """Drive a two-source PLIC over its CSR bus and assert the spec's rules."""
    dut = Plic(sources=2)

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)

        # --- a source that nothing has enabled raises nothing ----------------
        ctx.set(dut.sources, 1 << CONSOLE)
        await ctx.tick()
        checks.check(
            "an un-enabled source does not reach the CPU",
            ctx.get(dut.irq_out) == 0,
            "irq_out went high with enable and priority both at their reset "
            "value of zero. A PLIC that interrupts before software has "
            "configured it hangs the boot it is supposed to serve.")

        # `pending` still reports it. The spec distinguishes "requesting" from
        # "this context will be interrupted", and so must software: a driver
        # reads pending to find out what is going on, including sources it has
        # deliberately masked.
        checks.check(
            "pending reports a request even while masked",
            await bus.read(PENDING_BASE) & (1 << CONSOLE),
            "pending read back 0 for a source that is asserting. Masking "
            "belongs in the enable and the threshold, not in the gateway.")

        # --- enabling without a priority is still silent ----------------------
        await bus.write(ENABLE_BASE, (1 << CONSOLE) | (1 << APOLLO))
        checks.check(
            "an enabled source at priority 0 stays masked",
            ctx.get(dut.irq_out) == 0,
            "priority 0 must never interrupt: with the threshold at its reset "
            "value of 0, the rule is `priority > threshold` and 0 > 0 is "
            "false. A driver that enables a source and forgets its priority "
            "should get silence, not a storm it cannot turn off.")

        # --- priority above the threshold does interrupt ----------------------
        await bus.write(priority_addr(CONSOLE), 1)
        await ctx.tick()
        checks.check(
            "an enabled source above the threshold raises irq_out",
            ctx.get(dut.irq_out) == 1,
            "the CPU's interrupt line stayed low for a source that is "
            "pending, enabled, and above the threshold.")

        # --- the threshold masks it again -------------------------------------
        await bus.write(THRESHOLD, 1)
        await ctx.tick()
        checks.check(
            "raising the threshold to the source's own priority masks it",
            ctx.get(dut.irq_out) == 0,
            "the rule is strictly greater-than: priority 1 must not pass a "
            "threshold of 1. An off-by-one here silently changes which "
            "sources a masked window blocks -- and the shell reports this "
            "register in `info`, so a wrong one is also a wrong diagnosis.")
        await bus.write(THRESHOLD, 0)

        # --- reading pending changes nothing ----------------------------------
        #
        # THE ASSERTION THIS FILE EXISTS FOR. See the module docstring.
        first = await bus.read(PENDING_BASE)
        second = await bus.read(PENDING_BASE)
        third = await bus.read(PENDING_BASE)
        checks.check(
            "pending_read_has_no_side_effect",
            first == second == third and ctx.get(dut.irq_out) == 1,
            f"three consecutive reads of pending gave {first:#x}, "
            f"{second:#x}, {third:#x}, and irq_out is now "
            f"{ctx.get(dut.irq_out)}.\n"
            "A status register that answers differently on the second read "
            "cannot be polled, and this SoC has lost days to exactly that.")

        # --- claim returns the source and gates it ----------------------------
        claimed = await bus.read(CLAIM)
        checks.check(
            "claim returns the pending source number",
            claimed == CONSOLE,
            f"claim returned {claimed}, expected {CONSOLE}")

        await ctx.tick()
        checks.check(
            "claim drops irq_out while the source is still asserting",
            ctx.get(dut.irq_out) == 0,
            "the source's level is still high -- it is a 16550 with a byte in "
            "its FIFO -- so without the claim gate the handler would be "
            "re-entered before its first instruction and the CPU would make "
            "no progress at all. This is the livelock the claim exists to "
            "prevent.")

        checks.check(
            "pending reads 0 for a claimed source",
            not (await bus.read(PENDING_BASE)) & (1 << CONSOLE),
            "the spec clears the pending bit on claim; a driver that reads "
            "pending to decide what still needs work would otherwise loop.")

        # --- complete for a source that is not enabled is ignored -------------
        await bus.write(ENABLE_BASE, 1 << APOLLO)
        await bus.write(CLAIM, CONSOLE)
        await bus.write(ENABLE_BASE, (1 << CONSOLE) | (1 << APOLLO))
        await ctx.tick()
        checks.check(
            "complete is ignored for a source this context has disabled",
            ctx.get(dut.irq_out) == 0,
            "the spec says an unenabled completion is ignored. Honouring it "
            "would let a driver that masks a source between claim and "
            "complete release a claim it no longer owns.")

        # --- complete releases it ---------------------------------------------
        await bus.write(CLAIM, CONSOLE)
        await ctx.tick()
        checks.check(
            "complete re-raises a source whose level is still high",
            ctx.get(dut.irq_out) == 1,
            "after complete, a level that is still asserted must interrupt "
            "again -- there is still a byte in the FIFO. A design that waits "
            "for an edge here loses everything after the first burst.")

        # --- the source going away clears it ----------------------------------
        ctx.set(dut.sources, 0)
        await ctx.tick()
        checks.check(
            "irq_out follows the source down",
            ctx.get(dut.irq_out) == 0,
            "the level went away and the line stayed high, so this is "
            "latching an edge somewhere it should not.")

        checks.check(
            "claim with nothing pending returns source 0",
            await bus.read(CLAIM) == 0,
            "0 is the spec's 'nothing to claim'. Returning a real source "
            "number here would send a handler to service a peripheral that "
            "did not ask.")

        # --- ties go to the lower source number -------------------------------
        await bus.write(priority_addr(APOLLO), 1)
        ctx.set(dut.sources, (1 << CONSOLE) | (1 << APOLLO))
        await ctx.tick()
        first_claim = await bus.read(CLAIM)
        second_claim = await bus.read(CLAIM)
        checks.check(
            "equal priorities are claimed lowest source number first",
            (first_claim, second_claim) == (CONSOLE, APOLLO),
            f"claimed {first_claim} then {second_claim}, expected "
            f"{CONSOLE} then {APOLLO}.\n"
            "The spec breaks ties by lowest id. Breaking them the other way "
            "starves source 1 whenever source 2 is equally busy, which here "
            "is the USB console losing to the Apollo port permanently.")

        # --- a higher priority wins regardless of number ----------------------
        await bus.write(CLAIM, CONSOLE)
        await bus.write(CLAIM, APOLLO)
        await bus.write(priority_addr(APOLLO), 7)
        await ctx.tick()
        winner = await bus.read(CLAIM)
        checks.check(
            "a higher priority wins over a lower source number",
            winner == APOLLO,
            f"claimed {winner}, expected {APOLLO} at priority 7 against "
            f"{CONSOLE} at priority 1")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def run_uart_checks(checks, verbose):
    """Assert the 16550 raises `irq` only when IER says to, and holds it."""
    dut = Uart16550()

    # Register offsets, from the standard map.
    RBR_THR, IER, IIR, LCR, LSR = 0, 1, 2, 3, 5

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)

        # 8N1 with DLAB clear, so RBR is the data register rather than a
        # divisor latch. Without this the sink pushes below still arrive, but a
        # read of RBR would return the divisor and pop nothing.
        await bus.write(LCR, 0x03)

        checks.check(
            "irq is low out of reset",
            ctx.get(dut.irq) == 0,
            "IER resets to 0, so nothing can be enabled yet. A line that is "
            "high here interrupts the CPU before firmware has a handler.")

        # A byte arrives with interrupts still disabled.
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

        # Enable receive interrupts.
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

        # THE ONE THAT MATTERS. On a real NS16550A a read of IIR clears the
        # transmit-empty interrupt, and IIR is at +2 -- the same 32-bit word as
        # RBR at +0. See the comment on the IIR block in uart16550.py.
        before = ctx.get(dut.irq)
        again = await bus.read(IIR)
        checks.check(
            "reading IIR changes nothing",
            (before, again) == (ctx.get(dut.irq), iir),
            f"irq was {before} and is now {ctx.get(dut.irq)}; IIR read "
            f"{iir:#04x} then {again:#04x}.\n"
            "A state-changing read at +2 shares a 32-bit word with RBR at +0. "
            "That arrangement has cost this project a day already, in the "
            "other direction.")

        # Draining the FIFO clears the condition, which is how a handler makes
        # the interrupt go away.
        await bus.read(RBR_THR)
        await ctx.tick()
        checks.check(
            "reading the byte out of RBR drops irq",
            ctx.get(dut.irq) == 0,
            "the receive FIFO is empty, so LSR.DR is clear and there is "
            "nothing to interrupt about. A line still high here is a handler "
            "that can never return.")

        checks.check(
            "IIR reports no interrupt pending once irq is low",
            (await bus.read(IIR)) & 0x01 == 1,
            "bit 0 set means 'none pending', and its sense is inverted from "
            "every other status bit in the part.")

        # Masking IER while a byte waits must silence the line, because that is
        # the flow control the firmware's ring buffer uses when it fills.
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
            "line must drop. This is the only way the firmware can stop a "
            "full receive ring from livelocking the CPU: mask the source, "
            "return, and let the consumer run.")

        # The transmit side, which the firmware deliberately does not enable.
        # Asserted anyway, because the gateware offers it and a future driver
        # will find it here.
        await bus.write(IER, 0x02)
        await ctx.tick()
        checks.check(
            "IER.ETBEI raises irq while the transmit FIFO has room",
            ctx.get(dut.irq) == 1,
            "THRE is set whenever the transmit FIFO is not full, which after "
            "reset is always.")

        iir = await bus.read(IIR)
        checks.check(
            "IIR reports the transmit-empty id when only ETBEI is set",
            iir & 0x01 == 0 and (iir >> 1) & 0x07 == 0b001,
            f"IIR read {iir:#04x}, expected id 0b001")

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


def run_type_c_checks(checks, verbose):
    """One PLIC source per FUSB302B, driven from the mux that raises them.

    The mux alone is checked in `scripts/soc_board_sim.py`; what is checked here
    is the composition `top.py` builds -- each `int` line on its own
    source -- and the property the split exists for:

    **A handler learns which device asserted from the claim, and touches nothing
    belonging to the other one.** On the OR-ed source that was impossible: the
    claim said "Type-C", the mux's `LINES` register had to be read to say which,
    and every asserting device had to be cleared before the source could come
    back or the level re-fired forever.
    """
    m = Module()
    m.submodules.mux = mux = I2CBusMux()
    m.submodules.plic = plic = Plic(sources=TYPE_C_AUX)
    m.d.comb += [
        plic.sources[TYPE_C_TARGET].eq(mux.target_irq),
        plic.sources[TYPE_C_AUX].eq(mux.aux_irq),
    ]

    async def testbench(ctx):
        bus = Bus(ctx, plic.bus, verbose)

        # FFSynchronizer is two stages inside the mux, so give every pad level
        # four edges to reach the PLIC. Sampling earlier would be testing the
        # synchroniser rather than the dispatch.
        async def settle():
            await ctx.tick().repeat(4)

        await bus.write(ENABLE_BASE,
                        (1 << TYPE_C_TARGET) | (1 << TYPE_C_AUX))
        await bus.write(priority_addr(TYPE_C_TARGET), 1)
        await bus.write(priority_addr(TYPE_C_AUX), 1)

        # --- one device asserting is claimed as that device -------------------
        ctx.set(mux.aux_int, 1)
        await settle()
        claimed = await bus.read(CLAIM)
        checks.check(
            "the claim names the device that asserted",
            claimed == TYPE_C_AUX,
            f"claim returned {claimed}, expected {TYPE_C_AUX} with only AUX's "
            f"`int` asserting.\n"
            "This is what the split buys. One OR-ed source would have returned "
            "the same number whichever controller it was, and the handler "
            "would have had to read the mux over I2C's neighbour to find out.")

        # --- and the OTHER device's source was never involved -----------------
        #
        # The assertion the issue is about: servicing AUX requires nothing to be
        # read, scanned or cleared on TARGET.
        pending = await bus.read(PENDING_BASE)
        checks.check(
            "one device asserting leaves the other's source untouched",
            not pending & (1 << TYPE_C_TARGET),
            f"pending was {pending:#x} with only AUX asserting; TARGET's bit "
            f"is set.\n"
            "A handler is entitled to service exactly the source it claimed. "
            "If the quiet device's source can be pending anyway, then every "
            "handler has to scan both again and the shared-line obligation is "
            "back under a different name.")

        # --- masking one does not mask the other ------------------------------
        #
        # The firmware defers by DISABLING the source it claimed, because
        # clearing a FUSB302B is ~1 ms of I2C on the controller the foreground
        # is also using. With one source that masked both devices for the whole
        # window; with two it masks one.
        await bus.write(CLAIM, TYPE_C_AUX)
        await bus.write(ENABLE_BASE, 1 << TYPE_C_TARGET)
        await settle()
        checks.check(
            "a deferred device masks only itself",
            ctx.get(plic.irq_out) == 0,
            "AUX is still asserting -- the firmware has not cleared it yet -- "
            "and disabling its source must silence it, or the deferral spins.")

        ctx.set(mux.target_int, 1)
        await settle()
        claimed = await bus.read(CLAIM)
        checks.check(
            "the other device still interrupts while one is deferred",
            claimed == TYPE_C_TARGET,
            f"claim returned {claimed}, expected {TYPE_C_TARGET}.\n"
            "TARGET asserted while AUX was masked awaiting its I2C clear. On "
            "the OR-ed source this event was invisible until AUX had been "
            "serviced; that is the concrete cost the split removes.")

        # --- pending still reports the masked device --------------------------
        pending = await bus.read(PENDING_BASE)
        checks.check(
            "a masked device is still visible in pending",
            pending & (1 << TYPE_C_AUX),
            f"pending was {pending:#x}; AUX is asserting and disabled, and the "
            f"spec distinguishes 'requesting' from 'this context will be "
            f"interrupted'. The `irq` command reads this to show which source "
            f"is mid-deferral.")

        # --- re-enabling after the devices were cleared -----------------------
        #
        # `fusb302::clear` reads the three read-to-clear registers, the `int`
        # line drops, and `irq::resume_type_c` re-enables just that source.
        ctx.set(mux.target_int, 0)
        ctx.set(mux.aux_int, 0)
        await settle()
        await bus.write(CLAIM, TYPE_C_TARGET)
        await bus.write(ENABLE_BASE, (1 << TYPE_C_TARGET) | (1 << TYPE_C_AUX))
        await settle()
        checks.check(
            "re-enabling a cleared device raises nothing",
            ctx.get(plic.irq_out) == 0 and (await bus.read(CLAIM)) == 0,
            "the device was cleared before its source came back, so there is "
            "nothing to claim. A re-enable that immediately re-fires is the "
            "storm, and here it can only mean the clear did not take.")

        # --- a device that was NOT cleared re-fires, which is correct ---------
        ctx.set(mux.aux_int, 1)
        await settle()
        claimed = await bus.read(CLAIM)
        checks.check(
            "a device still asserting when re-enabled fires again",
            claimed == TYPE_C_AUX,
            f"claim returned {claimed}, expected {TYPE_C_AUX}. A level that is "
            f"still high must re-interrupt -- there is still work -- and the "
            f"handler masks again on the way in, so this is a loop with the "
            f"CPU making progress between passes rather than a storm.")
        await bus.write(CLAIM, TYPE_C_AUX)

        # --- fault is on neither source ---------------------------------------
        ctx.set(mux.aux_int, 0)
        ctx.set(mux.target_int, 0)
        ctx.set(mux.target_fault, 1)
        ctx.set(mux.aux_fault, 1)
        await settle()
        checks.check(
            "fault reaches no source at all",
            ctx.get(plic.irq_out) == 0 and (await bus.read(PENDING_BASE)) == 0,
            "`fault` is polled, not wired: nothing in the firmware can clear "
            "it -- it drops when the device's fault does -- so an interrupt on "
            "it would have to stay masked until a poll saw the level go, which "
            "means adding a handler and keeping the poll.")

    sim = Simulator(Fragment.get(m, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()


# ---------------------------------------------------------------------------
# The ranking, and the firmware that has to match it. #344.
# ---------------------------------------------------------------------------

# THE TABLE. Levels 1..7 (PRIORITY_BITS = 3); 0 means "never interrupt".
#
# Ranked by the cost of being late, not by how important the peripheral is. A
# source with a bounded hardware FIFO and a short drain window outranks one where
# lateness is merely annoying.
#
# Named by the constant in `firmware/cynthion-soc/src/plic.rs`'s `priority`
# module, which is where each level's reasoning lives. This file holds only the
# numbers, so the two cannot drift without one of them failing.
RANKING = {
    "POWER_ALERT": 4,
    "CONSOLE":     3,
    "TYPE_C":      2,
    "I2C":         1,
}

# Levels no source may use yet, and what each is being kept for.
#
# The consoles sit at 3 rather than at the top precisely so these stay free: a
# ranking with no headroom has to renumber everything to admit the data path.
# A live source that reaches into this range fails the check below.
RESERVED = {
    7: "USB capture / ULPI RX CMD -- #125, the shortest drain window on the board",
    6: "HyperRAM timed_out / overflow -- #324, the capture sink",
    5: "unallocated, between the capture path and a hardware fault",
}

# Which level each wired source takes. Keys are the `*_IRQ` constants in the
# generated `firmware/cynthion-soc-pac/src/base.rs`, so a source that moves in
# `gateware/soc/top.py` and is regenerated lands here automatically -- and a
# source ADDED there with nothing claiming it fails `every wired source has a
# stated priority` below.
SOURCE_LEVEL = {
    "CONSOLE_IRQ":                   "CONSOLE",
    "APOLLO_UART_IRQ":               "CONSOLE",
    "BOARD_I2C_IRQ":                 "I2C",
    "BOARD_I2C_MUX_TARGET_IRQ":      "TYPE_C",
    "BOARD_I2C_MUX_AUX_IRQ":         "TYPE_C",
    "BOARD_I2C_MUX_POWER_ALERT_IRQ": "POWER_ALERT",
}

# Source expressions that name no wired source of their own.
#
# `workload::source::SOURCE` is #115's measurement harness, which HIJACKS an
# existing source (TARGET on the board, the goldfish RTC under QEMU) rather than
# adding one. It still has to state a level from the table -- that is what stops
# it silently demoting the source it borrowed -- but it contributes no entry to
# the source-to-level map.
BORROWED_SOURCES = {"SOURCE"}

# `Plic::claim_source` forwards its own parameter to `set_priority`. That is the
# one place a level may be a variable, because it is the funnel every other site
# goes through.
FORWARDED_LEVELS = {"priority"}

FIRMWARE = ROOT / "firmware" / "cynthion-soc" / "src"
PAC_BASE = ROOT / "firmware" / "cynthion-soc-pac" / "src" / "base.rs"

# `src/bin/` is out of scope: those are #115's dispatcher models, each a
# standalone binary that arms one or two sources to measure a latency. They do
# not run the instrument, and ranking them against the instrument's table would
# change what they measure.
SKIP_DIRS = {"bin"}

# `claim_source(source, level)` and `set_priority(source, level)`, with an optional
# path in front and an optional trailing comma. Two arguments and no nested
# parens, which is every call site in this firmware; anything else is reported
# as unparseable rather than skipped, because a call this cannot read is a
# priority this cannot check.
CALL_RE = re.compile(
    r"(?:\w+::)*(?P<fn>claim_source|claim|set_priority)\("
    r"\s*(?P<source>[^,()]+?)\s*,\s*(?P<level>[^,()]+?)\s*,?\s*\)", re.S)

LOOP_RE = re.compile(r"for\s+&(\w+)\s+in\s+target::(\w+)")
IRQ_CONST_RE = re.compile(r"^pub const (\w+_IRQ): u32 = (\d+);", re.M)
SLICE_RE = re.compile(r"pub const (\w+): &\[u32\] = &\[(.*?)\];", re.S)
PRIORITY_MOD_RE = re.compile(r"pub mod priority \{(.*?)\n\}", re.S)
PRIORITY_CONST_RE = re.compile(r"pub const (\w+): u32 = (\d+);")


def read_pac_sources():
    """`{name: source number}` for every PLIC source the generated PAC declares."""
    return {name: int(value)
            for name, value in IRQ_CONST_RE.findall(PAC_BASE.read_text())}


def read_target_slices():
    """`{slice name: [IRQ constant name, ...]}` from `src/target.rs`.

    Each slice is declared twice, once per `#[cfg]`. The board's is the one whose
    body names the generated PAC; QEMU's is a bare literal and is not what the
    bitstream wires.
    """
    text = (FIRMWARE / "target.rs").read_text()
    slices = {}
    for name, body in SLICE_RE.findall(text):
        names = re.findall(r"base::(\w+)", body)
        if names:
            slices[name] = names
    return slices


def read_priority_module():
    """`{constant name: level}` from `plic.rs`'s `priority` module, or `{}`."""
    match = PRIORITY_MOD_RE.search((FIRMWARE / "plic.rs").read_text())
    if not match:
        return {}
    return {name: int(value)
            for name, value in PRIORITY_CONST_RE.findall(match.group(1))}


def read_claim_sites(pac_sources, slices):
    """Every priority the firmware states, and how it states it.

    Returns `(levels, literals, unresolved)`:

      levels      {source number: level constant name, or an int for a literal}
      literals    ["file:line  expression"] -- a level written as a bare number
      unresolved  ["file:line  expression"] -- a call this parser cannot read

    A literal is recorded in BOTH: it is a violation, and it is also what the
    hardware would actually be programmed with, which is what the arbitration
    check below needs in order to show today's ordering rather than no ordering.
    """
    priorities = read_priority_module()
    levels, literals, unresolved = {}, [], []

    for path in sorted(FIRMWARE.rglob("*.rs")):
        if SKIP_DIRS & set(path.relative_to(FIRMWARE).parts):
            continue
        text = path.read_text()
        loops = [(m.start(), m.group(1), m.group(2))
                 for m in LOOP_RE.finditer(text)]
        for match in CALL_RE.finditer(text):
            # The definition of `claim` itself, whose parameter list has the
            # same shape as a call to it.
            if text[max(0, match.start() - 3):match.start()] == "fn ":
                continue
            where = f"{path.relative_to(ROOT)}:{text.count(chr(10), 0, match.start()) + 1}"
            source_expr = match.group("source")
            level_expr = match.group("level")

            if level_expr in FORWARDED_LEVELS:
                continue
            if level_expr.isdigit():
                level = int(level_expr)
                literals.append(f"{where}  {match.group('fn')}(.., {level_expr})")
            else:
                tail = level_expr.rsplit("::", 1)
                if len(tail) != 2 or not tail[0].endswith("priority"):
                    unresolved.append(f"{where}  level {level_expr!r}")
                    continue
                level = tail[1]
                if level not in priorities:
                    unresolved.append(f"{where}  no priority::{level} in plic.rs")
                    continue

            # Which sources this call configures.
            bare = source_expr.rsplit("::", 1)[-1]
            if bare in BORROWED_SOURCES:
                continue
            if bare in pac_sources:
                numbers = [pac_sources[bare]]
            elif bare.isdigit():
                numbers = [int(bare)]
            else:
                # A loop variable: `for &source in target::UART_IRQS`.
                loop = [(pos, var, slc) for pos, var, slc in loops
                        if pos < match.start() and var == bare]
                if not loop or loop[-1][2] not in slices:
                    unresolved.append(f"{where}  source {source_expr!r}")
                    continue
                numbers = [pac_sources[n] for n in slices[loop[-1][2]]
                           if n in pac_sources]
            for number in numbers:
                levels[number] = level

    return levels, literals, unresolved


def level_value(level, priorities):
    """A level as a number, whether it was written as a constant or a literal."""
    return level if isinstance(level, int) else priorities.get(level)


def run_ranking_checks(checks, verbose):
    """The firmware's priorities against RANKING, and the order they produce.

    Not a gateware simulation until the last check. The arbiter has honoured
    priority since the `>=` fix above; what had never been checked is that
    anything ever wrote a priority other than 1 -- so the ordering on this board
    was the source-number tie-break, and the two consoles outranked the data
    path by accident of wiring order.
    """
    pac_sources = read_pac_sources()
    slices = read_target_slices()
    priorities = read_priority_module()
    levels, literals, unresolved = read_claim_sites(pac_sources, slices)

    checks.check(
        "every claim site states its level as a named constant",
        not literals and not unresolved,
        "a bare number at a claim site is a ranking that no table can enforce, "
        "and it is how every source came to be claimed at 1:\n        "
        + "\n        ".join(literals + unresolved))

    checks.check(
        "plic.rs's priority module matches the table",
        priorities == RANKING,
        f"firmware has {priorities}, this file's table is {RANKING}.\n"
        "The levels live in one place and the reasoning beside them; this is "
        "the assertion that the two files are still describing one ranking.")

    missing = sorted(name for name in pac_sources
                     if pac_sources[name] not in levels)
    checks.check(
        "every wired source has a stated priority",
        not missing,
        f"{', '.join(missing) or '-'} appear in the generated PAC -- so the "
        f"gateware wires them -- and nothing claims them with a level. A source "
        f"added without a rank inherits its number as its rank.")

    wanted = {pac_sources[name]: RANKING[level]
              for name, level in SOURCE_LEVEL.items() if name in pac_sources}
    actual = {number: level_value(level, priorities)
              for number, level in levels.items() if number in wanted}
    checks.check(
        "each source is ranked where the table puts it",
        actual == wanted,
        f"source-to-level is {actual}, expected {wanted}")

    checks.check(
        "the ranking is not flat",
        len(set(actual.values())) > 1,
        f"every source is at the same level ({sorted(set(actual.values()))}), "
        "so the PLIC's arbitration is entirely the source-number tie-break and "
        "the priority registers decide nothing. That was the state this check "
        "was written against.")

    used = {level for level in actual.values() if level is not None}
    intruding = sorted(used & set(RESERVED))
    checks.check(
        "levels 5-7 are still free for the capture path",
        not intruding,
        "\n        ".join([f"level {n} is taken: {RESERVED[n]}"
                           for n in intruding]))

    # --- and what that ordering actually does, on the arbiter itself ---------
    #
    # The checks above are text. This one programs the real Plic with the levels
    # the firmware just said it writes, raises every source at once, and reads
    # the order back out of the claim register.
    count = max(pac_sources.values())
    dut = Plic(sources=count)
    order = []

    async def testbench(ctx):
        bus = Bus(ctx, dut.bus, verbose)
        await bus.write(ENABLE_BASE + 0, 0xff)
        await bus.write(ENABLE_BASE + 1, 0xff)
        for number, level in sorted(levels.items()):
            if number <= count:
                await bus.write(priority_addr(number),
                                level_value(level, priorities) or 0)
        ctx.set(dut.sources, ((1 << (count + 1)) - 1) & ~1)
        await ctx.tick()
        # Claim without completing: each claim gates its source, so the next one
        # returns the next in arbitration order. Exactly what the handler loop
        # in `machine_external` does when several sources are pending at once.
        for _ in range(count):
            order.append(await bus.read(CLAIM))

    sim = Simulator(Fragment.get(dut, None))
    sim.add_clock(1e-6)
    sim.add_testbench(testbench)
    sim.run()

    # Highest level first; a tie goes to the lowest source number.
    expected = sorted(wanted, key=lambda n: (-wanted[n], n))
    checks.check(
        "the claim order is the table's order",
        order == expected,
        f"claimed {order}, the table wants {expected}.\n"
        "Source-number order here means the priorities were written and "
        "ignored -- which is what a flat ranking looks like from the arbiter.")

    alert = pac_sources.get("BOARD_I2C_MUX_POWER_ALERT_IRQ")
    console = pac_sources.get("CONSOLE_IRQ")
    checks.check(
        "a hardware fault outranks a lower-numbered console",
        order and order[0] == alert,
        f"source {console} was claimed before source {alert} with both "
        f"pending. The console is the lower number, so under a flat ranking it "
        f"wins every time -- and the ranking is meant to be the thing that "
        f"decides, not the wiring order.")


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="print every CSR bus access")
    args = parser.parse_args()

    checks = Checks(emit)

    emit("vexii_plic.Plic")
    run_plic_checks(checks, args.verbose)
    emit()
    emit("uart16550.Uart16550.irq")
    run_uart_checks(checks, args.verbose)
    emit()
    emit("i2c_mux.I2CBusMux -> Plic -- a source per FUSB302B")
    run_type_c_checks(checks, args.verbose)
    emit()
    emit("the ranking -- src/plic.rs against this file's table (#344)")
    run_ranking_checks(checks, args.verbose)
    return checks.summary()


if __name__ == "__main__":
    sys.exit(main())
