#!/usr/bin/env python3
#
# One Wishbone-to-CSR bridge for every CSR peripheral, with an ERR for holes.
# SPDX-License-Identifier: BSD-3-Clause

"""
`amaranth_soc.csr.wishbone.WishboneCSRBridge`, plus an ERR for an unmapped word.

- Same byte-stepping and registered read as upstream: one CSR byte per cycle,
  so a 32-bit access takes five cycles and strobes each byte once.
- `csr_bus` is a `csr.Decoder` over every peripheral. One bridge instead of
  one per peripheral: each upstream bridge carries its own cycle counter and a
  24-bit read re-register.
- A word that no window of `csr_bus` overlaps answers ERR in the request
  cycle and strobes nothing. Upstream acks zeros, which `BusFault` cannot see
  because the Wishbone window claims the address.
"""

from amaranth import Cat, Module, Signal
from amaranth.lib import wiring
from amaranth.lib.wiring import In
from amaranth.utils import exact_log2

from amaranth_soc import wishbone
from amaranth_soc.memory import MemoryMap

__all__ = ["CsrBridge"]


class CsrBridge(wiring.Component):
    def __init__(self, csr_bus, *, data_width=32, name=None,
                 features=("cti", "bte", "err")):
        if csr_bus.data_width != 8:
            raise ValueError("CSR bus must be 8 bits wide")
        ratio = data_width // csr_bus.data_width
        # All three features, so the Wishbone decoder builds no feature shim.
        wb_sig = wishbone.Signature(
            addr_width=csr_bus.addr_width - exact_log2(ratio),
            data_width=data_width, granularity=csr_bus.data_width,
            features=set(features))
        super().__init__({"wb_bus": In(wb_sig)})

        self.wb_bus.memory_map = MemoryMap(addr_width=csr_bus.addr_width,
                                           data_width=csr_bus.data_width)
        self.wb_bus.memory_map.add_window(csr_bus.memory_map, name=name)
        self._csr_bus = csr_bus

    @property
    def csr_bus(self):
        return self._csr_bus

    def _mapped_words(self):
        """(word address prefix, prefix width) per window, for the hit test."""
        lanes = len(self.wb_bus.sel)
        shift = exact_log2(lanes)
        out = []
        for _, _, (start, end, _) in self.csr_bus.memory_map.windows():
            first, last = start >> shift, (end - 1) >> shift
            words = last - first + 1
            if words & (words - 1) or first % words:
                raise ValueError(f"window {start:#x}..{end:#x} is not an aligned "
                                 f"power-of-two number of words")
            out.append((first, exact_log2(words)))
        return out

    def elaborate(self, platform):
        csr_bus, wb_bus = self.csr_bus, self.wb_bus
        m = Module()

        lanes = len(wb_bus.sel)
        cycle = Signal(range(lanes + 1))
        m.d.comb += csr_bus.addr.eq(Cat(cycle[:exact_log2(lanes)], wb_bus.adr))

        hit = Signal()
        for first, low in self._mapped_words():
            with m.If(wb_bus.adr[low:] == (first >> low)):
                m.d.comb += hit.eq(1)

        def lane(index):
            return slice(index * wb_bus.granularity,
                         (index + 1) * wb_bus.granularity)

        with m.If(wb_bus.cyc & wb_bus.stb):
            with m.If(~hit):
                m.d.comb += wb_bus.err.eq(1)
            with m.Else():
                with m.Switch(cycle):
                    for index, sel in enumerate(wb_bus.sel):
                        with m.Case(index):
                            if index > 0:
                                # CSR reads are registered; register again.
                                m.d.sync += wb_bus.dat_r[lane(index - 1)].eq(
                                    csr_bus.r_data)
                            m.d.comb += [
                                csr_bus.r_stb.eq(sel & ~wb_bus.we),
                                csr_bus.w_data.eq(wb_bus.dat_w[lane(index)]),
                                csr_bus.w_stb.eq(sel & wb_bus.we),
                            ]
                            m.d.sync += cycle.eq(index + 1)
                    with m.Default():
                        m.d.sync += wb_bus.dat_r[lane(lanes - 1)].eq(
                            csr_bus.r_data)
                        m.d.sync += wb_bus.ack.eq(1)

        with m.If(wb_bus.ack):
            m.d.sync += [cycle.eq(0), wb_bus.ack.eq(0)]

        return m
