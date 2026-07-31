#!/usr/bin/env python3
#
# The receiving half of a USB bitstream loader, and the proof it cannot finish.
# SPDX-License-Identifier: BSD-3-Clause

"""
Receives a bitstream over USB bulk at full rate, and measures the receiving.

Issue #100 proposes a loader that owns a USB port, accepts a bitstream over bulk,
and configures the ECP5's SRAM with it -- replacing a ~3 s JTAG load with ~6 ms
of transfer. This is the receiving half of that loader. The configuring half
cannot be written, and this file exists partly to make that concrete.

**Why the second half is missing.** The ECP5 has no fabric path into its own
configuration engine. There is no ICAP-equivalent; the complete primitive list is
`JTAGG`, `OSCG`, `SEDGA`, `DTR`, `USRMCLK`, `GSR`, and none of them accepts
configuration data. `JTAGG` exposes only the ER1/ER2 user data registers, not the
instruction register, so the configuration opcodes are unreachable from fabric.
MachXO2's `PCNTR`, which would allow exactly this, does not exist on ECP5.
`--background` does not help: it enables partial reconfiguration driven by an
*external* master, and in background mode the persistent ports are read-only.
Even triggering a reconfiguration is impossible from fabric -- PROGRAMN is a
dedicated input pin, and REFRESH is accepted only on JTAG and Slave SPI.

The evidence, with citations, is in docs/luna_ecp5_fpga/fast-bitstream-loading.md.

**So what is this for?** It measures the leg of the journey that the issue
assumed was the expensive one, and shows it is not. The FPGA can absorb a
bitstream-sized payload over bulk in a few milliseconds. The ~3 s is spent
elsewhere entirely -- getting the image through the SAMD11 debug controller,
which is a *full-speed* 12 Mbps device, on control transfers costing 204 us each
plus 2.53 us/byte. The 388 Mbps the issue reasons from belongs to this PHY, not
to the path the bitstream actually takes.

Running this therefore establishes the useful negative: even at infinite FPGA-side
speed, the load time is unchanged, because the bottleneck is upstream.

The design counts bytes and measures its own reception window in 60 MHz clocks,
both readable over JTAG. Data is discarded after counting -- there is nowhere for
it to go, which is the point.

    ./ecp5-test/loader/bitstream_sink.py --build
    ./ecp5-test/loader/bitstream_sink.py --build --program
    ./scripts/fast_loader.py --mode sink-test --bitstream <file.bit>
"""

import sys as _uid_sys
from pathlib import Path as _uid_Path
_uid_sys.path.insert(0, str(_uid_Path(__file__).resolve().parent.parent))
import usb_ids


import argparse
import sys
from pathlib import Path

from amaranth import Cat, Const, Elaboratable, Module, Signal

from luna.gateware.architecture.car import LunaECP5DomainGenerator
from luna.gateware.interface.jtag import JTAGRegisterInterface
from luna.gateware.usb.usb2.device import USBDevice
from luna.gateware.usb.usb2.endpoints.stream import USBStreamOutEndpoint

from usb_protocol.emitters import DeviceDescriptorCollection

ROOT = Path(__file__).resolve().parent.parent.parent

CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# 512 is the high-speed bulk maximum. The default is 64 -- the full-speed limit
# -- which enumerates at high speed and then runs at an eighth of the rate,
# silently. That failure mode is exactly what this design exists to rule out.
MAX_PACKET_SIZE = 512

BULK_OUT_ENDPOINT = 1

# 1209:000e is the pid.codes "example" ID that 54-cynthion.rules already grants
# uaccess to. An unlisted PID enumerates but cannot be opened without root,
# which looks identical to broken gateware.
USB_VENDOR_ID  = usb_ids.VENDOR_ID
USB_PRODUCT_ID = usb_ids.product_id("bitstream_sink")

# TARGET-C. The CONTROL port is shared with the Apollo debug controller and
# would need an ApolloAdvertiser to claim; TARGET is free. Note that on r1.4 the
# `uart 0` pins are shared with JTAG TDI/TMS, so nothing here drives them --
# JTAG is how the counters are read back.
PHY_NAME = "target_phy"

