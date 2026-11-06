#
# A synchronous FIFO in block RAM with amaranth's SyncFIFO cycle behaviour.
# SPDX-License-Identifier: GPL-3.0-or-later

"""
`SyncFIFO` is a drop-in for `amaranth.lib.fifo.SyncFIFO` whose storage maps to block RAM (#619).

- amaranth's `SyncFIFO` reads its memory combinationally, so yosys puts it in distributed RAM.
- `SyncFIFOBuffered` uses block RAM but makes `r_rdy` rise a cycle later after a write into an
  empty FIFO. This one adds a bypass register for that write instead, so every port
  (`w_rdy`, `r_rdy`, `level`, `w_level`, `r_level`, `r_data` while `r_rdy`) matches
  `SyncFIFO` on every cycle for any input sequence.
- Proof: `scripts/soc/sie_fifo_equiv_sim.py` runs both in lockstep.
- `r_data` while `r_rdy` is low is unspecified (as in amaranth's docs) and differs.
"""

from amaranth import Elaboratable, Module, Mux, Signal
from amaranth.lib.fifo import FIFOInterface
from amaranth.lib.memory import Memory

__all__ = ["SyncFIFO"]


def _incr(counter, modulo):
    if modulo == 2 ** len(counter):
        return counter + 1
    return Mux(counter == modulo - 1, 0, counter + 1)


class SyncFIFO(Elaboratable, FIFOInterface):
    def __init__(self, *, width, depth):
        if depth < 2:
            raise ValueError(f"depth must be at least 2, not {depth}")
        super().__init__(width=width, depth=depth)
        self.level = Signal(range(depth + 1))

    def elaborate(self, platform):
        m = Module()
        depth = self.depth

        # Registered read port: what lets yosys pick DP16KD.
        m.submodules.storage = storage = Memory(shape=self.width, depth=depth, init=[])
        w_port = storage.write_port()
        r_port = storage.read_port(domain="sync")

        produce = Signal(range(depth))
        consume = Signal(range(depth))
        stored = Signal(range(depth + 1))       # entries in `storage`, not on the output
        bypass_data = Signal(self.width)
        bypass_sel = Signal()                   # output is bypass_data, not the RAM

        # Invariant: ~r_rdy implies stored == 0, so stored <= depth - 1 and the
        # read and write addresses never collide.
        do_write = self.w_rdy & self.w_en
        out_free = ~self.r_rdy | self.r_en      # output stage empty after this cycle
        refill = out_free & (stored != 0)
        bypass = out_free & (stored == 0) & do_write
        mem_write = do_write & ~bypass

        m.d.comb += [
            self.level.eq(stored + self.r_rdy),
            self.w_level.eq(self.level),
            self.r_level.eq(self.level),
            self.w_rdy.eq(self.level != depth),
            self.r_data.eq(Mux(bypass_sel, bypass_data, r_port.data)),
            w_port.addr.eq(produce),
            w_port.data.eq(self.w_data),
            w_port.en.eq(mem_write),
            r_port.addr.eq(consume),
            r_port.en.eq(refill),
        ]

        with m.If(mem_write):
            m.d.sync += produce.eq(_incr(produce, depth))
        with m.If(refill):
            m.d.sync += consume.eq(_incr(consume, depth))
        with m.If(mem_write & ~refill):
            m.d.sync += stored.eq(stored + 1)
        with m.Elif(refill & ~mem_write):
            m.d.sync += stored.eq(stored - 1)

        with m.If(refill):
            m.d.sync += [self.r_rdy.eq(1), bypass_sel.eq(0)]
        with m.Elif(bypass):
            m.d.sync += [self.r_rdy.eq(1), bypass_sel.eq(1), bypass_data.eq(self.w_data)]
        with m.Elif(self.r_en):
            m.d.sync += self.r_rdy.eq(0)

        return m
