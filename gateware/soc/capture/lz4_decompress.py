#!/usr/bin/env python3
#
# Streaming LZ4 block decompressor.
# SPDX-License-Identifier: BSD-3-Clause

"""Decodes any valid LZ4 block whose offsets are <= WINDOW. Interface: `codec.py`.

## Structure
  - History: WINDOW-byte circular RAM (1 read + 1 write port), written with every
    output byte. Index = output count mod WINDOW.
  - Literals pass straight through (1 byte/clk). A match copy runs at 1 byte/clk
    after a 1-clock prime; an offset-1 run forwards the previous byte instead of
    reading the RAM (a read would race the write of the same address).
  - Output holds the newest byte back one stage so the final byte of the block
    carries `last` even when the block ends in a match or a zero-literal token.

## Corrupt blocks (bounded, never hangs)
  Detected before any byte is emitted: offset 0, offset > bytes produced so far,
  offset > WINDOW, literal or match length that would pass `raw_len`, `last` seen
  in the middle of a sequence, block ended with output != `raw_len`.
  On detection: `error` is set, nothing more is emitted, the rest of the block is
  consumed up to its `last`, then `done` pulses.
  Bound, with `source.ready` held high: the block ends within
  comp_len + raw_len + 2 * sequences + 3 clocks of its first beat, and total
  output <= raw_len. `error` is valid when `done` pulses and held until the next
  block's first beat.
"""

from amaranth            import Cat, Module, Mux, Signal
from amaranth.lib        import wiring
from amaranth.lib.memory import Memory

from .codec import (BLOCK_BYTES, ID_LZ4, LEN_BITS, DecompressorSignature,
                    register_codec)

__all__ = ["LZ4Decompressor"]

MIN_MATCH = 4


