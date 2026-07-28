#!/usr/bin/env python3
#
# One-way USB bulk throughput: each direction measured on its own.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures each direction of a USB bulk link independently, with no loopback.

A loopback conflates two things it should not. Every byte crosses the bus twice,
so the figure pays latency in both directions; and the same endpoint buffering
serves both, so a stall in one direction throttles the other. That is how the
combinational loopback in the CDC bitstream ended up limiting throughput to
195 Mbps -- `rx.ready` was wired to `tx.ready`, so a packet awaiting ACK NAKed
the next one arriving.

This removes both confounders:

**IN (device to host).** The FPGA generates a counting sequence and streams it
continuously. Nothing is received, so nothing can back-pressure it. The host
reads and checks the sequence. This measures how fast the device can fill the
bus.

**OUT (host to device).** The host sends a counting sequence; the FPGA checks
it and discards it. Nothing is transmitted, so no buffer is shared. This
measures how fast the device can drain the bus.

The generated pattern is a free-running counter rather than constant data, so a
dropped or duplicated byte shows up as a discontinuity at a known position. A
constant fill would hide exactly the faults worth finding, and a mismatch
counter in gateware catches corruption the host might otherwise attribute to
its own buffering.

Two separate endpoints, so both directions can be exercised in one bitstream
without sharing a data path.
"""

from amaranth                          import Cat, Const, Elaboratable, Module, Signal

from luna.gateware.architecture.car    import LunaECP5DomainGenerator
from luna.gateware.interface.jtag      import JTAGRegisterInterface
from luna.gateware.usb.usb2.device     import USBDevice
from luna.gateware.usb.usb2.endpoints.stream import (USBStreamInEndpoint,
                                                     USBStreamOutEndpoint)

from usb_protocol.emitters             import DeviceDescriptorCollection


CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# 512 is the high-speed bulk maximum. Smaller would cap throughput regardless
# of anything else in the design.
MAX_PACKET_SIZE = 512

BULK_IN_ENDPOINT  = 1
BULK_OUT_ENDPOINT = 1

# Reuses Cynthion's own product ID deliberately: the udev rules grant access
# only to 615b/615c, and a fresh ID gives a permission error rather than a
# measurement. Worth a dedicated rule if this becomes permanent.
USB_VENDOR_ID  = 0x1d50
USB_PRODUCT_ID = 0x615b

APPLET_ID = 0x314f5741   # "1OWA"

REGISTER_ID       = 1
REGISTER_IN_COUNT = 2   # bytes the device has transmitted
REGISTER_OUT_COUNT = 3  # bytes the device has received
REGISTER_ERRORS   = 4   # bytes that did not match the expected sequence


class USBOneWay(Elaboratable):
    """ Streams a counting sequence in, verifies and discards a stream out. """

    def create_descriptors(self):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor  = USB_VENDOR_ID
            d.idProduct = USB_PRODUCT_ID
            d.iManufacturer = "Great Scott Gadgets"
            d.iProduct      = "Cynthion One-Way Bulk Test"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0

                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | BULK_IN_ENDPOINT
                    e.wMaxPacketSize   = MAX_PACKET_SIZE

                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = BULK_OUT_ENDPOINT
                    e.wMaxPacketSize   = MAX_PACKET_SIZE

        return descriptors

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        bus = platform.request("aux_phy", 0)
        usb = USBDevice(bus=bus)
        m.submodules.usb = usb

        descriptors = self.create_descriptors()
        usb.add_standard_control_endpoint(descriptors)
        m.d.comb += usb.connect.eq(1)

        #
        # IN: the device generates and never stops.
        #
        # `valid` is tied high, so the endpoint takes a byte whenever it can.
        # Nothing gates this on the OUT direction, which is the whole point --
        # a loopback would make transmission wait on reception.
        #
        stream_in = USBStreamInEndpoint(
            endpoint_number=BULK_IN_ENDPOINT,
            max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(stream_in)

        in_counter = Signal(8)
        in_bytes   = Signal(32)

        m.d.comb += [
            stream_in.stream.payload.eq(in_counter),
            stream_in.stream.valid.eq(1),
            # `last` stays low so the endpoint emits full 512-byte packets.
            # Asserting it per byte would terminate every packet after one
            # byte and generate a zero-length packet for each.
            stream_in.stream.last.eq(0),
        ]

        with m.If(stream_in.stream.valid & stream_in.stream.ready):
            m.d.usb += [
                in_counter.eq(in_counter + 1),
                in_bytes.eq(in_bytes + 1),
            ]

        #
        # OUT: the device consumes and discards.
        #
        # `ready` is tied high, so the host is never NAKed for want of
        # somewhere to put the data. Each byte is checked against the expected
        # sequence before being thrown away -- discarding unverified data would
        # measure throughput without establishing that it was correct.
        #
        stream_out = USBStreamOutEndpoint(
            endpoint_number=BULK_OUT_ENDPOINT,
            max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(stream_out)

        out_counter = Signal(8)
        out_bytes   = Signal(32)
        out_errors  = Signal(16)
        out_started = Signal()

        m.d.comb += stream_out.stream.ready.eq(1)

        with m.If(stream_out.stream.valid & stream_out.stream.ready):
            m.d.usb += [
                out_bytes.eq(out_bytes + 1),
                out_counter.eq(stream_out.stream.payload + 1),
                out_started.eq(1),
            ]
            # Skip the first byte: the host may begin anywhere in the sequence,
            # so there is nothing to compare it against. From the second byte
            # on, each should follow the previous.
            with m.If(out_started & (stream_out.stream.payload != out_counter)):
                m.d.usb += out_errors.eq(out_errors + 1)

        registers.add_read_only_register(REGISTER_IN_COUNT,  read=in_bytes)
        registers.add_read_only_register(REGISTER_OUT_COUNT, read=out_bytes)
        registers.add_read_only_register(
            REGISTER_ERRORS, read=Cat(out_errors, Const(0, 16)))

        return m
