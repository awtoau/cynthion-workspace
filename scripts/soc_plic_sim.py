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
anything belonging to the other. `docs/decisions.md` decision 8 is the argument;
these are the assertions that it is built that way.

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
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

from amaranth import Module                  # noqa: E402
from amaranth.hdl import Fragment            # noqa: E402
from amaranth.sim import Simulator           # noqa: E402

from i2c_mux import I2CBusMux                # noqa: E402
from uart16550 import Uart16550              # noqa: E402
from vexii_plic import Plic, CONTEXT_BASE, ENABLE_BASE, PENDING_BASE  # noqa: E402

# Source numbers, matching what ecp5-test/riscv/vexii_hello_soc.py wires up.
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


class Checks:
    """Assertions, counted, with the failure printed rather than raised.

    Raising on the first failure hides every later one, and when an interrupt
    controller is wrong it is usually wrong in more than one way at once.
    """

    def __init__(self, emit):
        self.emit = emit
        self.failures = []

    def check(self, name, ok, detail=""):
        self.emit(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            self.failures.append(name)
            for line in str(detail).splitlines():
                self.emit(f"        {line}")


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
            "sources a critical section blocks, which is what RTIC will use "
            "the threshold for.")
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
    is the composition `vexii_hello_soc.py` builds -- each `int` line on its own
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

    if checks.failures:
        emit(f"{len(checks.failures)} FAILED: "
             f"{', '.join(checks.failures)}")
    else:
        emit("all checks passed")
    return 1 if checks.failures else 0


if __name__ == "__main__":
    sys.exit(main())
