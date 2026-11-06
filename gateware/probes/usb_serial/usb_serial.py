#!/usr/bin/env python3
#
# USB serial terminal on the AUX port, at high speed.
# SPDX-License-Identifier: BSD-3-Clause

"""LUNA `USBSerialDevice` (CDC-ACM) on AUX, looping received bytes straight back.

- Host sees a tty; CONTROL stays with Apollo.
- `max_packet_size=512`: LUNA's default 64 is the FS limit and runs HS at ~1/8 rate.
- LUNA #276 (FS cap) applies only to UTMI PHYs; ULPI sets `always_fs = False`.
- Loopback is combinational (`rx.ready = tx.ready`), as measured in docs/usb-performance.md.
- Host side: `scripts/usb/usb_baseline.py`.
"""

import sys
from pathlib import Path

_GW = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(_GW))
sys.path.insert(0, str(_GW / "probes"))

import usb_ids  # noqa: E402
from amaranth import Cat, Elaboratable, Module, Signal  # noqa: E402
from jtag_registers import JTAGRegisterInterface  # noqa: E402
from luna.gateware.usb.devices.acm import USBSerialDevice  # noqa: E402
from usb_domains import OscDomains  # noqa: E402

MAX_PACKET_SIZE = 512
USB_VENDOR_ID = usb_ids.VENDOR_ID
USB_PRODUCT_ID = usb_ids.product_id("usb_serial")

APPLET_ID = 0x55534244         # "USBD"
REGISTER_ID = 1
REGISTER_STATUS = 2            # [15:0] rx bytes, [31:16] tx bytes (wrapping)


class USBSerialTerminal(Elaboratable):
    def elaborate(self, platform):
        m = Module()
        m.submodules.clocks = clocks = OscDomains()
        m.domains += clocks.domains

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        serial = USBSerialDevice(
            bus=platform.request("aux_phy", 0),
            idVendor=USB_VENDOR_ID,
            idProduct=USB_PRODUCT_ID,
            manufacturer_string="Great Scott Gadgets",
            product_string=usb_ids.product_string("usb_serial"),
            max_packet_size=MAX_PACKET_SIZE)
        m.submodules.serial = serial
        m.d.comb += serial.connect.eq(1)

        m.d.comb += [
            serial.tx.payload.eq(serial.rx.payload),
            serial.tx.valid.eq(serial.rx.valid),
            serial.tx.first.eq(serial.rx.first),
            serial.tx.last.eq(serial.rx.last),
            serial.rx.ready.eq(serial.tx.ready),
        ]

        rx_count = Signal(16)
        tx_count = Signal(16)
        with m.If(serial.rx.valid & serial.rx.ready):
            m.d.usb += rx_count.eq(rx_count + 1)
        with m.If(serial.tx.valid & serial.tx.ready):
            m.d.usb += tx_count.eq(tx_count + 1)
        registers.add_read_only_register(REGISTER_STATUS, read=Cat(rx_count, tx_count))
        return m
