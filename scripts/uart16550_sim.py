#!/usr/bin/env python3
#
# Simulate the 16550 peripheral and assert its register map, byte by byte.
# SPDX-License-Identifier: BSD-3-Clause

"""
Drives `ecp5-test/riscv/uart16550.py` over its CSR bus and checks what it does.

    ./scripts/uart16550_sim.py
    ./scripts/uart16550_sim.py -v      # print every access

## Why simulate this at all

The bug this peripheral replaces was a *read with a side effect sharing a 32-bit
word with a polled status register*, and it cost multiple sessions to find on
hardware because every symptom was silence. The assertion that matters most here
takes three lines in a simulator and a bitstream rebuild plus a USB enumeration
on the board:

    read every register that is not RBR, a thousand times, and require that the
    receive FIFO still holds exactly what was put into it.

That is `poll_does_not_pop` below. Everything else is here because a register map
that is *nearly* a 16550 is worse than one that is obviously not: a generic
driver would set DLAB, write a divisor, and transmit two junk bytes.

The reads that DO change state are asserted here too, in both directions -- that
each clears what it is meant to, and that nothing else does. A side effect that
goes missing is a driver that never returns from a handler; one that appears
where the table says there is none is the original bug. Three of these assertions
already earned their place: THRE reporting space rather than emptiness, IIR
holding an id at rest, and MSR failing the probe Linux opens with.

Exit status is 0 if every assertion held. Log at tmp/logs/dev.log.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit  # noqa: E402

from amaranth.sim import Simulator                            # noqa: E402
from uart16550 import Uart16550, FIFO_DEPTH                   # noqa: E402

# Register offsets, spelled out here rather than imported, so this test would
# still fail if the peripheral silently renumbered them.
RBR = THR = 0
IER = DLM = 1
IIR = FCR = 2
LCR = 3
MCR = 4
LSR = 5
MSR = 6
SCR = 7

LSR_DR   = 1 << 0
LSR_OE   = 1 << 1
LSR_FE   = 1 << 3
LSR_THRE = 1 << 5
LSR_TEMT = 1 << 6
LSR_ERR  = 1 << 7

LCR_DLAB = 1 << 7

IER_ERBFI = 1 << 0
IER_ETBEI = 1 << 1
IER_ELSI  = 1 << 2

# IIR's low nibble: bit 0 CLEAR means an interrupt is pending, bits 3:1 say which.
IIR_NONE = 0b0001
IIR_THRE = 0b0010
IIR_RX   = 0b0100
IIR_LINE = 0b0110


async def csr_write(ctx, dut, addr, value):
    """One CSR write. `w_stb` is registered inside the multiplexer, so the
    effect lands a cycle after the strobe -- hence the trailing tick."""
    ctx.set(dut.bus.addr, addr)
    ctx.set(dut.bus.w_data, value)
    ctx.set(dut.bus.w_stb, 1)
    await ctx.tick()
    ctx.set(dut.bus.w_stb, 0)
    await ctx.tick()


async def csr_read(ctx, dut, addr):
    """One CSR read. Reads are pipelined by one cycle: the multiplexer latches
    the addressed register into a shadow chunk, and `r_data` follows."""
    ctx.set(dut.bus.addr, addr)
    ctx.set(dut.bus.r_stb, 1)
    await ctx.tick()
    ctx.set(dut.bus.r_stb, 0)
    value = ctx.get(dut.bus.r_data)
    await ctx.tick()
    return value


async def push_rx(ctx, dut, byte):
    """Deliver one byte from the transport into the receive FIFO."""
    ctx.set(dut.sink.payload, byte)
    ctx.set(dut.sink.valid, 1)
    await ctx.tick()
    ctx.set(dut.sink.valid, 0)
    await ctx.tick()


async def pulse(ctx, signal):
    """One cycle on a transport error input, the way the transport drives it."""
    ctx.set(signal, 1)
    await ctx.tick()
    ctx.set(signal, 0)
    await ctx.tick()


async def pop_tx(ctx, dut):
    """Take one byte out of the transmit stream, or None if none is offered."""
    if not ctx.get(dut.source.valid):
        return None
    byte = ctx.get(dut.source.payload)
    ctx.set(dut.source.ready, 1)
    await ctx.tick()
    ctx.set(dut.source.ready, 0)
    await ctx.tick()
    return byte


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    failures = []

    def check(name, ok, detail=""):
        emit(f"  {'PASS' if ok else 'FAIL'}  {name}")
        if not ok:
            failures.append(name)
            for line in str(detail).splitlines():
                emit(f"        {line}")

    dut = Uart16550()

    async def bench(ctx):
        # ---- the peripheral identifies as a 16550A ----------------------
        await csr_write(ctx, dut, FCR, 0x01)
        iir = await csr_read(ctx, dut, IIR)
        check("IIR reports FIFOs enabled and no interrupt pending",
              iir == 0xc1, f"IIR = {iir:#04x}, expected 0xc1")

        await csr_write(ctx, dut, SCR, 0xa5)
        scr = await csr_read(ctx, dut, SCR)
        check("SCR is eight bits of readable scratch",
              scr == 0xa5, f"SCR = {scr:#04x}, expected 0xa5")

        msr = await csr_read(ctx, dut, MSR)
        check("MSR reports CTS, DSR and DCD asserted",
              msr == 0xb0, f"MSR = {msr:#04x}, expected 0xb0")

        # Linux's `autoconfig` opens with exactly this and gives up on the
        # port if the answer is wrong -- "check to see if a UART is really
        # there" in 8250_port.c. A constant MSR fails it.
        await csr_write(ctx, dut, MCR, 0x10 | 0x08 | 0x02)  # LOOP|OUT2|RTS
        msr = await csr_read(ctx, dut, MSR)
        check("MCR.LOOP routes OUT2 and RTS to DCD and CTS  (autoconfig)",
              msr & 0xf0 == 0x90,
              f"MSR = {msr:#04x}, expected DCD|CTS in the top nibble. "
              f"Linux abandons the port on any other answer.")
        await csr_write(ctx, dut, MCR, 0x00)
        msr = await csr_read(ctx, dut, MSR)
        check("and clearing LOOP puts the constant back",
              msr == 0xb0, f"MSR = {msr:#04x}")

        # ---- an empty peripheral is ready to send and has nothing to say -
        lsr = await csr_read(ctx, dut, LSR)
        check("LSR at rest is THRE|TEMT with DR clear",
              lsr == (LSR_THRE | LSR_TEMT),
              f"LSR = {lsr:#04x}, expected {LSR_THRE | LSR_TEMT:#04x}")

        # ---- transmit ----------------------------------------------------
        await csr_write(ctx, dut, THR, ord("A"))
        # Two ticks of settling before LSR is believed.
        #
        # THRE and TEMT come off the FIFO's `level`, which counts a byte one
        # cycle after the write strobe -- so one tick would do, and two is
        # what a CPU takes to come back round the bus anyway. `r_rdy` would
        # need three: `SyncFIFOBuffered` registers its output, so a byte
        # written on one cycle does not reach it until the cycle after next,
        # and a driver polling in between would be told the transmitter was
        # empty while holding a byte.
        await ctx.tick()
        await ctx.tick()
        lsr = await csr_read(ctx, dut, LSR)
        check("writing THR clears THRE and TEMT together",
              lsr & (LSR_THRE | LSR_TEMT) == 0,
              f"LSR = {lsr:#04x}. THRE means the transmit FIFO is EMPTY, "
              f"not that it has room -- a driver that sees it set writes a "
              f"whole FIFO.")
        sent = await pop_tx(ctx, dut)
        check("the byte written to THR appears on the source stream",
              sent == ord("A"), f"got {sent!r}")

        # ---- receive ------------------------------------------------------
        await push_rx(ctx, dut, ord("q"))
        lsr = await csr_read(ctx, dut, LSR)
        check("a byte from the sink stream sets LSR.DR",
              lsr & LSR_DR, f"LSR = {lsr:#04x}")
        got = await csr_read(ctx, dut, RBR)
        check("RBR returns the received byte",
              got == ord("q"), f"got {got!r}")
        lsr = await csr_read(ctx, dut, LSR)
        check("reading RBR pops the FIFO, so DR clears",
              lsr & LSR_DR == 0, f"LSR = {lsr:#04x}")

        # ---- THE ONE THAT MATTERS ----------------------------------------
        #
        # Fill the receive FIFO, then hammer every register except RBR --
        # including LSR, which is what a poll loop reads -- and require that
        # not one byte has been consumed. This is the assertion the previous
        # peripheral would have failed the moment anything widened a byte
        # read of `rx_valid` at +3 into a word read covering `rx_data` at +2.
        for byte in b"keepme":
            await push_rx(ctx, dut, byte)
        for _ in range(50):
            for addr in (IER, IIR, LCR, MCR, LSR, MSR, SCR):
                await csr_read(ctx, dut, addr)
        received = bytes([await csr_read(ctx, dut, RBR) for _ in range(6)])
        check("polling any register other than RBR never pops the FIFO",
              received == b"keepme",
              f"expected b'keepme' after 350 status reads, got {received!r}")

        # ---- DLAB ----------------------------------------------------------
        #
        # A generic driver's opening move is: set DLAB, write the divisor,
        # clear DLAB. If DLAB did not gate the THR push, that divisor would
        # be transmitted as two characters -- a corrupt banner that looks
        # like a baud mismatch and is not one.
        await csr_write(ctx, dut, LCR, LCR_DLAB | 0x03)
        await csr_write(ctx, dut, THR, 0x0c)     # DLL
        await csr_write(ctx, dut, DLM, 0x00)     # DLM
        leaked = await pop_tx(ctx, dut)
        check("writing the divisor latches transmits nothing",
              leaked is None, f"the source stream offered {leaked!r}")
        dll = await csr_read(ctx, dut, RBR)
        check("DLL reads back what was written to it",
              dll == 0x0c, f"DLL = {dll:#04x}")
        await csr_write(ctx, dut, LCR, 0x03)
        lcr = await csr_read(ctx, dut, LCR)
        check("LCR reads back, DLAB cleared",
              lcr == 0x03, f"LCR = {lcr:#04x}")

        # ---- FIFO clear ------------------------------------------------------
        await push_rx(ctx, dut, ord("x"))
        await csr_write(ctx, dut, FCR, 0x01 | 0x02)   # bit 1 clears RX
        lsr = await csr_read(ctx, dut, LSR)
        check("FCR bit 1 clears the receive FIFO",
              lsr & LSR_DR == 0, f"LSR = {lsr:#04x}")

        await csr_write(ctx, dut, THR, ord("z"))
        await csr_write(ctx, dut, FCR, 0x01 | 0x04)   # bit 2 clears TX
        lsr = await csr_read(ctx, dut, LSR)
        check("FCR bit 2 clears the transmit FIFO",
              lsr & LSR_TEMT, f"LSR = {lsr:#04x}")

        # ---- the FIFO is 16 deep and says so ----------------------------------
        #
        # Written as one burst with no polling in between, which is what a
        # driver does on seeing THRE: the standard promises room for a whole
        # FIFO, and Linux's 8250 writes `up->tx_loadsz` bytes without looking
        # again. If this peripheral took fewer, that driver would lose the
        # remainder silently.
        lsr = await csr_read(ctx, dut, LSR)
        check("THRE is set before the burst, which is the promise being taken",
              lsr & LSR_THRE, f"LSR = {lsr:#04x}")
        for index in range(FIFO_DEPTH):
            await csr_write(ctx, dut, THR, index)
        lsr = await csr_read(ctx, dut, LSR)
        check("THRE stays clear while the transmit FIFO holds anything",
              lsr & (LSR_THRE | LSR_TEMT) == 0,
              f"LSR = {lsr:#04x} after {FIFO_DEPTH} bytes")

        drained = []
        while True:
            byte = await pop_tx(ctx, dut)
            if byte is None:
                break
            drained.append(byte)
        check("the transmit FIFO holds and returns all 16 bytes in order",
              drained == list(range(FIFO_DEPTH)),
              f"drained {len(drained)} bytes: {drained}")

        # ---- reading IIR clears the transmit-empty interrupt -------------
        #
        # The sequence every 16550 driver runs: take the interrupt, read IIR
        # to find out why, act, return. If the read does not clear a
        # transmit-empty interrupt the line is still asserted at the return
        # and the handler is re-entered forever, which on this board is a
        # dead-looking CPU with a running clock.
        await csr_write(ctx, dut, IER, IER_ETBEI)
        # `irq` sampled BEFORE the read, because the read is what clears it.
        asserted = ctx.get(dut.irq)
        iir = await csr_read(ctx, dut, IIR)
        check("enabling ETBEI with an empty FIFO raises the interrupt",
              asserted and iir & 0x0f == IIR_THRE,
              f"irq = {asserted}, IIR = {iir:#04x}. A driver whose "
              f"first act is to enable transmit interrupts would otherwise "
              f"wait forever for an edge that had already happened.")

        iir = await csr_read(ctx, dut, IIR)
        check("reading IIR clears it",
              not ctx.get(dut.irq) and iir & 0x0f == IIR_NONE,
              f"irq = {ctx.get(dut.irq)}, second IIR read = {iir:#04x}")

        for _ in range(20):
            await csr_read(ctx, dut, LSR)
        check("and it stays clear while the FIFO stays empty",
              not ctx.get(dut.irq),
              "the transmit interrupt came back without the FIFO having "
              "been written and drained -- it is level-derived again.")

        # It must come back on the next empty, or a driver that stopped
        # sending when its ring emptied never restarts.
        await csr_write(ctx, dut, THR, ord("!"))
        await ctx.tick()
        check("writing THR takes it away rather than raising it",
              not ctx.get(dut.irq),
              "irq asserted with a byte still in the transmit FIFO")
        sent = await pop_tx(ctx, dut)
        await ctx.tick()
        check("draining the FIFO raises it again",
              sent == ord("!") and ctx.get(dut.irq),
              f"sent {sent!r}, irq = {ctx.get(dut.irq)}")

        # A read taken for a RECEIVE interrupt must not swallow it. IIR
        # reports one source; clearing a source it did not report is how a
        # transmit path wedges after an unrelated interrupt.
        await csr_write(ctx, dut, IER, IER_ETBEI | IER_ERBFI)
        await push_rx(ctx, dut, ord("r"))
        iir = await csr_read(ctx, dut, IIR)
        check("a receive interrupt outranks a transmit one in IIR",
              iir & 0x0f == IIR_RX, f"IIR = {iir:#04x}")
        got = await csr_read(ctx, dut, RBR)
        iir = await csr_read(ctx, dut, IIR)
        check("and reading IIR for it left the transmit one alone",
              got == ord("r") and iir & 0x0f == IIR_THRE,
              f"RBR = {got!r}, IIR after draining the byte = {iir:#04x}")
        await csr_read(ctx, dut, IIR)
        await csr_write(ctx, dut, IER, 0)

        # ---- the LSR error bits latch, and a read clears them ------------
        #
        # OE is the one that matters: a console that drops bytes without
        # saying so is the failure this project keeps meeting. It cannot be
        # detected inside this module -- `sink.ready` is real backpressure, so
        # a full FIFO here is a stall and not a loss -- so the transport
        # reports it on a pulse.
        await pulse(ctx, dut.overrun)
        lsr = await csr_read(ctx, dut, LSR)
        check("an overrun pulse sets LSR.OE and the bit 7 summary",
              lsr & LSR_OE and lsr & LSR_ERR,
              f"LSR = {lsr:#04x}")
        lsr = await csr_read(ctx, dut, LSR)
        check("and reading LSR cleared both -- an error is reported once",
              lsr & (LSR_OE | LSR_ERR) == 0, f"LSR = {lsr:#04x}")

        await pulse(ctx, dut.frame_error)
        lsr = await csr_read(ctx, dut, LSR)
        check("a framing pulse sets LSR.FE and the summary",
              lsr & LSR_FE and lsr & LSR_ERR, f"LSR = {lsr:#04x}")
        lsr = await csr_read(ctx, dut, LSR)
        check("and it too clears on the read",
              lsr & (LSR_FE | LSR_ERR) == 0, f"LSR = {lsr:#04x}")

        check("PE and BI never set -- no parity is checked, no break detected",
              lsr & 0b0001_0100 == 0, f"LSR = {lsr:#04x}")

        # With ELSI enabled the error is an interrupt, at the top of the
        # priority order, and the same read clears it.
        await csr_write(ctx, dut, IER, IER_ELSI)
        await pulse(ctx, dut.overrun)
        iir = await csr_read(ctx, dut, IIR)
        check("an error raises the line-status interrupt when ELSI is set",
              ctx.get(dut.irq) and iir & 0x0f == IIR_LINE,
              f"irq = {ctx.get(dut.irq)}, IIR = {iir:#04x}")
        lsr = await csr_read(ctx, dut, LSR)
        await ctx.tick()
        check("reading LSR clears the interrupt, not just the bit",
              lsr & LSR_OE and not ctx.get(dut.irq),
              f"LSR = {lsr:#04x}, irq = {ctx.get(dut.irq)}")

        # An error arriving on the cycle of the read must survive it. Losing
        # it would make the reporting worst at exactly the rate that matters.
        ctx.set(dut.bus.addr, LSR)
        ctx.set(dut.bus.r_stb, 1)
        ctx.set(dut.overrun, 1)
        await ctx.tick()
        ctx.set(dut.bus.r_stb, 0)
        ctx.set(dut.overrun, 0)
        await ctx.tick()
        lsr = await csr_read(ctx, dut, LSR)
        check("an overrun arriving on the cycle of a read is kept, not lost",
              lsr & LSR_OE, f"LSR = {lsr:#04x}")
        await csr_read(ctx, dut, LSR)
        await csr_write(ctx, dut, IER, 0)

    sim = Simulator(dut)
    sim.add_clock(1e-6)
    sim.add_testbench(bench)
    sim.run()

    emit()
    if failures:
        emit(f"{len(failures)} FAILED: {', '.join(failures)}")
    else:
        emit("all checks passed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
