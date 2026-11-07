#!/usr/bin/env python3
#
# AUX byte stream -> Wishbone master: the CPU-less device-mode bitstream's register access. #612
# SPDX-License-Identifier: BSD-3-Clause

"""
`AuxCsrBridge`: the PC reads and writes every peripheral through the AUX link; no CPU.

First-pass framing on a byte stream (a stand-in for the AUX link spec, which is open):

    request                                     response
    'W' addr[4 LE] data[4 LE]                   status
    'R' addr[4 LE]                              data[4 LE] status
    'E'                                         events status

- `addr`: byte address on the SoC bus (30-bit word address << 2); low 2 bits ignored, `sel` = 0xf.
- `status`: 0 ok, 1 bus `err` (unclaimed / timed out: `bus/fault.py` answers every access).
- `events`: bit 0 = the interrupt controller's output (level); the PC reads `intc` to see which.
- Unknown opcode: dropped silently, framing resyncs at the next opcode byte.
- One access at a time; the stream's own flow control (`ready`) holds the PC.
- Waits for: ACK or ERR from the bus. `bus/fault.py` bounds every access, so no timer here.
"""

from amaranth import Module, Signal
from amaranth.lib import stream, wiring
from amaranth.lib.wiring import In, Out

from amaranth_soc import wishbone

__all__ = ["AuxCsrBridge", "OP_WRITE", "OP_READ", "OP_EVENTS"]

OP_WRITE, OP_READ, OP_EVENTS = ord("W"), ord("R"), ord("E")


class AuxCsrBridge(wiring.Component):
    wb:     Out(wishbone.Signature(addr_width=30, data_width=32, granularity=8,
                                   features=("err", "cti", "bte")))
    rx:     In(stream.Signature(8))
    tx:     Out(stream.Signature(8))
    events: In(1)

    def elaborate(self, platform):
        m = Module()
        op = Signal(8)
        addr = Signal(32)
        data = Signal(32)
        count = Signal(3)
        err = Signal()
        out = Signal(40)            # response bytes, LSB first
        out_len = Signal(3)

        m.d.comb += [self.wb.sel.eq(0xf), self.wb.adr.eq(addr[2:])]

        with m.FSM():
            with m.State("OP"):
                m.d.comb += self.rx.ready.eq(1)
                with m.If(self.rx.valid):
                    m.d.sync += [op.eq(self.rx.payload), count.eq(0)]
                    with m.Switch(self.rx.payload):
                        with m.Case(OP_WRITE, OP_READ):
                            m.next = "ADDR"
                        with m.Case(OP_EVENTS):
                            m.d.sync += [out.eq(self.events), out_len.eq(2)]
                            m.next = "REPLY"
            with m.State("ADDR"):
                m.d.comb += self.rx.ready.eq(1)
                with m.If(self.rx.valid):
                    m.d.sync += [addr.word_select(count[:2], 8).eq(self.rx.payload),
                                 count.eq(count + 1)]
                    with m.If(count == 3):
                        m.d.sync += count.eq(0)
                        m.next = "AFTER_ADDR"
            with m.State("AFTER_ADDR"):
                with m.If(op == OP_WRITE):
                    m.next = "DATA"
                with m.Else():
                    m.next = "BUS"
            with m.State("DATA"):
                m.d.comb += self.rx.ready.eq(1)
                with m.If(self.rx.valid):
                    m.d.sync += [data.word_select(count[:2], 8).eq(self.rx.payload),
                                 count.eq(count + 1)]
                    with m.If(count == 3):
                        m.next = "BUS"
            with m.State("BUS"):
                m.d.comb += [self.wb.cyc.eq(1), self.wb.stb.eq(1),
                             self.wb.we.eq(op == OP_WRITE), self.wb.dat_w.eq(data)]
                with m.If(self.wb.ack | self.wb.err):
                    m.d.sync += err.eq(self.wb.err)
                    with m.If(op == OP_WRITE):
                        m.d.sync += [out.eq(self.wb.err), out_len.eq(1)]
                    with m.Else():
                        m.d.sync += [out.eq(self.wb.dat_r | (self.wb.err << 32)), out_len.eq(5)]
                    m.next = "REPLY"
            with m.State("REPLY"):
                m.d.comb += [self.tx.valid.eq(1), self.tx.payload.eq(out[:8])]
                with m.If(self.tx.ready):
                    m.d.sync += [out.eq(out >> 8), out_len.eq(out_len - 1)]
                    with m.If(out_len == 1):
                        m.next = "OP"
        return m

