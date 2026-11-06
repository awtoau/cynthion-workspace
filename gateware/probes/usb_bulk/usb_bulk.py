#!/usr/bin/env python3
#
# Raw bulk-endpoint loopback on the AUX port, at high speed.
# SPDX-License-Identifier: BSD-3-Clause

"""Vendor-class bulk IN/OUT loopback on AUX: the control for the CDC-ACM loopback.

- Held identical to `usb_serial.py`: AUX ULPI PHY, 60 MHz `usb`, 512-byte packets,
  LUNA `USBStreamInEndpoint` / `USBStreamOutEndpoint`.
- `buffered=True`: a 2-packet FIFO between rx and tx. `False`: rx wired to tx, as CDC does.
- Combinational loopback couples `rx.ready` to `tx.ready`, so a packet awaiting ACK
  NAKs the next OUT; the FIFO removes that (docs/usb-performance.md).
- Host side: `scripts/usb/usb_baseline.py`.
"""

import sys
from pathlib import Path

_GW = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_GW))
sys.path.insert(0, str(_GW / "probes"))

import usb_ids  # noqa: E402
from amaranth import Cat, DomainRenamer, Elaboratable, Module, Signal  # noqa: E402
from amaranth.lib.fifo import SyncFIFOBuffered  # noqa: E402
from jtag_registers import JTAGRegisterInterface  # noqa: E402
from luna.usb2 import USBDevice, USBStreamInEndpoint, USBStreamOutEndpoint  # noqa: E402
from usb_domains import OscDomains  # noqa: E402
from usb_protocol.emitters import DeviceDescriptorCollection  # noqa: E402

MAX_PACKET_SIZE = 512          # HS bulk maximum; LUNA defaults to the FS 64
USB_VENDOR_ID = usb_ids.VENDOR_ID
USB_PRODUCT_ID = usb_ids.product_id("usb_bulk")
BULK_ENDPOINT_NUMBER = 1

APPLET_ID = 0x55534242         # "USBB"
REGISTER_ID = 1
REGISTER_STATUS = 2            # [15:0] rx bytes, [31:16] tx bytes (wrapping)
REGISTER_MODE = 3              # 1 = FIFO, 0 = combinational


class USBBulkLoopback(Elaboratable):
    def __init__(self, buffered=True, fifo_depth=None):
        self.buffered = buffered
        # Two packets: one received while the previous awaits its ACK.
        self.fifo_depth = fifo_depth or (MAX_PACKET_SIZE * 2)

    def create_descriptors(self):
        descriptors = DeviceDescriptorCollection()
        with descriptors.DeviceDescriptor() as d:
            d.idVendor = USB_VENDOR_ID
            d.idProduct = USB_PRODUCT_ID
            d.iManufacturer = "Great Scott Gadgets"
            d.iProduct = usb_ids.product_string("usb_bulk")
            d.iSerialNumber = "bulk-fifo" if self.buffered else "bulk-comb"
            d.bNumConfigurations = 1
        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                i.bInterfaceClass = 0xFF      # vendor: no kernel driver claims it
                i.bInterfaceSubclass = 0x00
                i.bInterfaceProtocol = 0x00
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x80 | BULK_ENDPOINT_NUMBER
                    e.wMaxPacketSize = MAX_PACKET_SIZE
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = BULK_ENDPOINT_NUMBER
                    e.wMaxPacketSize = MAX_PACKET_SIZE
        return descriptors

    def elaborate(self, platform):
        m = Module()
        m.submodules.clocks = clocks = OscDomains()
        m.domains += clocks.domains

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)
        registers.add_read_only_register(REGISTER_MODE, read=int(self.buffered))

        m.submodules.usb = usb = USBDevice(bus=platform.request("aux_phy", 0))
        usb.add_standard_control_endpoint(self.create_descriptors())
        rx_ep = USBStreamOutEndpoint(endpoint_number=BULK_ENDPOINT_NUMBER,
                                     max_packet_size=MAX_PACKET_SIZE)
        tx_ep = USBStreamInEndpoint(endpoint_number=BULK_ENDPOINT_NUMBER,
                                    max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(rx_ep)
        usb.add_endpoint(tx_ep)
        rx, tx = rx_ep.stream, tx_ep.stream

        if self.buffered:
            m.submodules.fifo = fifo = DomainRenamer("usb")(
                SyncFIFOBuffered(width=8, depth=self.fifo_depth))
            m.d.comb += [
                fifo.w_data.eq(rx.payload),
                fifo.w_en.eq(rx.valid & fifo.w_rdy),
                rx.ready.eq(fifo.w_rdy),
                tx.payload.eq(fifo.r_data),
                tx.valid.eq(fifo.r_rdy),
                fifo.r_en.eq(tx.ready),
                # `last` never asserted: only max-length packets, no mid-stream ZLP.
                tx.first.eq(0),
                tx.last.eq(0),
            ]
        else:
            m.d.comb += [
                tx.payload.eq(rx.payload),
                tx.valid.eq(rx.valid),
                tx.first.eq(rx.first),
                tx.last.eq(rx.last),
                rx.ready.eq(tx.ready),
            ]

        m.d.comb += usb.connect.eq(1)

        rx_count = Signal(16)
        tx_count = Signal(16)
        with m.If(rx.valid & rx.ready):
            m.d.usb += rx_count.eq(rx_count + 1)
        with m.If(tx.valid & tx.ready):
            m.d.usb += tx_count.eq(tx_count + 1)
        registers.add_read_only_register(REGISTER_STATUS, read=Cat(rx_count, tx_count))
        return m
