#!/usr/bin/env python3
#
# Run a generated MicroSoC on hardware and bring its UART out over USB.
# SPDX-License-Identifier: BSD-3-Clause

"""
Wraps a generated VexiiRiscv MicroSoC so its console reaches the host.

MicroSoC arrives as Verilog with four ports: a clock, a reset, and a UART
txd/rxd pair. Its RAM is initialised from an ELF at generation time
(`--ram-elf`), so the firmware is already inside the bitstream and nothing
needs loading at runtime.

That leaves one job: get the UART off the chip. It is not routed to pins here.
On r1.4 the `uart 0` pins are shared with JTAG TDI/TMS, so driving them
competes with the thing loading the bitstream. Instead the serial stream is
received in gateware and pushed into a USB bulk endpoint, the same path the
block-RAM SoC uses for its console.

The baud rate has to match what the firmware programs into the UART. MicroSoC's
divider is set by software rather than fixed at generation, so this receives at
a configured rate and will produce framing errors rather than silence if the
two disagree -- which is the more diagnosable failure.

    ./gateware/soc/bench.py --build
"""

import sys as _uid_sys
from pathlib import Path as _uid_Path
_uid_sys.path.insert(0, str(_uid_Path(__file__).resolve().parent.parent))
import usb_ids


import argparse
import sys
from pathlib import Path

from amaranth                        import Elaboratable, Module, Signal, Instance, ClockSignal, ResetSignal
from amaranth.lib.fifo               import SyncFIFOBuffered
from amaranth_stdio.serial           import AsyncSerialRX

from luna.gateware.architecture.car  import LunaECP5DomainGenerator

ROOT = Path(__file__).resolve().parent.parent.parent
VERILOG = ROOT / "repos" / "vexiiriscv" / "MicroSoc.v"

# MicroSoC runs in the `sync` domain. 60 MHz matches what the block-RAM SoC
# uses, so CoreMark cycle counts are comparable between the two without
# rescaling.
CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}
SYNC_HZ = 60_000_000

# What the MicroSoC firmware programs into its UART divider.
BAUD = 115200


class VexiiBenchSoC(Elaboratable):
    """A generated MicroSoC, with its UART bridged to a USB bulk endpoint."""

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        platform.add_file("MicroSoc.v", VERILOG.read_text())

        # --ram-elf writes the firmware image out as separate per-byte-lane
        # .bin files that the Verilog reads with $readmemb at elaboration.
        # They have to sit beside the sources in the build directory or yosys
        # fails on a missing file rather than on anything to do with the
        # design.
        for init in sorted(VERILOG.parent.glob("MicroSoc.v_toplevel_*.bin")):
            platform.add_file(init.name, init.read_text())

        uart_tx = Signal()
        m.submodules.soc = Instance(
            "MicroSoc",
            i_socCtrl_systemClk=ClockSignal("sync"),
            i_socCtrl_asyncReset=ResetSignal("sync"),
            o_system_peripheral_uart_logic_uart_txd=uart_tx,
            # Nothing sends to the SoC; idle high is a UART's mark state, and
            # tying it low would read as a permanent break.
            i_system_peripheral_uart_logic_uart_rxd=1,
        )

        # Receive the SoC's serial output in gateware.
        divisor = int(SYNC_HZ // BAUD)
        m.submodules.rx = rx = AsyncSerialRX(divisor=divisor, divisor_bits=16)
        m.d.comb += rx.i.eq(uart_tx)

        # Buffer before USB: the UART delivers a byte every ~87 us at 115200
        # while USB moves packets, so without a FIFO every byte would wait for
        # a bus transaction.
        fifo = SyncFIFOBuffered(width=8, depth=2048)
        m.submodules.fifo = fifo
        m.d.comb += [
            fifo.w_data.eq(rx.data),
            fifo.w_en.eq(rx.rdy & ~rx.err.frame & ~rx.err.overflow),
            rx.ack.eq(fifo.w_rdy),
        ]

        from luna.gateware.usb.usb2.device import USBDevice
        from luna.gateware.usb.usb2.endpoints.stream import USBStreamInEndpoint
        from usb_protocol.emitters import DeviceDescriptorCollection

        ulpi = platform.request("target_phy")
        usb = USBDevice(bus=ulpi)
        m.submodules.usb = usb

        descriptors = DeviceDescriptorCollection()
        with descriptors.DeviceDescriptor() as d:
            # 1209:000e is granted uaccess by the shipped udev rules; an
            # unlisted product ID enumerates but cannot be opened without root.
            d.idVendor, d.idProduct = usb_ids.VENDOR_ID, usb_ids.product_id("riscv_bench")
            d.iManufacturer, d.iProduct = usb_ids.product_string("riscv_bench"), usb_ids.product_string("riscv_bench")
            d.bNumConfigurations = 1
        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x81
                    e.wMaxPacketSize = 512
        usb.add_standard_control_endpoint(descriptors)

        endpoint = USBStreamInEndpoint(endpoint_number=1, max_packet_size=512)
        usb.add_endpoint(endpoint)

        m.d.comb += [
            endpoint.stream.payload.eq(fifo.r_data),
            endpoint.stream.valid.eq(fifo.r_rdy),
            endpoint.stream.last.eq(0),
            fifo.r_en.eq(endpoint.stream.ready),
            # Send short packets as soon as the FIFO drains. Without this the
            # endpoint waits for 512 bytes, and a benchmark that prints a few
            # hundred would appear to hang.
            endpoint.flush.eq(~fifo.r_rdy),
            usb.connect.eq(1),
        ]
        return m


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--build-dir", default="tmp/vexii_bench/build")
    args = parser.parse_args()

    if not VERILOG.exists():
        print(f"no MicroSoc.v at {VERILOG}")
        print("generate one with MicroSocGen --ram-elf <firmware>")
        return 1

    if not args.build:
        print(f"MicroSoc.v: {VERILOG.stat().st_size // 1024} KiB")
        print("pass --build to synthesise")
        return 0

    from board.cynthion_r1_4 import CynthionPlatformRev1D4
    CynthionPlatformRev1D4().build(VexiiBenchSoC(), do_program=False,
                                   build_dir=str(ROOT / args.build_dir))
    return 0


if __name__ == "__main__":
    sys.exit(main())
