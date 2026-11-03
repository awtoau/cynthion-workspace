# SPDX-License-Identifier: BSD-3-Clause
"""Option (a): ULPI byte stream (60 MHz) <-> 32-bit word stream (120 MHz) over Gray-pointer FIFOs.

- Crossing: Amaranth stdlib `AsyncFIFO` (Cummings SNUG 2002 style); no clock relationship assumed.
- Width change on the 60 side (`stream.py`), so each FIFO entry is one 35-bit word.
- Reset: both domain resets; each FIFO empties from its write-domain reset.
"""

from amaranth import Elaboratable, Module, Signal
from amaranth.lib.fifo import AsyncFIFO

from .stream import WORD_BITS, Packer, Unpacker

DEPTH = 512     # words per direction: one 512 x 36 EBR each; sizing in docs/ulpi-bridge.md


class BridgePorts:
    """Ports shared by both bridge options. rx = ULPI -> 120 side, tx = 120 side -> ULPI."""

    def __init__(self):
        # 60 MHz side
        self.rx_data, self.rx_last, self.rx_valid, self.rx_ready = Signal(8), Signal(), Signal(), Signal()
        self.rx_overflow = Signal()     # sticky: a byte offered while not ready (ULPI cannot wait)
        self.tx_data, self.tx_last, self.tx_valid, self.tx_ready = Signal(8), Signal(), Signal(), Signal()
        # 120 MHz side
        self.rxw_word, self.rxw_valid, self.rxw_ready = Signal(WORD_BITS), Signal(), Signal()
        self.txw_word, self.txw_valid, self.txw_ready = Signal(WORD_BITS), Signal(), Signal()

    def _wire_bytes(self, m, pack, unpack, usb, rx_gate=1, overflow=True):
        m.d.comb += [pack.i_data.eq(self.rx_data), pack.i_last.eq(self.rx_last),
                     pack.i_valid.eq(self.rx_valid & rx_gate),
                     self.rx_ready.eq(pack.i_ready & rx_gate),
                     self.tx_data.eq(unpack.o_data), self.tx_last.eq(unpack.o_last),
                     self.tx_valid.eq(unpack.o_valid), unpack.o_ready.eq(self.tx_ready)]
        if overflow:
            self._track_overflow(m, usb)

    def _track_overflow(self, m, usb):
        with m.If(self.rx_valid & ~self.rx_ready):
            m.d[usb] += self.rx_overflow.eq(1)


class AsyncBridge(Elaboratable, BridgePorts):
    def __init__(self, *, depth=DEPTH, usb="usb", fast="fast", swap=False):
        BridgePorts.__init__(self)
        self.depth, self.usb, self.fast, self.swap = depth, usb, fast, swap

    def elaborate(self, platform):
        m = Module()
        m.submodules.pack = pack = Packer(domain=self.usb, swap=self.swap)
        m.submodules.unpack = unpack = Unpacker(domain=self.usb)
        m.submodules.rx_fifo = rxf = AsyncFIFO(width=WORD_BITS, depth=self.depth,
                                               w_domain=self.usb, r_domain=self.fast)
        m.submodules.tx_fifo = txf = AsyncFIFO(width=WORD_BITS, depth=self.depth,
                                               w_domain=self.fast, r_domain=self.usb)
        self._wire_bytes(m, pack, unpack, self.usb)
        m.d.comb += [rxf.w_data.eq(pack.o_word), rxf.w_en.eq(pack.o_valid),
                     pack.o_ready.eq(rxf.w_rdy),
                     self.rxw_word.eq(rxf.r_data), self.rxw_valid.eq(rxf.r_rdy),
                     rxf.r_en.eq(self.rxw_ready),
                     txf.w_data.eq(self.txw_word), txf.w_en.eq(self.txw_valid),
                     self.txw_ready.eq(txf.w_rdy),
                     unpack.i_word.eq(txf.r_data), unpack.i_valid.eq(txf.r_rdy),
                     txf.r_en.eq(unpack.i_ready)]
        return m
