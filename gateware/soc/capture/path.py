#!/usr/bin/env python3
#
# Capture path: framer + ring + record-aligned blocks -> switchable LZ4 compressor.
# SPDX-License-Identifier: BSD-3-Clause

"""`CapturePath`: packets/events in, compressed blocks out. Design: `docs/capture-compression.md`."""

from amaranth            import Module
from amaranth.lib        import wiring
from amaranth.lib.wiring import In, Out

from .codec     import (CODEC_ID_BITS, ID_LZ4, LEN_BITS, SwitchableCompressor,
                        byte_stream_signature)
from .framer    import CaptureRing
from . import lz4_compress  # noqa: F401  (registers the lz4 codec)

__all__ = ["CapturePath"]

PASSTHROUGH = ("pkt_data", "pkt_valid", "pkt_last", "pkt_err", "ev_valid", "ev_code", "flush")
READBACK    = ("blk_ts", "blk_ts_valid", "now", "ring_used", "n_pkt", "n_sof", "n_nak",
               "n_err", "n_dropped", "n_lost")


class CapturePath(wiring.Component):
    def __init__(self, *, window=1024, **ring_args):
        self._window, self._ring_args = window, ring_args
        super().__init__({
            "pkt_data": In(8), "pkt_valid": In(1), "pkt_last": In(1), "pkt_err": In(1),
            "ev_valid": In(1), "ev_code": In(8),
            "flush": In(1), "mode": In(CODEC_ID_BITS, init=ID_LZ4),
            "source": Out(byte_stream_signature()),
            "codec_id": Out(CODEC_ID_BITS), "comp_len": Out(LEN_BITS), "raw_len": Out(LEN_BITS),
            "blk_ts": Out(16), "blk_ts_valid": Out(1), "now": Out(16),
            "ring_used": Out(16),
            "n_pkt": Out(16), "n_sof": Out(16), "n_nak": Out(16), "n_err": Out(16),
            "n_dropped": Out(16), "n_lost": Out(16),
        })

    def elaborate(self, platform):
        m = Module()
        ring = m.submodules.ring = CaptureRing(**self._ring_args)
        comp = m.submodules.comp = SwitchableCompressor(window=self._window)
        for name in PASSTHROUGH:
            m.d.comb += getattr(ring, name).eq(getattr(self, name))
        m.d.comb += [
            comp.mode.eq(self.mode),
            comp.sink.data.eq(ring.source.data), comp.sink.valid.eq(ring.source.valid),
            comp.sink.last.eq(ring.source.last), ring.source.ready.eq(comp.sink.ready),
            self.source.data.eq(comp.source.data), self.source.valid.eq(comp.source.valid),
            self.source.last.eq(comp.source.last), comp.source.ready.eq(self.source.ready),
            self.codec_id.eq(comp.codec_id), self.comp_len.eq(comp.comp_len),
            self.raw_len.eq(comp.raw_len),
        ]
        for name in READBACK:
            m.d.comb += getattr(self, name).eq(getattr(ring, name))
        return m
