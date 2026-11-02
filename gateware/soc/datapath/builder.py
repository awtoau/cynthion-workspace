#
# Result descriptor (from the RV) + IN payload -> one block on the data endpoint IN.
# SPDX-License-Identifier: BSD-3-Clause

"""`BlockBuilder`: emits MAGIC + the 7 header bytes the RV wrote, then LENGTH payload
bytes when FLAGS.dir is IN, with `last` on the final byte (the endpoint ends the USB
transfer there, adding a ZLP when that byte fills a packet). `sent` strobes at the end.
"""

from amaranth             import Array, Module, Signal
from amaranth.lib         import wiring
from amaranth.lib.wiring  import In, Out

from capture.codec        import byte_stream_signature
from datapath.header      import HEADER_BYTES, LEN_BITS, MAGIC

__all__ = ["BlockBuilder"]


class BlockBuilder(wiring.Component):
    def __init__(self):
        super().__init__({
            "flags": In(8), "tag": In(8), "aux": In(8), "status": In(8),
            "length": In(LEN_BITS + 1),
            "send": In(1), "busy": Out(1), "sent": Out(1),
            "payload": In(byte_stream_signature()),
            "source":  Out(byte_stream_signature()),
        })

    def elaborate(self, platform):
        m = Module()
        n = self.length
        with_payload = self.flags[4] & (n != 0)
        hdr = Array([MAGIC, self.flags, self.tag, self.aux, self.status,
                     n[0:8], n[8:16], n[16:]])
        idx = Signal(range(HEADER_BYTES))
        left = Signal(LEN_BITS + 1)
        src, pay = self.source, self.payload

        with m.FSM():
            with m.State("IDLE"):
                with m.If(self.send):
                    m.d.sync += idx.eq(0)
                    m.next = "HDR"
            with m.State("HDR"):
                m.d.comb += [self.busy.eq(1), src.valid.eq(1), src.data.eq(hdr[idx]),
                             src.last.eq((idx == HEADER_BYTES - 1) & ~with_payload)]
                with m.If(src.ready):
                    m.d.sync += idx.eq(idx + 1)
                    with m.If(idx == HEADER_BYTES - 1):
                        m.d.sync += left.eq(n)
                        m.next = "PAYLOAD"
                        with m.If(~with_payload):
                            m.next = "SENT"
            with m.State("PAYLOAD"):
                m.d.comb += [self.busy.eq(1), src.valid.eq(pay.valid), src.data.eq(pay.data),
                             src.last.eq(left == 1), pay.ready.eq(src.ready)]
                with m.If(pay.valid & src.ready):
                    m.d.sync += left.eq(left - 1)
                    with m.If(left == 1):
                        m.next = "SENT"
            with m.State("SENT"):
                m.d.comb += [self.busy.eq(1), self.sent.eq(1)]
                m.next = "IDLE"
        return m
