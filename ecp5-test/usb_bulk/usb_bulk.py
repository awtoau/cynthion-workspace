#!/usr/bin/env python3
#
# Raw bulk-endpoint loopback on the AUX port, at high speed.
# SPDX-License-Identifier: BSD-3-Clause

"""
A raw bulk IN/OUT loopback, as a control against the CDC-ACM measurement.

This exists to answer one question: how much of the CDC-ACM loopback's
throughput shortfall is CDC's fault, and how much is the loopback's?

To make that a fair test, everything that could confound it is held identical
to `ecp5-test/usb_serial/usb_serial.py` -- same AUX ULPI PHY, same 60 MHz
domains, same 512-byte max packet size, same `USBStreamInEndpoint` and
`USBStreamOutEndpoint` classes underneath. CDC-ACM in LUNA is *already* those
two endpoints plus descriptors and a SET_LINE_CODING handler; it adds no data
path of its own. So the only differences here are:

  1. no CDC descriptors, so the host binds a bulk interface rather than a tty,
     and no cdc-acm driver, line discipline, or termios sits in the path; and
  2. a FIFO between rx and tx, rather than a combinational wire.

The FIFO is the point of interest. The CDC bitstream wires `rx.ready` straight
to `tx.ready`, which couples the two endpoints: the OUT endpoint can only
accept a byte when the IN endpoint's transmit buffer will take it, and that
buffer stops accepting while a packet is in flight awaiting ACK. Decoupling
them with a packet-sized buffer lets a packet be received while the previous
one is still being acknowledged.

`bulk_only` selects between the two, so the same bitstream can isolate either
variable rather than changing both at once and learning nothing:

    USBBulkLoopback()                  # FIFO -- decoupled
    USBBulkLoopback(buffered=False)    # combinational -- matches CDC exactly

Build and flash:

    source "$HOME/opt/oss-cad-suite/environment"
    python3.15t -c "
    import sys; sys.path.insert(0,'ecp5-test')
    from usb_bulk.usb_bulk import USBBulkLoopback
    from cynthion_platform.cynthion_r1_4 import CynthionPlatformRev1D4
    CynthionPlatformRev1D4().build(USBBulkLoopback(), do_program=False,
                                   build_dir='ecp5-test/usb_bulk/build')"
    apollo configure ecp5-test/usb_bulk/build/top.bit
"""

import sys as _uid_sys
from pathlib import Path as _uid_Path
_uid_sys.path.insert(0, str(_uid_Path(__file__).resolve().parent.parent))
import usb_ids


from amaranth                       import (Cat, DomainRenamer, Elaboratable,
                                            Module, Signal)
from amaranth.lib.fifo              import SyncFIFOBuffered

from luna.gateware.architecture.car import LunaECP5DomainGenerator
from luna.gateware.interface.jtag   import JTAGRegisterInterface
from luna.usb2                      import (USBDevice, USBStreamInEndpoint,
                                            USBStreamOutEndpoint)

from usb_protocol.emitters          import DeviceDescriptorCollection


# Matches usb_serial.py exactly. The USB domain must be 60 MHz for the ULPI
# PHY; `sync` is held at 60 too so the loopback needs no clock-domain crossing.
CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# 512 is the high-speed bulk maximum, and matches the CDC bitstream.
MAX_PACKET_SIZE = 512

# From the central allocation in ecp5-test/usb_ids.py, never a locally chosen number.
#
# Borrowing LUNA's 0x615b makes this bitstream indistinguishable from LUNA and from
# every other bitstream that borrows it. The pull towards doing so is that libusb needs
# uaccess and the installed udev rules already grant it for that ID;
# scripts/install_udev.py grants it per allocated ID instead.
USB_VENDOR_ID  = usb_ids.VENDOR_ID
USB_PRODUCT_ID = usb_ids.product_id("usb_bulk")

BULK_ENDPOINT_NUMBER = 1

APPLET_ID = 0x55534242   # "USBB"

REGISTER_ID     = 1
REGISTER_STATUS = 2   # byte counters, as in the CDC bitstream


