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

Exit status is 0 if every assertion held. Log at tmp/logs/uart16550_sim.log.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = ROOT / "tmp" / "logs" / "uart16550_sim.log"

sys.path.insert(0, str(ROOT / "ecp5-test" / "riscv"))

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
LSR_THRE = 1 << 5
LSR_TEMT = 1 << 6

LCR_DLAB = 1 << 7


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

    LOG.parent.mkdir(parents=True, exist_ok=True)
    with LOG.open("w") as handle:
        def emit(text=""):
            print(text, flush=True)
            handle.write(text + "\n")
            handle.flush()

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

            # ---- an empty peripheral is ready to send and has nothing to say -
            lsr = await csr_read(ctx, dut, LSR)
            check("LSR at rest is THRE|TEMT with DR clear",
                  lsr == (LSR_THRE | LSR_TEMT),
                  f"LSR = {lsr:#04x}, expected {LSR_THRE | LSR_TEMT:#04x}")

            # ---- transmit ----------------------------------------------------
            await csr_write(ctx, dut, THR, ord("A"))
            # Two ticks of settling before LSR is believed.
            #
            # `SyncFIFOBuffered` registers its output, so a byte written on one
            # cycle is not visible at `r_rdy` -- and therefore not visible in
            # TEMT -- until the cycle after next. That is a property of the FIFO,
            # not a bug: TEMT means "the transmitter has nothing left", and a
            # byte still propagating into the output register genuinely has not
            # reached it yet. No firmware depends on TEMT; it is asserted here so
            # that if it ever starts lying about a FIFO that is actually full,
            # this says so.
            await ctx.tick()
            await ctx.tick()
            lsr = await csr_read(ctx, dut, LSR)
            check("writing THR clears TEMT and leaves THRE set",
                  lsr & LSR_TEMT == 0 and lsr & LSR_THRE,
                  f"LSR = {lsr:#04x}")
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
            for index in range(FIFO_DEPTH):
                lsr = await csr_read(ctx, dut, LSR)
                if not lsr & LSR_THRE:
                    check("THRE stays set until the transmit FIFO is full",
                          False, f"THRE cleared after {index} bytes")
                    break
                await csr_write(ctx, dut, THR, index)
            else:
                lsr = await csr_read(ctx, dut, LSR)
                check("THRE clears exactly when the transmit FIFO is full",
                      lsr & LSR_THRE == 0,
                      f"LSR = {lsr:#04x} after {FIFO_DEPTH} bytes; the FIFO "
                      f"accepted more than its declared depth")

            drained = []
            while True:
                byte = await pop_tx(ctx, dut)
                if byte is None:
                    break
                drained.append(byte)
            check("the transmit FIFO holds and returns all 16 bytes in order",
                  drained == list(range(FIFO_DEPTH)),
                  f"drained {len(drained)} bytes: {drained}")

        sim = Simulator(dut)
        sim.add_clock(1e-6)
        sim.add_testbench(bench)
        sim.run()

        emit()
        if failures:
            emit(f"{len(failures)} FAILED: {', '.join(failures)}")
        else:
            emit("all checks passed")
        emit(f"log: {LOG.relative_to(ROOT)}")
        return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
