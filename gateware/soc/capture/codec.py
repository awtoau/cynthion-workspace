#!/usr/bin/env python3
#
# Pluggable block codec interface for the capture path, plus the `stored` codec.
# SPDX-License-Identifier: BSD-3-Clause

"""Streaming block-codec interface and registry. Design: `docs/capture-compression.md`.

## Interface (one block = one `last`-terminated byte stream)

Compressor (`CompressorSignature`):
  - `sink`   raw bytes in (`data`, `valid`, `ready`, `last`); `last` marks the final byte.
  - `source` coded bytes out, same shape.
  - `block_limit` in: max raw bytes per block (1..BLOCK_BYTES). Reaching it ends the
    block as if `last` were set; the next byte starts a new block.
  - `codec_id`, `comp_len`, `raw_len` out: valid during the beat that carries
    `source.last` (the consumer samples them there). `comp_len` is the coded length.

Decompressor (`DecompressorSignature`):
  - `sink`   coded bytes in; `last` marks the final coded byte.
  - `source` raw bytes out; `last` on the final raw byte of a good block.
  - `codec_id`, `raw_len` in: from the block's index entry; held for the whole block.
  - `error` out: a corrupt block was seen; sticky until the next block's first byte.
    On error no further raw byte is emitted, `source.last` is not asserted, and the
    block is drained to its `last`.
  - `done` out: one-cycle strobe when the block has ended (good or bad).

Registry: `CODECS[id] = Codec(name, compressor, decompressor)`; ids: 0 stored, 1 lz4,
2..15 reserved. `SwitchableCompressor` / `SwitchableDecompressor` pick by id at block
boundaries.
"""

from dataclasses import dataclass

from amaranth            import Array, Module, Mux, Signal
from amaranth.lib        import wiring
from amaranth.lib.wiring import In, Out

__all__ = ["BLOCK_BYTES", "CODEC_ID_BITS", "LEN_BITS", "ID_STORED", "ID_LZ4",
           "CODECS", "Codec", "register_codec", "byte_stream_signature",
           "CompressorSignature", "DecompressorSignature", "StoredCompressor",
           "StoredDecompressor", "SwitchableCompressor", "SwitchableDecompressor"]

BLOCK_BYTES   = 1024
CODEC_ID_BITS = 4
LEN_BITS      = 12     # coded length may exceed 1024 inside lz4 (then the block is stored)
ID_STORED     = 0
ID_LZ4        = 1


def byte_stream_signature():
    return wiring.Signature({
        "data":  Out(8),
        "valid": Out(1),
        "last":  Out(1),
        "ready": In(1),
    })


def _compressor_members():
    return {
        "sink":        In(byte_stream_signature()),
        "source":      Out(byte_stream_signature()),
        "block_limit": In(LEN_BITS, init=BLOCK_BYTES),
        "codec_id":    Out(CODEC_ID_BITS),
        "comp_len":    Out(LEN_BITS),
        "raw_len":     Out(LEN_BITS),
    }


class CompressorSignature(wiring.Signature):
    def __init__(self, extra=None):
        super().__init__({**_compressor_members(), **(extra or {})})


class DecompressorSignature(wiring.Signature):
    def __init__(self):
        super().__init__({
            "sink":     In(byte_stream_signature()),
            "source":   Out(byte_stream_signature()),
            "codec_id": In(CODEC_ID_BITS),
            "raw_len":  In(LEN_BITS),
            "error":    Out(1),
            "done":     Out(1),
        })


@dataclass(frozen=True)
class Codec:
    name:         str
    compressor:   type
    decompressor: type


CODECS: dict = {}


def register_codec(codec_id, name, compressor=None, decompressor=None):
    """Register (or complete) codec `codec_id`; compressor and decompressor may arrive separately."""
    assert 0 <= codec_id < (1 << CODEC_ID_BITS)
    old = CODECS.get(codec_id)
    if old is not None:
        assert old.name == name
        compressor, decompressor = compressor or old.compressor, decompressor or old.decompressor
    CODECS[codec_id] = Codec(name, compressor, decompressor)


def _limit(block_limit):
    return Mux(block_limit > BLOCK_BYTES, BLOCK_BYTES, Mux(block_limit == 0, 1, block_limit))


class StoredCompressor(wiring.Component):
    """Pass-through; `comp_len == raw_len` is the byte count, valid on the last beat."""

    def __init__(self, **_):
        super().__init__(CompressorSignature())

    def elaborate(self, platform):
        m = Module()
        count = Signal(LEN_BITS)
        end   = Signal()
        m.d.comb += [
            end.eq(self.sink.last | (count + 1 >= _limit(self.block_limit))),
            self.source.data.eq(self.sink.data),
            self.source.valid.eq(self.sink.valid),
            self.source.last.eq(end),
            self.sink.ready.eq(self.source.ready),
            self.codec_id.eq(ID_STORED),
            self.comp_len.eq(count + 1),
            self.raw_len.eq(count + 1),
        ]
        with m.If(self.sink.valid & self.sink.ready):
            m.d.sync += count.eq(Mux(end, 0, count + 1))
        return m


