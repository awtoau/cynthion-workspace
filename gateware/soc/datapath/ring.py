#
# Byte FIFO in block RAM whose read side can be rewound to its last commit.
# SPDX-License-Identifier: BSD-3-Clause

"""`RewindFifo`: one clock domain, `depth` bytes, first-word-fall-through read.

- Write side: `w_data`/`w_en`/`w_rdy`. Space is counted from the COMMITTED read
  pointer, so bytes read but not committed are never overwritten.
- Read side: `r_data` is valid while `r_rdy`; `r_en` advances the speculative pointer.
- `commit`: the commit pointer takes the read pointer (after this cycle's `r_en`).
- `rewind`: the read pointer returns to the commit pointer (wins over `r_en`).
- `flush`: empty, all pointers to 0.
- A plain FIFO is this with `commit` tied high (the constant folds away).
- Used for the OUT ring (a NAKed packet is re-sent from the same bytes) and the IN FIFO.
"""

from amaranth             import Module, Signal, Mux
from amaranth.lib         import wiring
from amaranth.lib.memory  import Memory
from amaranth.lib.wiring  import In, Out

__all__ = ["RewindFifo"]


class RewindFifo(wiring.Component):
    def __init__(self, depth=2048):
        assert depth & (depth - 1) == 0, "depth must be a power of two"
        self.depth = depth
        super().__init__({
            "w_data": In(8), "w_en": In(1), "w_rdy": Out(1),
            "w_level": Out(range(depth + 1)),
            "r_data": Out(8), "r_en": In(1), "r_rdy": Out(1),
            "r_level": Out(range(depth + 1)),
            "commit": In(1), "rewind": In(1), "flush": In(1),
        })

    def elaborate(self, platform):
        m = Module()
        depth = self.depth
        abits = (depth - 1).bit_length()
        m.submodules.mem = mem = Memory(shape=8, depth=depth, init=[])
        wport = mem.write_port()
        rport = mem.read_port(transparent_for=(wport,))

        wp, rp, cp = (Signal(abits + 1, name=n) for n in ("wp", "rp", "cp"))
        rp_next = Signal(abits + 1)
        pop = self.r_en & self.r_rdy
        push = self.w_en & self.w_rdy
        m.d.comb += [
            self.w_level.eq(wp - cp),
            self.w_rdy.eq(self.w_level != depth),
            self.r_level.eq(wp - rp),
            self.r_rdy.eq(wp != rp),
            rp_next.eq(Mux(self.rewind, cp, rp + pop)),
            wport.addr.eq(wp[:abits]), wport.data.eq(self.w_data), wport.en.eq(push),
            rport.addr.eq(rp_next[:abits]),
            self.r_data.eq(rport.data),
        ]
        m.d.sync += [wp.eq(wp + push), rp.eq(rp_next)]
        with m.If(self.commit):
            m.d.sync += cp.eq(rp_next)
        with m.If(self.flush):
            m.d.sync += [wp.eq(0), rp.eq(0), cp.eq(0)]
        return m
