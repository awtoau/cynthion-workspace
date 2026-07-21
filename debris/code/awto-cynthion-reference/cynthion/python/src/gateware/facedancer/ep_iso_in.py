#
# This file is part of Cynthion.
#
# Copyright (c) 2024-2025 Great Scott Gadgets <info@greatscottgadgets.com>
# SPDX-License-Identifier: BSD-3-Clause

"""CSR-accessible isochronous IN endpoint for the moondancer SoC.

RISC-V firmware protocol per USB frame:
  1. Write payload bytes to DATA.
  2. Write byte count to BYTES_IN_FRAME (arms the frame transmit).
  3. Hardware streams the FIFO to the host on each IN token in that frame.
  4. STATUS.frame_pending asserts on next SOF — repeat from step 1.

If firmware does not arm before the next SOF, the hardware sends a ZLP.

NOTE: this peripheral handles a single fixed endpoint number (set at
construction). The eptri ep_in peripheral must not prime the same endpoint
number while this peripheral is active, otherwise both will contend for the
same IN token. Firmware should stall the eptri ep_in side for isochronous
endpoints via stall_endpoint_in().
"""
from typing import Annotated

from amaranth              import *
from amaranth.hdl.xfrm    import DomainRenamer
from amaranth.lib          import wiring
from amaranth.lib.fifo     import SyncFIFOBuffered
from amaranth.lib.wiring   import In, Out, connect, flipped
from amaranth_soc          import csr, event

from luna.gateware.usb.usb2.endpoint                        import EndpointInterface
from luna.gateware.usb.usb2.endpoints.isochronous_stream_in import USBIsochronousStreamInEndpoint


class Peripheral(wiring.Component):
    """Isochronous IN endpoint with a FIFO-backed CSR interface.

    Parameters
    ----------
    endpoint_number : int
        Fixed USB endpoint number this peripheral responds to.
    max_packet_size : int
        Maximum bytes per micro-frame.  Should match the endpoint descriptor.
    fifo_depth : int
        Payload FIFO depth in bytes.  Recommended: at least 2 × max_packet_size.
    """

    class BytesInFrame(csr.Register, access="w"):
        """Write the number of payload bytes for the next frame before SOF."""
        count : csr.Field(csr.action.W, unsigned(11))  # 0..1023

    class Status(csr.Register, access="r"):
        """frame_pending: SOF arrived, firmware should refill FIFO and re-arm.
        overflow: DATA was written while FIFO was full; bytes were dropped."""
        frame_pending : csr.Field(csr.action.R, unsigned(1))
        overflow      : csr.Field(csr.action.R, unsigned(1))

    class Reset(csr.Register, access="w"):
        """Write 1 to clear the payload FIFO and reset frame state."""
        fifo : csr.Field(csr.action.W, unsigned(1))

    class Data(csr.Register, access="w"):
        """Payload byte FIFO.  Write one byte per access."""
        byte : csr.Field(csr.action.W, unsigned(8))

    def __init__(self, endpoint_number, max_packet_size=1024, fifo_depth=2048):
        self._endpoint_number = endpoint_number
        self._max_packet_size = max_packet_size
        self._fifo_depth      = fifo_depth

        # Create the LUNA isochronous endpoint; share its EndpointInterface
        # directly so USBDevice.add_endpoint() picks it up from self.interface.
        self._iso_ep = USBIsochronousStreamInEndpoint(
            endpoint_number=endpoint_number,
            max_packet_size=max_packet_size,
        )
        self.interface = self._iso_ep.interface

        regs = csr.Builder(addr_width=4, data_width=8)
        self._bytes_in_frame = regs.add("bytes_in_frame", self.BytesInFrame())
        self._status         = regs.add("status",         self.Status())
        self._reset          = regs.add("reset",          self.Reset())
        self._data           = regs.add("data",           self.Data())
        self._bridge         = csr.Bridge(regs.as_memory_map())

        EventSource = Annotated[event.Source, "Asserts when the host issues an IN token for this endpoint (start of frame data request)."]
        self._frame_irq = EventSource(trigger="rise", path=("frame",))
        ev_map = event.EventMap()
        ev_map.add(self._frame_irq)
        self._events = csr.event.EventMonitor(ev_map, data_width=8)

        self._csr_decoder = csr.Decoder(addr_width=5, data_width=8)
        self._csr_decoder.add(self._bridge.bus)
        self._csr_decoder.add(self._events.bus, name="ev")

        super().__init__({
            "bus": Out(self._csr_decoder.bus.signature),
            "irq": Out(unsigned(1)),
        })
        self.bus.memory_map = self._csr_decoder.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules += [self._bridge, self._events, self._csr_decoder]
        m.submodules.iso_ep = self._iso_ep

        connect(m, flipped(self.bus), self._csr_decoder.bus)

        # Payload FIFO: written from CSR DATA register, drained by iso endpoint.
        m.submodules.fifo = fifo = SyncFIFOBuffered(width=8, depth=self._fifo_depth)
        m.d.comb += [
            fifo.w_en   .eq(self._data.f.byte.w_stb & fifo.w_rdy),
            fifo.w_data .eq(self._data.f.byte.w_data),
        ]

        # Overflow: DATA written while FIFO was full.
        overflow = Signal()
        with m.If(self._data.f.byte.w_stb & ~fifo.w_rdy):
            m.d.sync += overflow.eq(1)
        with m.If(self._reset.f.fifo.w_stb):
            m.d.sync += overflow.eq(0)

        # bytes_in_frame: firmware writes this to arm each frame.
        # Cleared to 0 on reset; held between frames so a missed re-arm sends
        # the same count (and whatever data is in the FIFO) rather than a ZLP.
        bytes_in_frame = Signal(11)
        frame_pending  = Signal()

        with m.If(self._bytes_in_frame.f.count.w_stb):
            m.d.sync += [
                bytes_in_frame .eq(self._bytes_in_frame.f.count.w_data),
                frame_pending  .eq(0),
            ]

        # new_frame asserts once per USB SOF; firmware should see frame_pending
        # and refill within one frame window.
        with m.If(self._iso_ep.interface.tokenizer.new_frame):
            m.d.sync += frame_pending.eq(1)

        with m.If(self._reset.f.fifo.w_stb):
            m.d.sync += [bytes_in_frame.eq(0), frame_pending.eq(0)]

        m.d.comb += [
            self._iso_ep.bytes_in_frame          .eq(bytes_in_frame),
            self._status.f.frame_pending.r_data  .eq(frame_pending),
            self._status.f.overflow.r_data       .eq(overflow),
        ]

        # Wire FIFO read port → isochronous stream input.
        m.d.comb += [
            self._iso_ep.stream.payload .eq(fifo.r_data),
            self._iso_ep.stream.valid   .eq(fifo.r_rdy),
            fifo.r_en                   .eq(self._iso_ep.stream.valid & self._iso_ep.stream.ready),
        ]

        # Interrupt when the endpoint requests frame data (host issued IN token).
        m.d.comb += [
            self._frame_irq.i .eq(self._iso_ep.data_requested),
            self.irq          .eq(self._events.src.i),
        ]

        # Run all sync registers in the "usb" clock domain, same as ep_in.
        return DomainRenamer({"sync": "usb"})(m)