class USBBulkLoopback(Elaboratable):
    """ A raw bulk IN/OUT loopback on AUX.

    Parameters
    ----------
    buffered: bool
        If True (default), a packet-sized FIFO decouples the OUT endpoint from
        the IN endpoint. If False, rx is wired to tx combinationally, exactly
        as the CDC bitstream does -- which isolates CDC overhead from the cost
        of the unbuffered loopback.
    """

    def __init__(self, buffered=True, fifo_depth=None):
        self.buffered   = buffered
        # Two packets deep: enough to receive one packet while the previous is
        # still awaiting its ACK, which is precisely the pipelining the
        # combinational path cannot do.
        self.fifo_depth = fifo_depth or (MAX_PACKET_SIZE * 2)

    def create_descriptors(self):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor           = USB_VENDOR_ID
            d.idProduct          = USB_PRODUCT_ID
            d.iManufacturer      = "Great Scott Gadgets"
            d.iProduct           = usb_ids.product_string("usb_bulk")
            d.iSerialNumber      = "bulk-loopback"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                # Vendor-specific, so no kernel class driver claims it and the
                # host can talk to it directly with libusb.
                i.bInterfaceClass    = 0xFF
                i.bInterfaceSubclass = 0x00
                i.bInterfaceProtocol = 0x00

                # Bulk IN to host (tx, from our side)
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | BULK_ENDPOINT_NUMBER
                    e.wMaxPacketSize   = MAX_PACKET_SIZE

                # Bulk OUT from host (rx, from our side)
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = BULK_ENDPOINT_NUMBER
                    e.wMaxPacketSize   = MAX_PACKET_SIZE

        return descriptors

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        # AUX, as in the CDC bitstream: it belongs to the FPGA outright and
        # needs no arbitration with Apollo.
        bus = platform.request("aux_phy", 0)

        m.submodules.usb = usb = USBDevice(bus=bus)
        usb.add_standard_control_endpoint(self.create_descriptors())

        rx_ep = USBStreamOutEndpoint(
            endpoint_number=BULK_ENDPOINT_NUMBER,
            max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(rx_ep)

        tx_ep = USBStreamInEndpoint(
            endpoint_number=BULK_ENDPOINT_NUMBER,
            max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(tx_ep)

        rx = rx_ep.stream
        tx = tx_ep.stream

        if self.buffered:
            # A FIFO in the `usb` domain -- no clock-domain crossing, since the
            # endpoints and the loopback all run at 60 MHz.
            fifo = SyncFIFOBuffered(width=8, depth=self.fifo_depth)
            m.submodules.fifo = DomainRenamer("usb")(fifo)

            m.d.comb += [
                fifo.w_data .eq(rx.payload),
                fifo.w_en   .eq(rx.valid & fifo.w_rdy),
                rx.ready    .eq(fifo.w_rdy),

                tx.payload  .eq(fifo.r_data),
                tx.valid    .eq(fifo.r_rdy),
                fifo.r_en   .eq(tx.ready),

                # `last` is deliberately never asserted. The IN transfer
                # manager then emits only max-length packets and never a short
                # one, so no ZLP is injected mid-stream. Terminating packets
                # early is what makes the CDC path pay a per-packet round trip.
                tx.first    .eq(0),
                tx.last     .eq(0),
            ]
        else:
            # Identical to the CDC bitstream's loopback, for a controlled
            # comparison.
            m.d.comb += [
                tx.payload .eq(rx.payload),
                tx.valid   .eq(rx.valid),
                tx.first   .eq(rx.first),
                tx.last    .eq(rx.last),
                rx.ready   .eq(tx.ready),
            ]

        m.d.comb += usb.connect.eq(1)

        #
        # Instrumentation, matching the CDC bitstream so the two are directly
        # comparable over JTAG.
        #
        rx_count = Signal(16)
        tx_count = Signal(16)

        with m.If(rx.valid & rx.ready):
            m.d.sync += rx_count.eq(rx_count + 1)
        with m.If(tx.valid & tx.ready):
            m.d.sync += tx_count.eq(tx_count + 1)

        registers.add_read_only_register(
            REGISTER_STATUS, read=Cat(rx_count, tx_count))

        return m
