#!/usr/bin/env python3
#
# Elastic buffering between a peripheral and a transport, sized per transport.
# SPDX-License-Identifier: BSD-3-Clause

"""
A stream in, the same stream out, with a chosen amount of slack and an optional
clock-domain crossing in between.

Buffering lives HERE, next to where the transport is chosen -- not in
`uart16550.py`, whose 16-byte FIFOs are 16 bytes because the NS16550A's are.
Every byte beyond that covers a property of the transport (how long the far end
can be busy, its packet size, its clock), and none of those are properties of a
register map.

## Sizing

The question is "how many bytes may the CPU produce while the far end accepts
none".

  * USB CDC console, one byte per packet: roughly one frame's worth of stall --
    a handful of bytes.
  * Do not guess large. On the ECP5, 16 entries of 8 bits are distributed LUT
    RAM (TRELLIS_DPR16X4); 1024 entries are a block RAM (DP16KD). This design
    uses 42 of the die's 56 DP16KD, and the caches, firmware BRAM, USB endpoint
    buffers and flash ILA all compete for the rest.

## Overflow

Dropped at the PRODUCER by backpressure: `sink.ready` goes low, the 16550 stops
accepting, the CPU sees THRE clear and waits. Nothing is lost as long as the
producer honours `ready`.

Why elastic buffering at the transport rather than a deep FIFO in the UART:
`../../docs/decisions.md`.
"""

from amaranth               import Module
from amaranth.lib           import wiring, stream
from amaranth.lib.fifo      import AsyncFIFOBuffered, SyncFIFOBuffered
from amaranth.lib.wiring    import In, Out


__all__ = ["StreamBuffer"]


class StreamBuffer(wiring.Component):
    """`depth` bytes of slack between a stream source and a stream sink.

    Parameters
    ----------
    depth : int
        Entries. Must be a power of two when the domains differ, because a
        gray-coded pointer is only monotonic across a power-of-two wrap.
    width : int
        Payload bits. 8 for every current user; a parameter so this is not a
        byte-only module by accident.
    i_domain, o_domain : str
        The domains the sink and source sit in. When they are the same this is a
        plain synchronous FIFO; when they differ it is an asynchronous one.

    A crossing is not optional when the domains genuinely differ, and getting it
    wrong is quiet. A `SyncFIFOBuffered` between `sync` at 80 MHz and `usb` at 60
    worked perfectly while both were 60 MHz, then produced a stream with correct
    counter VALUES and dropped CHARACTERS -- `tic 00000`, `tck 000001` -- because
    bytes vanished in transit while the arithmetic that produced them was
    untouched. That is the signature of an unsynchronised crossing: ungray-coded
    pointers and ready flags sampled in the wrong domain. Passing the two domain
    names explicitly, rather than defaulting both to `sync`, is what makes that
    choice visible at the instantiation site.
    """

    def __init__(self, *, depth, width=8, i_domain="sync", o_domain="sync"):
        self._depth    = depth
        self._width    = width
        self._i_domain = i_domain
        self._o_domain = o_domain
        super().__init__({
            "sink":   In(stream.Signature(width)),
            "source": Out(stream.Signature(width)),
        })

    @property
    def depth(self):
        return self._depth

    def elaborate(self, platform):
        m = Module()

        if self._i_domain == self._o_domain:
            fifo = SyncFIFOBuffered(width=self._width, depth=self._depth)
            if self._i_domain != "sync":
                from amaranth import DomainRenamer
                fifo = DomainRenamer(self._i_domain)(fifo)
        else:
            fifo = AsyncFIFOBuffered(width=self._width, depth=self._depth,
                                     w_domain=self._i_domain,
                                     r_domain=self._o_domain)
        m.submodules.fifo = fifo

        # `w_stream` and `r_stream` are `amaranth.lib.fifo`'s own stream views of
        # the FIFO's ports -- `w_data`/`w_en`/`w_rdy` presented as
        # payload/valid/ready, and the read side likewise. They alias the same
        # signals rather than adding any, so this produces a byte-identical
        # netlist to the twelve lines of `m.d.comb` it replaces; what it buys is
        # that `wiring.connect` checks the two signatures agree, where a hand
        # assignment of `payload` to a differently-sized `w_data` would silently
        # truncate.
        #
        # The FIFO gates `w_en` with `w_rdy` internally, so a producer that
        # ignores backpressure loses the byte rather than corrupting the
        # pointers -- and `sink.ready` is the honest answer to give it anyway,
        # which is what `w_stream.ready` is.
        wiring.connect(m, wiring.flipped(self.sink), fifo.w_stream)
        wiring.connect(m, fifo.r_stream, wiring.flipped(self.source))

        return m