APPLET_ID = 0x4B4E4953   # "SINK"

REGISTER_ID      = 1
REGISTER_BYTES   = 2   # bytes received
REGISTER_CYCLES  = 3   # 60 MHz clocks spanned by the reception
REGISTER_PACKETS = 4   # packets seen, to distinguish stall from starvation

# Reception is considered finished once the stream has been idle this long. At
# 60 MHz this is ~17 ms, comfortably longer than any inter-packet gap the host
# introduces mid-transfer (a full-speed frame is 1 ms, a high-speed microframe
# 125 us) and short enough to settle before the host reads the counters back.
IDLE_TIMEOUT_CYCLES = 1_000_000


class BitstreamSink(Elaboratable):
    """Counts bytes arriving on a bulk OUT endpoint, and times the arrival."""

    def create_descriptors(self):
        descriptors = DeviceDescriptorCollection()

        with descriptors.DeviceDescriptor() as d:
            d.idVendor = USB_VENDOR_ID
            d.idProduct = USB_PRODUCT_ID
            d.iManufacturer = "Cynthion"
            d.iProduct = "Bitstream sink"
            d.bNumConfigurations = 1

        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0

                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = BULK_OUT_ENDPOINT
                    e.wMaxPacketSize = MAX_PACKET_SIZE

        return descriptors

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        bus = platform.request(PHY_NAME, 0)
        usb = USBDevice(bus=bus)
        m.submodules.usb = usb

        usb.add_standard_control_endpoint(self.create_descriptors())
        m.d.comb += usb.connect.eq(1)

        stream_out = USBStreamOutEndpoint(
            endpoint_number=BULK_OUT_ENDPOINT,
            max_packet_size=MAX_PACKET_SIZE)
        usb.add_endpoint(stream_out)

        byte_count = Signal(32)
        cycle_count = Signal(32)
        packet_count = Signal(32)
        idle_count = Signal(range(IDLE_TIMEOUT_CYCLES + 1))
        running = Signal()

        # `ready` is tied high: the sink never back-pressures, so the measured
        # rate is the host's and the bus's, not an artefact of this design
        # stalling. Data is discarded -- there is nowhere on ECP5 for it to go.
        m.d.comb += stream_out.stream.ready.eq(1)

        accepted = stream_out.stream.valid & stream_out.stream.ready

        with m.If(accepted):
            m.d.usb += [
                byte_count.eq(byte_count + 1),
                running.eq(1),
                idle_count.eq(0),
            ]
            with m.If(stream_out.stream.last):
                m.d.usb += packet_count.eq(packet_count + 1)

        # The clock counter runs only while a transfer is in flight, so the
        # figure is the reception window rather than time since configuration.
        # Without the idle timeout it would keep counting after the host
        # finished and report a rate arbitrarily close to zero.
        with m.Elif(running):
            with m.If(idle_count == IDLE_TIMEOUT_CYCLES):
                m.d.usb += running.eq(0)
            with m.Else():
                m.d.usb += idle_count.eq(idle_count + 1)

        with m.If(running):
            m.d.usb += cycle_count.eq(cycle_count + 1)

        registers.add_read_only_register(REGISTER_BYTES, read=byte_count)
        registers.add_read_only_register(REGISTER_CYCLES, read=cycle_count)
        registers.add_read_only_register(REGISTER_PACKETS, read=packet_count)

        return m


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--program", action="store_true",
                        help="configure SRAM over JTAG; does not touch flash")
    args = parser.parse_args()

    if not (args.build or args.program):
        print("nothing to do; pass --build")
        return 0

    # The installed cynthion package, not the in-repo source tree: the repo copy
    # pulls in amaranth_boards, which is not installed here.
    from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4

    CynthionPlatformRev1D4().build(
        BitstreamSink(),
        do_program=args.program,
        build_dir=str(ROOT / "tmp" / "loader_sink" / "build"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