class StoredDecompressor(wiring.Component):
    """Pass-through with the same `raw_len` bound as the lz4 decoder."""

    def __init__(self, **_):
        super().__init__(DecompressorSignature())

    def elaborate(self, platform):
        m = Module()
        count = Signal(LEN_BITS)
        sticky = Signal()
        over  = Signal()
        beat  = self.sink.valid & self.sink.ready
        m.d.comb += [
            over.eq(count >= self.raw_len),
            self.source.data.eq(self.sink.data),
            self.source.valid.eq(self.sink.valid & ~over & ~sticky),
            self.source.last.eq(self.sink.last & (count + 1 == self.raw_len) & ~sticky),
            self.sink.ready.eq(self.source.ready | over | sticky),
            self.done.eq(beat & self.sink.last),
            self.error.eq(sticky | (beat & over)
                          | (beat & self.sink.last & (count + 1 != self.raw_len))),
        ]
        with m.If(beat):
            with m.If(self.sink.last):
                m.d.sync += [count.eq(0), sticky.eq(0)]
            with m.Else():
                m.d.sync += count.eq(count + 1)
                with m.If(over):
                    m.d.sync += sticky.eq(1)
        return m


class SwitchableCompressor(wiring.Component):
    """Compress with codec `mode`, `mode` sampled at each block's first byte.

    A block of a different codec than the previous one is accepted only when no
    block is outstanding (every output block finished), so coded blocks never reorder.
    """

    def __init__(self, codec_ids=(ID_STORED, ID_LZ4), **codec_args):
        self._ids  = tuple(codec_ids)
        self._args = codec_args
        super().__init__(CompressorSignature({"mode": In(CODEC_ID_BITS)}))

    def elaborate(self, platform):
        m = Module()
        subs = []
        for cid in self._ids:
            sub = CODECS[cid].compressor(**self._args)
            m.submodules[f"c{cid}"] = sub
            subs.append(sub)
        width = max(1, (len(subs) - 1).bit_length())
        cur, want, sel, osel = (Signal(width) for _ in range(4))
        mid         = Signal()          # a block is open on `cur`
        outstanding = Signal(4)
        gate        = Signal()
        for index, cid in enumerate(self._ids):
            with m.If(self.mode == cid):
                m.d.comb += want.eq(index)
        m.d.comb += [
            sel.eq(Mux(mid, cur, want)),
            osel.eq(Mux(mid | (outstanding != 0), cur, want)),
            gate.eq(mid | (want == cur) | (outstanding == 0)),
        ]
        for index, sub in enumerate(subs):
            m.d.comb += [
                sub.sink.data.eq(self.sink.data),
                sub.sink.last.eq(self.sink.last),
                sub.sink.valid.eq(self.sink.valid & gate & (sel == index)),
                sub.block_limit.eq(self.block_limit),
                sub.source.ready.eq(self.source.ready & (osel == index)),
            ]
        with m.Switch(osel):
            for index, sub in enumerate(subs):
                with m.Case(index):
                    m.d.comb += [
                        self.source.data.eq(sub.source.data),
                        self.source.valid.eq(sub.source.valid),
                        self.source.last.eq(sub.source.last),
                        self.codec_id.eq(sub.codec_id),
                        self.comp_len.eq(sub.comp_len),
                        self.raw_len.eq(sub.raw_len),
                    ]
        m.d.comb += self.sink.ready.eq(Array([s.sink.ready for s in subs])[sel] & gate)

        taken    = self.sink.valid & self.sink.ready
        count    = Signal(LEN_BITS)
        ends     = Signal()
        out_done = Signal()
        m.d.comb += [
            ends.eq(self.sink.last | (count + 1 >= _limit(self.block_limit))),
            out_done.eq(self.source.valid & self.source.ready & self.source.last),
        ]
        with m.If(taken):
            with m.If(~mid):
                m.d.sync += cur.eq(want)
            m.d.sync += [mid.eq(~ends), count.eq(Mux(ends, 0, count + 1))]
        closed = taken & ends
        with m.If(closed & ~out_done):
            m.d.sync += outstanding.eq(outstanding + 1)
        with m.Elif(~closed & out_done):
            m.d.sync += outstanding.eq(outstanding - 1)
        return m


class SwitchableDecompressor(wiring.Component):
    """Decompress with the codec named by `codec_id`, sampled at each block's first byte."""

    def __init__(self, codec_ids=(ID_STORED, ID_LZ4), **codec_args):
        self._ids  = tuple(codec_ids)
        self._args = codec_args
        super().__init__(DecompressorSignature())

    def elaborate(self, platform):
        m = Module()
        subs = []
        for cid in self._ids:
            sub = CODECS[cid].decompressor(**self._args)
            m.submodules[f"d{cid}"] = sub
            subs.append(sub)
        width = max(1, (len(subs) - 1).bit_length())
        cur, want, sel = (Signal(width) for _ in range(3))
        mid = Signal()
        for index, cid in enumerate(self._ids):
            with m.If(self.codec_id == cid):
                m.d.comb += want.eq(index)
        m.d.comb += sel.eq(Mux(mid, cur, want))
        for index, sub in enumerate(subs):
            m.d.comb += [
                sub.sink.data.eq(self.sink.data),
                sub.sink.last.eq(self.sink.last),
                sub.sink.valid.eq(self.sink.valid & (sel == index)),
                sub.codec_id.eq(self.codec_id),
                sub.raw_len.eq(self.raw_len),
                sub.source.ready.eq(self.source.ready & (sel == index)),
            ]
        with m.Switch(sel):
            for index, sub in enumerate(subs):
                with m.Case(index):
                    m.d.comb += [
                        self.source.data.eq(sub.source.data),
                        self.source.valid.eq(sub.source.valid),
                        self.source.last.eq(sub.source.last),
                        self.sink.ready.eq(sub.sink.ready),
                        self.error.eq(sub.error),
                        self.done.eq(sub.done),
                    ]
        with m.If(self.sink.valid & self.sink.ready):
            with m.If(~mid):
                m.d.sync += cur.eq(want)
            m.d.sync += mid.eq(~self.sink.last)
        return m


register_codec(ID_STORED, "stored", StoredCompressor, StoredDecompressor)
