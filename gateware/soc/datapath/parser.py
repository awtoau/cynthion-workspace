#
# Data endpoint OUT stream -> descriptor queue entry + payload stream.
# SPDX-License-Identifier: BSD-3-Clause

"""`HeaderParser`: splits the PC's byte stream into headers and payload.

- Reads 8 header bytes (`header.py`), pushes one descriptor, then forwards LENGTH
  payload bytes for an OUT block (none for an IN request).
- LENGTH is authoritative: USB transfer boundaries (`last`) are ignored inside a block.
- Bad MAGIC or LENGTH > MAX_LEN: bytes are dropped through the next `last` (end of
  the USB transfer), then one descriptor with `err` set is pushed. That is the resync.
- Descriptor: `flags` `tag` `aux` (header bytes 1-3), `length`, `err` [0] magic [1] length.
"""

from amaranth             import Array, Cat, Module, Signal
from amaranth.lib         import wiring
from amaranth.lib.wiring  import In, Out

from capture.codec        import byte_stream_signature
from datapath.header      import HEADER_BYTES, LEN_BITS, MAGIC, MAX_LEN

__all__ = ["HeaderParser", "DESC_WIDTH", "desc_fields"]

DESC_WIDTH = 8 + 8 + 8 + LEN_BITS + 2


def desc_fields(word):
    """Slices of a packed descriptor: (flags, tag, aux, length, err)."""
    return (word[0:8], word[8:16], word[16:24], word[24:24 + LEN_BITS],
            word[24 + LEN_BITS:24 + LEN_BITS + 2])


class HeaderParser(wiring.Component):
    def __init__(self):
        super().__init__({
            "sink":       In(byte_stream_signature()),
            "payload":    Out(byte_stream_signature()),
            "desc":       Out(DESC_WIDTH),
            "desc_valid": Out(1),
            "desc_ready": In(1),
        })

    def elaborate(self, platform):
        m = Module()
        s = self.sink
        hdr = Array(Signal(8, name=f"h{i}") for i in range(HEADER_BYTES))
        idx = Signal(range(HEADER_BYTES))
        err = Signal(2)
        ended = Signal()            # the header's last byte also ended the transfer
        left = Signal(LEN_BITS)
        length = Signal(24)
        m.d.comb += length.eq(Cat(hdr[5], hdr[6], hdr[7]))
        beat = s.valid & s.ready
        is_out = ~hdr[1][4]

        m.d.comb += [self.desc.eq(Cat(hdr[1], hdr[2], hdr[3], length[:LEN_BITS], err)),
                     self.payload.data.eq(s.data), self.payload.last.eq(left == 1)]

        with m.FSM():
            with m.State("HDR"):
                m.d.comb += s.ready.eq(1)
                with m.If(beat):
                    m.d.sync += [hdr[idx].eq(s.data), idx.eq(idx + 1)]
                    with m.If((idx == 0) & (s.data != MAGIC)):
                        m.d.sync += [err.eq(0b01), idx.eq(0)]
                        for i in range(1, HEADER_BYTES):
                            m.d.sync += hdr[i].eq(0)
                        m.next = "DISCARD"
                        with m.If(s.last):
                            m.next = "PUSH"
                    with m.Elif(idx == HEADER_BYTES - 1):
                        m.d.sync += [idx.eq(0), ended.eq(s.last)]
                        m.next = "CHECK"
            with m.State("CHECK"):
                with m.If(length > MAX_LEN):
                    m.d.sync += err.eq(0b10)
                    m.next = "DISCARD"
                    with m.If(ended):
                        m.next = "PUSH"
                with m.Else():
                    m.d.sync += err.eq(0)
                    m.next = "PUSH"
            with m.State("DISCARD"):
                m.d.comb += s.ready.eq(1)
                with m.If(beat & s.last):
                    m.next = "PUSH"
            with m.State("PUSH"):
                m.d.comb += self.desc_valid.eq(1)
                with m.If(self.desc_ready):
                    m.d.sync += left.eq(length[:LEN_BITS])
                    with m.If((err == 0) & is_out & (length != 0)):
                        m.next = "PAYLOAD"
                    with m.Else():
                        m.next = "HDR"
            with m.State("PAYLOAD"):
                m.d.comb += [self.payload.valid.eq(s.valid), s.ready.eq(self.payload.ready)]
                with m.If(beat):
                    m.d.sync += left.eq(left - 1)
                    with m.If(left == 1):
                        m.next = "HDR"
        return m
