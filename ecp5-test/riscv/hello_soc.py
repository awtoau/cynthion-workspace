#!/usr/bin/env python3
#
# A RISC-V core on block RAM, printing over USB CDC-ACM.
# SPDX-License-Identifier: BSD-3-Clause

"""
Phase 1 of the RISC-V bring-up: a core, memory, and a way to see it run.

The point is a prompt, not a benchmark. Block RAM is single-cycle and needs no
cache, no bus wrapper and no latency tuning, so the only things that can be
wrong are the CPU, its reset vector and the peripheral it writes to. HyperRAM
would mean debugging a CPU and a latency-sensitive memory at once.

Output goes over USB CDC-ACM from the FPGA rather than through the Apollo UART.
On r1.4 the `uart 0` pins (R14/T14) are shared with JTAG TDI/TMS, so a design
that drives them competes with the thing loading its own bitstream. The USB path
has no such conflict, and the CDC gateware is already measured -- 195 Mbps
loopback -- so it is known to work.

That means the console peripheral is not `luna_soc.gateware.core.uart`: that one
instantiates AsyncSerialTX and drives pins. This one presents the same
register shape to software (write a byte, poll a ready bit) but hands the byte
to a USB endpoint instead of a shift register.

    ./ecp5-test/riscv/hello_soc.py --build
    ./ecp5-test/riscv/hello_soc.py --build --program
"""

import argparse
import sys
from pathlib import Path

from amaranth                       import Elaboratable, Module, Signal, Cat
from amaranth.lib                   import wiring, stream
from amaranth.lib.fifo              import SyncFIFOBuffered

from luna.gateware.architecture.car import LunaECP5DomainGenerator

# Import order matters. amaranth_soc is vendored inside luna_soc rather than
# installed standalone, and importing a luna_soc peripheral is what aliases it
# onto sys.modules under the bare name. Importing the vendored path directly
# instead yields a *different* class object for wishbone.Interface, so
# Decoder.add() rejects a bus that is structurally identical -- these must come
# first, and the bare name must be used afterwards.
from luna_soc.gateware.core         import blockram
from luna_soc.gateware.cpu          import VexRiscv

from amaranth_soc                   import csr, wishbone
from amaranth_soc.wishbone          import Decoder

ROOT = Path(__file__).resolve().parent.parent.parent

# 64 KiB at address zero, matching what moondancer allocates. The reset vector
# is 0x00000000, so the firmware's entry point must be the first instruction.
RAM_BASE = 0x00000000
RAM_SIZE = 64 * 1024

# The console peripheral, clear of the 64 KiB RAM window.
#
# It must also be inside what the decoder can reach. The Wishbone decoder is
# 30 bits wide with byte granularity, so it spans 0x40000000 -- an address at
# or above that is silently unreachable. 0x80000000 looked like the obvious
# "high peripheral" address and produced a design that built, enumerated, and
# dropped every store on the floor: the CPU wrote, the decoder saw a truncated
# address, nothing matched, and no error was raised anywhere.
CONSOLE_BASE = 0x10000000

CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}


class ConsolePeripheral(wiring.Component):
    """A byte sink that looks like a UART to software and a stream to USB.

    Software sees two registers: write a byte to `data`, and poll `ready` to
    find out whether there is room for another. That is the same contract as
    the luna_soc UART peripheral, so firmware written against one works against
    the other -- which matters because phase 2 wants to run benchmarks whose
    output routing should not be their concern.

    Behind it is a FIFO rather than a single byte. A CPU writing a string
    character by character would otherwise stall on every byte waiting for USB,
    and USB delivers in packets rather than bytes.
    """

    def __init__(self, *, depth=1024):
        self.depth = depth
        self._data = csr.Register({"data": csr.Field(csr.action.W, 8)},
                                  access="w")
        self._ready = csr.Register({"ready": csr.Field(csr.action.R, 1)},
                                   access="r")

        builder = csr.Builder(addr_width=4, data_width=8)
        builder.add("data", self._data)
        builder.add("ready", self._ready)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":    wiring.In(csr.Signature(addr_width=4, data_width=8)),
            "source": wiring.Out(stream.Signature(8)),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        fifo = SyncFIFOBuffered(width=8, depth=self.depth)
        m.submodules.fifo = fifo

        m.d.comb += [
            fifo.w_data.eq(self._data.f.data.w_data),
            fifo.w_en.eq(self._data.f.data.w_stb),
            self._ready.f.ready.r_data.eq(fifo.w_rdy),

            self.source.payload.eq(fifo.r_data),
            self.source.valid.eq(fifo.r_rdy),
            fifo.r_en.eq(self.source.ready),
        ]
        return m


