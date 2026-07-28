#!/usr/bin/env python3
#
# USB serial terminal on the AUX port, at high speed.
# SPDX-License-Identifier: BSD-3-Clause

"""
Presents Cynthion as a USB CDC-ACM serial device on the AUX port.

The host sees an ordinary tty. Nothing needs installing, no debug session is
required, and the CONTROL port stays free for Apollo -- which is the point: it
gives a way to talk to gateware, and later to a CPU, using only a cable.

**Speed.** The PHYs are ULPI (USB3343), which are high-speed capable, and
LUNA's `USBDevice` negotiates high speed by default -- `full_speed_only` is an
input signal defaulting to zero, not a build-time choice.

Worth knowing given a known LUNA limitation: upstream issue #276 reports the
speed being capped to full speed, but that applies only to custom **UTMI** PHYs,
where a hardcoded constant forces it. `USBDevice` sets `always_fs = False` when
it detects a ULPI bus, which is what Cynthion has, so the cap does not apply
here. Full speed remains available as an automatic fallback.

The one thing that does need setting is `max_packet_size`. It defaults to 64,
which is the full-speed bulk limit; high-speed bulk endpoints use 512. Leaving
it at 64 would enumerate at high speed but move data at roughly an eighth of the
achievable rate, which is the sort of quiet underperformance that is easy to
never notice.

**Why AUX.** The platform sets `apollo_port_sharing = {'control_phy':
'advertising'}`, so CONTROL is shared with Apollo and claiming it means asking
for it. AUX is already `default_usb_connection` and belongs to the FPGA, so it
needs no arbitration. TARGET stays free for testing USB itself.

The loopback below is deliberate: whatever the host sends comes straight back.
That is enough to prove enumeration, both directions of the data path, and the
host's tty plumbing, without needing anything else in the design to be working
first.
"""

from amaranth                       import Cat, Const, Elaboratable, Module, Signal

from luna.gateware.architecture.car import LunaECP5DomainGenerator
from luna.gateware.interface.jtag   import JTAGRegisterInterface
from luna.gateware.usb.devices.acm  import USBSerialDevice


# 60 MHz sync, matching the ULPI clock. The USB domain must be 60 MHz for the
# PHY regardless of what else the design does.
CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# 512 for high-speed bulk. The default of 64 is the full-speed limit and would
# quietly cap throughput at about an eighth of what the link can carry.
MAX_PACKET_SIZE = 512

# Great Scott Gadgets' vendor ID, with a product ID from the range used for
# development bitstreams rather than a shipping product.
USB_VENDOR_ID  = 0x1d50
USB_PRODUCT_ID = 0x615c

APPLET_ID = 0x55534244   # "USBD"

REGISTER_ID     = 1
REGISTER_STATUS = 2   # connection state and byte counters


class USBSerialTerminal(Elaboratable):
    """ A CDC-ACM serial device on AUX, looping received data back. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        # AUX rather than CONTROL: CONTROL is shared with Apollo and would need
        # an ApolloAdvertiser to claim, while AUX belongs to the FPGA outright.
        bus = platform.request("aux_phy", 0)

        serial = USBSerialDevice(
            bus=bus,
            idVendor=USB_VENDOR_ID,
            idProduct=USB_PRODUCT_ID,
            manufacturer_string="Great Scott Gadgets",
            product_string="Cynthion USB Serial",
            max_packet_size=MAX_PACKET_SIZE)
        m.submodules.serial = serial

        # Connect immediately. There is nothing here that needs to initialise
        # first, and a device that only appears after a host poke is harder to
        # test than one that is simply present.
        m.d.comb += serial.connect.eq(1)

        #
        # Loopback.
        #
        # Bytes received from the host are returned unchanged. This proves
        # enumeration, both directions of the data path, and the host's tty
        # plumbing, without depending on anything else in the design.
        #
        m.d.comb += [
            serial.tx.payload.eq(serial.rx.payload),
            serial.tx.valid  .eq(serial.rx.valid),
            serial.tx.first  .eq(serial.rx.first),
            serial.tx.last   .eq(serial.rx.last),
            serial.rx.ready  .eq(serial.tx.ready),
        ]

        #
        # Instrumentation.
        #
        # Byte counters visible over JTAG, so the link can be diagnosed even
        # when the host end is not behaving. A counter that stays at zero while
        # the host thinks it is transmitting points at enumeration or endpoint
        # configuration rather than at the wire.
        #
        rx_count = Signal(16)
        tx_count = Signal(16)

        with m.If(serial.rx.valid & serial.rx.ready):
            m.d.sync += rx_count.eq(rx_count + 1)
        with m.If(serial.tx.valid & serial.tx.ready):
            m.d.sync += tx_count.eq(tx_count + 1)

        registers.add_read_only_register(
            REGISTER_STATUS,
            read=Cat(rx_count, tx_count))

        return m
