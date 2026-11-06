#!/usr/bin/env python3
#
# One-way USB bulk throughput: each direction measured on its own.
# SPDX-License-Identifier: BSD-3-Clause

"""Bulk IN source and bulk OUT checker on one device, no loopback.

- IN (EP1 IN): free-running 8-bit counter, `valid` tied high; nothing back-pressures it.
- OUT (EP1 OUT): `ready` tied high; each byte checked against previous + 1, then discarded.
- A counter, not constant fill, so a dropped or duplicated byte is a discontinuity.
- PHY chosen at build time (`phy=`): moving the cable only works if the gateware drives that PHY.
- Host side: `scripts/usb/usb_baseline.py`.
"""

import sys
from pathlib import Path

_GW = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_GW))
sys.path.insert(0, str(_GW / "probes"))

import usb_ids  # noqa: E402
from amaranth import Cat, Const, Elaboratable, Module, Signal  # noqa: E402
from jtag_registers import JTAGRegisterInterface  # noqa: E402
from luna.usb2 import USBDevice, USBStreamInEndpoint, USBStreamOutEndpoint  # noqa: E402
from usb_domains import OscDomains  # noqa: E402
from usb_protocol.emitters import DeviceDescriptorCollection  # noqa: E402

MAX_PACKET_SIZE = 512
BULK_IN_ENDPOINT = 1
BULK_OUT_ENDPOINT = 1
USB_VENDOR_ID = usb_ids.VENDOR_ID
USB_PRODUCT_ID = usb_ids.product_id("usb_oneway")

APPLET_ID = 0x314f5741         # "1OWA"
REGISTER_ID = 1
REGISTER_IN_COUNT = 2          # bytes transmitted (wrapping 32-bit)
REGISTER_OUT_COUNT = 3         # bytes received (wrapping 32-bit)
REGISTER_ERRORS = 4            # [15:0] OUT bytes out of sequence (saturating)


class USBOneWay(Elaboratable):
    def __init__(self, phy="aux_phy"):
        self.phy = phy

    def create_descriptors(self):
        descriptors = DeviceDescriptorCollection()
        with descriptors.DeviceDescriptor() as d:
            d.idVendor = USB_VENDOR_ID
            d.idProduct = USB_PRODUCT_ID
            d.iManufacturer = "Great Scott Gadgets"
            d.iProduct = usb_ids.product_string("usb_oneway")
            d.iSerialNumber = self.phy
            d.bNumConfigurations = 1
        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                i.bInterfaceClass = 0xFF
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | BULK_IN_ENDPOINT
                    e.wMaxPacketSize = MAX_PACKET_SIZE
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = BULK_OUT_ENDPOINT
                    e.wMaxPacketSize = MAX_PACKET_SIZE
        return descriptors

    def elaborate(self, platform):
        m = Module()
        m.submodules.clocks = clocks = OscDomains()
        m.domains += clocks.domains

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        # AUX or TARGET; CONTROL is shared with Apollo and would need an advertiser.
        m.submodules.usb = usb = USBDevice(bus=platform.request(self.phy, 0))
        usb.add_standard_control_endpoint(self.create_descriptors())
        m.d.comb += usb.connect.eq(1)

        stream_in = USBStreamInEndpoint(endpoint_number=BULK_IN_ENDPOINT,
                                        max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(stream_in)
        in_counter = Signal(8)
        in_bytes = Signal(32)
        m.d.comb += [
            stream_in.stream.payload.eq(in_counter),
            stream_in.stream.valid.eq(1),
            stream_in.stream.last.eq(0),      # full 512-byte packets only
        ]
        with m.If(stream_in.stream.valid & stream_in.stream.ready):
            m.d.usb += [in_counter.eq(in_counter + 1), in_bytes.eq(in_bytes + 1)]

        stream_out = USBStreamOutEndpoint(endpoint_number=BULK_OUT_ENDPOINT,
                                          max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(stream_out)
        out_expect = Signal(8)
        out_bytes = Signal(32)
        out_errors = Signal(16)
        out_started = Signal()
        m.d.comb += stream_out.stream.ready.eq(1)
        with m.If(stream_out.stream.valid):
            m.d.usb += [
                out_bytes.eq(out_bytes + 1),
                out_expect.eq(stream_out.stream.payload + 1),
                out_started.eq(1),
            ]
            # First byte has no predecessor: the host may start anywhere in the sequence.
            with m.If(out_started & (stream_out.stream.payload != out_expect)
                      & (out_errors != 0xFFFF)):
                m.d.usb += out_errors.eq(out_errors + 1)

        registers.add_read_only_register(REGISTER_IN_COUNT, read=in_bytes)
        registers.add_read_only_register(REGISTER_OUT_COUNT, read=out_bytes)
        registers.add_read_only_register(REGISTER_ERRORS, read=Cat(out_errors, Const(0, 16)))
        return m