class HelloSoC(Elaboratable):
    """VexRiscv, 64 KiB of block RAM, and a USB serial console."""

    def __init__(self, firmware):
        self.firmware = firmware

    def elaborate(self, platform):
        m = Module()

        m.submodules.car = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        # The variant moondancer ships. Pre-generated Verilog, so the Scala
        # toolchain freeze against Java 25 does not apply -- that blocks
        # regenerating the core, not using it.
        cpu = VexRiscv(variant="cynthion", reset_addr=RAM_BASE)
        m.submodules.cpu = cpu

        ram = blockram.Peripheral(size=RAM_SIZE, init=self.firmware)
        m.submodules.ram = ram

        console = ConsolePeripheral()
        m.submodules.console = console

        # Wishbone: RAM only. The console is a CSR peripheral behind its own
        # bridge, added to the same decoder through a wishbone-to-CSR bridge.
        decoder = Decoder(addr_width=30, data_width=32, granularity=8,
                          features={"cti", "bte", "err"})
        m.submodules.decoder = decoder
        decoder.add(ram.bus, addr=RAM_BASE, name="ram")

        from amaranth_soc.csr.wishbone import WishboneCSRBridge
        csr_bridge = WishboneCSRBridge(console.bus, data_width=32)
        m.submodules.csr_bridge = csr_bridge
        decoder.add(csr_bridge.wb_bus, addr=CONSOLE_BASE, name="console")

        # Both CPU ports share one decoder through an arbiter, so instruction
        # fetch and data access cannot corrupt each other.
        arbiter = wishbone.Arbiter(addr_width=30, data_width=32,
                                   granularity=8,
                                   features={"cti", "bte", "err"})
        m.submodules.arbiter = arbiter
        arbiter.add(cpu.ibus)
        arbiter.add(cpu.dbus)
        wiring.connect(m, arbiter.bus, decoder.bus)

        # USB CDC-ACM. The console stream is the IN endpoint's data source;
        # nothing reads from the host, since phase 1 only needs output.
        from luna.gateware.usb.usb2.device import USBDevice
        from luna.gateware.usb.request.standard import StandardRequestHandler
        from luna.gateware.usb.usb2.endpoints.stream import USBStreamInEndpoint
        from usb_protocol.emitters import DeviceDescriptorCollection

        ulpi = platform.request("target_phy")
        usb = USBDevice(bus=ulpi)
        m.submodules.usb = usb

        descriptors = DeviceDescriptorCollection()
        with descriptors.DeviceDescriptor() as d:
            # 1209:000e is the pid.codes "example" ID that 54-cynthion.rules
            # already grants uaccess to. Picking an unlisted PID leaves the
            # device enumerating but unopenable without root, which looks
            # exactly like a dead CPU.
            d.idVendor, d.idProduct = 0x1209, 0x000e
            d.iManufacturer, d.iProduct = "Cynthion", "RISC-V console"
            d.bNumConfigurations = 1
        with descriptors.ConfigurationDescriptor() as c:
            with c.InterfaceDescriptor() as i:
                i.bInterfaceNumber = 0
                with i.EndpointDescriptor() as e:
                    e.bEndpointAddress = 0x81
                    # 512 is the high-speed bulk maximum. The default is 64,
                    # the full-speed limit, which enumerates at high speed and
                    # then runs at an eighth of the achievable rate.
                    e.wMaxPacketSize = 512
        usb.add_standard_control_endpoint(descriptors)

        endpoint = USBStreamInEndpoint(endpoint_number=1, max_packet_size=512)
        usb.add_endpoint(endpoint)

        # LUNA's StreamInterface predates amaranth.lib.stream and is wired by
        # assignment rather than wiring.connect. `last` stays low so the
        # endpoint emits full packets instead of terminating one per byte.
        m.d.comb += [
            endpoint.stream.payload.eq(console.source.payload),
            endpoint.stream.valid.eq(console.source.valid),
            endpoint.stream.last.eq(0),
            console.source.ready.eq(endpoint.stream.ready),
        ]

        m.d.comb += usb.connect.eq(1)
        return m


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--program", action="store_true")
    parser.add_argument("--firmware", type=Path,
                        default=ROOT / "tmp" / "riscv_hello" / "hello.bin")
    args = parser.parse_args()

    if not args.firmware.exists():
        print(f"no firmware at {args.firmware}")
        print("build it with ./scripts/riscv_firmware.py")
        return 1

    raw = args.firmware.read_bytes()
    if len(raw) > RAM_SIZE:
        print(f"firmware is {len(raw)} bytes, block RAM is {RAM_SIZE}")
        return 1

    # Block RAM is 32 bits wide, so the image is loaded as words.
    padded = raw + b"\x00" * (-len(raw) % 4)
    words = [int.from_bytes(padded[i:i + 4], "little")
             for i in range(0, len(padded), 4)]
    print(f"firmware: {len(raw)} bytes, {len(words)} words")

    if not (args.build or args.program):
        print("nothing to do; pass --build")
        return 0

    # The installed cynthion package, not the in-repo source tree: the repo
    # copy pulls in amaranth_boards, which is not installed here, while the
    # packaged platform has no such dependency.
    from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4

    CynthionPlatformRev1D4().build(
        HelloSoC(firmware=words),
        do_program=args.program,
        build_dir=str(ROOT / "tmp" / "riscv_hello" / "build"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