class LZ4Decompressor(wiring.Component):
    def __init__(self, window=BLOCK_BYTES, **_):
        assert window & (window - 1) == 0 and 2 <= window <= 4096, "window: power of two"
        self.window = window
        super().__init__(DecompressorSignature())

    def elaborate(self, platform):
        m = Module()
        W    = self.window
        wbits = (W - 1).bit_length()

        mem = Memory(shape=8, depth=W, init=[])
        m.submodules.mem = mem
        wport, rport = mem.write_port(), mem.read_port()

        out_count = Signal(13)
        wp        = Signal(wbits)
        m.d.comb += wp.eq(out_count[:wbits])

        lit_left = Signal(13)
        ml_left  = Signal(13)
        mnib     = Signal(4)
        off_lo   = Signal(8)
        off      = Signal(wbits + 1)
        first    = Signal()
        prev_x   = Signal(8)
        err      = Signal()
        blk_first = Signal(init=1)
        m.d.comb += self.error.eq(err)

        # --- output stage: pending (held back) + source register
        pv, sv, sl = Signal(), Signal(), Signal()
        pd, sd = Signal(8), Signal(8)
        m.d.comb += [self.source.valid.eq(sv), self.source.data.eq(sd), self.source.last.eq(sl)]
        src_free = Signal()
        m.d.comb += src_free.eq(~sv | self.source.ready)
        room = Signal()
        m.d.comb += room.eq(~pv | src_free)

        push, push_data = Signal(), Signal(8)
        finish = Signal()
        drop   = Signal()
        with m.If(self.source.ready & sv):
            m.d.sync += sv.eq(0)
        with m.If(push):
            m.d.sync += [pv.eq(1), pd.eq(push_data)]
            with m.If(pv):
                m.d.sync += [sv.eq(1), sd.eq(pd), sl.eq(0)]
        with m.If(finish):
            m.d.sync += [pv.eq(0), sv.eq(1), sd.eq(pd), sl.eq(1)]
        with m.If(drop):
            m.d.sync += pv.eq(0)

        # window write: combinational, same cycle as the push
        m.d.comb += [wport.addr.eq(wp), wport.data.eq(push_data), wport.en.eq(push)]

        beat   = Signal()
        m.d.comb += beat.eq(self.sink.valid & self.sink.ready)
        d       = self.sink.data
        in_last = self.sink.last
        raw     = self.raw_len

        def bad():
            """Corrupt block seen on an accepted beat: flag it, skip to the block's end."""
            m.d.sync += err.eq(1)
            with m.If(in_last):
                m.next = "DONE"
            with m.Else():
                m.next = "DRAIN"

        read_addr = Signal(wbits)
        m.d.comb += [rport.addr.eq(read_addr), rport.en.eq(0)]

        with m.FSM():
            with m.State("TOKEN"):
                lit0 = d[4:8]
                m.d.comb += self.sink.ready.eq(1)
                with m.If(beat):
                    with m.If(blk_first):
                        m.d.sync += [err.eq(0), blk_first.eq(0)]
                    m.d.sync += [mnib.eq(d[0:4]), lit_left.eq(lit0)]
                    with m.If(lit0 == 15):
                        with m.If(in_last):
                            bad()
                        with m.Else():
                            m.next = "LEXT"
                    with m.Elif(out_count + lit0 > raw):
                        bad()
                    with m.Elif(lit0 != 0):
                        with m.If(in_last):
                            bad()
                        with m.Else():
                            m.next = "LIT"
                    with m.Elif(in_last):
                        m.next = "END"
                    with m.Else():
                        m.next = "OFFLO"
            with m.State("LEXT"):
                new_lit = Signal(13)
                m.d.comb += [self.sink.ready.eq(1), new_lit.eq(lit_left + d)]
                with m.If(beat):
                    m.d.sync += lit_left.eq(new_lit)
                    with m.If(out_count + new_lit > raw):
                        bad()
                    with m.Elif(d != 255):
                        with m.If(in_last):
                            bad()
                        with m.Else():
                            m.next = "LIT"
                    with m.Elif(in_last):
                        bad()
            with m.State("LIT"):
                m.d.comb += self.sink.ready.eq(room)
                with m.If(beat):
                    m.d.comb += [push.eq(1), push_data.eq(d)]
                    m.d.sync += [out_count.eq(out_count + 1), lit_left.eq(lit_left - 1)]
                    with m.If(lit_left == 1):
                        with m.If(in_last):
                            m.next = "END"
                        with m.Else():
                            m.next = "OFFLO"
                    with m.Elif(in_last):
                        bad()
            with m.State("OFFLO"):
                m.d.comb += self.sink.ready.eq(1)
                with m.If(beat):
                    m.d.sync += off_lo.eq(d)
                    with m.If(in_last):
                        bad()
                    with m.Else():
                        m.next = "OFFHI"
            with m.State("OFFHI"):
                full_off = Signal(16)
                m.d.comb += [self.sink.ready.eq(1), full_off.eq(Cat(off_lo, d))]
                with m.If(beat):
                    m.d.sync += off.eq(full_off)
                    with m.If(in_last | (full_off == 0) | (full_off > W) | (full_off > out_count)):
                        bad()
                    with m.Elif(mnib == 15):
                        m.d.sync += ml_left.eq(15)
                        m.next = "MEXT"
                    with m.Elif(out_count + mnib + MIN_MATCH > raw):
                        bad()
                    with m.Else():
                        m.d.sync += ml_left.eq(mnib + MIN_MATCH)
                        m.next = "PRIME"
            with m.State("MEXT"):
                new_ml = Signal(13)
                m.d.comb += [self.sink.ready.eq(1), new_ml.eq(ml_left + d)]
                with m.If(beat):
                    m.d.sync += ml_left.eq(new_ml)
                    with m.If(in_last | (out_count + new_ml + MIN_MATCH > raw)):
                        bad()
                    with m.Elif(d != 255):
                        m.d.sync += ml_left.eq(new_ml + MIN_MATCH)
                        m.next = "PRIME"
            with m.State("PRIME"):
                m.d.comb += [rport.en.eq(1), read_addr.eq(wp - off[:wbits])]
                m.d.sync += first.eq(1)
                m.next = "COPY"
            with m.State("COPY"):
                x = Signal(8)
                m.d.comb += x.eq(Mux(first | (off != 1), rport.data, prev_x))
                with m.If(room):
                    m.d.comb += [push.eq(1), push_data.eq(x),
                                 rport.en.eq(1), read_addr.eq(wp + 1 - off[:wbits])]
                    m.d.sync += [out_count.eq(out_count + 1), ml_left.eq(ml_left - 1),
                                 prev_x.eq(x), first.eq(0)]
                    with m.If(ml_left == 1):
                        m.next = "TOKEN"
            with m.State("END"):
                with m.If(out_count != raw):
                    m.d.sync += err.eq(1)
                    m.next = "DONE"
                with m.Elif(src_free):
                    m.d.comb += finish.eq(1)
                    m.next = "DONE"
            with m.State("DRAIN"):
                m.d.comb += [self.sink.ready.eq(1), drop.eq(1)]
                with m.If(beat & in_last):
                    m.next = "DONE"
            with m.State("DONE"):
                # wait for the last byte to leave the source register, then strobe
                m.d.comb += drop.eq(err)
                with m.If(src_free):
                    m.d.comb += self.done.eq(1)
                    m.d.sync += [out_count.eq(0), blk_first.eq(1)]
                    m.next = "TOKEN"
        return m


register_codec(ID_LZ4, "lz4", decompressor=LZ4Decompressor)
