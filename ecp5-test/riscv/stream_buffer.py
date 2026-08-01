#!/usr/bin/env python3
#
# Elastic buffering between a peripheral and a transport, sized per transport.
# SPDX-License-Identifier: BSD-3-Clause

"""
A stream in, the same stream out, with a chosen amount of slack and an optional
clock-domain crossing in between.

## Why this is not part of the UART

`uart16550.py` has fixed 16-byte FIFOs because the NS16550A has fixed 16-byte
FIFOs. Every byte of buffering beyond that exists to cover a property of the
*transport* -- how long the far end can be busy, how large a packet it moves,
what clock it runs on -- and none of those are properties of a register map.

They used to live in one module and quietly invalidated each other. The console
peripheral's 1024-byte FIFO was justified in a comment as "two 512-byte USB
packets: enough that the CPU can fill one while another is in flight". Later,
`serial.tx.last.eq(1)` changed the endpoint to one byte per packet, so a console
line would not sit unsent waiting for 512 characters that a console never emits.
Both decisions were right. Together they left a block RAM spent on packet
pipelining for a path that no longer pipelines packets, and nothing pointed at
the contradiction because it was inside a module about register layout.

So: buffering is declared where the transport is chosen, next to the reason.

## Sizing

The number to pick is "how many bytes may the CPU produce while the far end is
not accepting any". For the USB CDC console with one byte per packet that is
roughly one USB frame's worth of stall, which is a handful of bytes -- the CPU
writes a byte, the endpoint takes it, the host polls. Small.

The reason to care on this board: 16 entries of 8 bits map to distributed LUT
RAM (TRELLIS_DPR16X4) on the ECP5, while 1024 entries of 8 bits map to a block
RAM (DP16KD). The design uses 44 of the 56 DP16KD this die has, and the CPU's
caches, the block RAM the firmware runs from, the USB endpoint buffers and the
flash ILA all want them. A buffer sized by habit rather than by measurement is
paid for out of that budget.

Overflow is dropped at the *producer* side by backpressure: `sink.ready` goes low
and the 16550 stops accepting from its own FIFO, so the CPU sees THRE clear and
waits. Nothing is silently lost as long as the producer honours ready.
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

        m.d.comb += [
            fifo.w_data.eq(self.sink.payload),
            # The FIFO gates this with `w_rdy` internally, so a producer that
            # ignores backpressure loses the byte rather than corrupting the
            # pointers. `sink.ready` is the honest answer to give it anyway.
            fifo.w_en.eq(self.sink.valid),
            self.sink.ready.eq(fifo.w_rdy),

            self.source.payload.eq(fifo.r_data),
            self.source.valid.eq(fifo.r_rdy),
            fifo.r_en.eq(self.source.ready),
        ]

        return m
