#!/usr/bin/env python3
#
# A RISC-V core on block RAM, printing over USB CDC-ACM on the AUX port.
# SPDX-License-Identifier: BSD-3-Clause

"""
The same block-RAM SoC, running VexiiRiscv instead of VexRiscv: a core, memory, and a way to see it run.

The point is a prompt, not a benchmark. Block RAM is single-cycle and needs no
cache, no bus wrapper and no latency tuning, so the only things that can be
wrong are the CPU, its reset vector and the peripheral it writes to. HyperRAM
would mean debugging a CPU and a latency-sensitive memory at once.

Output goes over USB CDC-ACM from the FPGA rather than through the Apollo UART, on the
AUX port, appearing as an ordinary `/dev/ttyACM*` tty.

**This was not true until now.** The design claimed CDC-ACM in three places while
building a bare vendor-specific interface with one bulk IN endpoint and no CDC
descriptors, so no tty node ever appeared -- the kernel is right to refuse a serial
driver for a vendor-specific class. An investigation into the resulting silence spent
time reading `/dev/ttyACM1`, which is an ST-LINK. It now uses LUNA's `USBSerialDevice`,
the same gateware measured at 195.4 Mbps loopback in
`../../docs/luna_ecp5_fpga/usb-performance.md`.
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

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import usb_ids

# Import order matters. amaranth_soc is vendored inside luna_soc rather than
# installed standalone, and importing a luna_soc peripheral is what aliases it
# onto sys.modules under the bare name. Importing the vendored path directly
# instead yields a *different* class object for wishbone.Interface, so
# Decoder.add() rejects a bus that is structurally identical -- these must come
# first, and the bare name must be used afterwards.
from luna_soc.gateware.core         import blockram

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
from vexii_cpu import VexiiRiscv

from amaranth_soc                   import csr, wishbone
from amaranth_soc.wishbone          import Decoder

ROOT = Path(__file__).resolve().parent.parent.parent

# 64 KiB at address zero, matching what moondancer allocates. The reset vector
# is 0x00000000, so the firmware's entry point must be the first instruction.
RAM_BASE = 0x00000000
RAM_SIZE = 64 * 1024

# The console peripheral. Bit 31 must be set, and that is not a style choice.
#
# The `cynthion` variant uses DBusCachedPlugin, whose memory translator marks
# an access as uncached I/O only when address bit 31 is high:
#
#     assign DBusCachedPlugin_mmuBus_rsp_isIoAccess =
#         DBusCachedPlugin_mmuBus_rsp_physicalAddress[31];
#
# A peripheral below 0x80000000 is therefore cacheable: stores are absorbed by
# the write-back data cache and never become a bus cycle, and polling a status
# register reads back the CPU's own cache line. The design builds, enumerates,
# and is silent, with the CPU running perfectly the whole time.
#
# moondancer puts its CSRs at 0xf0000000 for this reason, and uses 0x10000000
# for SPI flash, which it *wants* cached.
CONSOLE_BASE = 0xf0000000

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

    The depth is two 512-byte USB packets: enough that the CPU can fill one
    while another is in flight, so a burst of output does not stall on the
    endpoint. Larger buys nothing here -- the firmware prints a line at a time,
    not megabytes -- and each 1024 bytes is a block RAM that the CPU's own
    memory then cannot have.

    `ready` is the FIFO's write-side space, and the endpoint's `valid` is its
    read side. So an empty FIFO with a live USB device means no store from the
    CPU is landing, rather than anything being wrong downstream.
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
        # Caches are not optional on the Wishbone path: the cacheless bridge
        # asserts !withAmo, and the firmware needs atomics.
        cpu = VexiiRiscv(reset_addr=RAM_BASE, cache_sets=64)
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

        # All three CPU ports share one decoder through an arbiter, so no two
        # can corrupt each other.
        #
        # `iobus` is the uncached path and it is not optional: the console is in
        # a `main=0` PMA region, so every console access arrives there rather
        # than on `dbus`. Omitting it is a silent failure -- synthesis warns
        # about undriven ACK/DAT_MISO/ERR and then produces a CPU that runs,
        # passes timing and enumerates over USB while every store to a
        # peripheral waits forever for an acknowledgement nothing drives. That
        # cost two sessions of looking for a firmware bug.
        arbiter = wishbone.Arbiter(addr_width=30, data_width=32,
                                   granularity=8,
                                   features={"cti", "bte", "err"})
        m.submodules.arbiter = arbiter
        arbiter.add(cpu.ibus)
        arbiter.add(cpu.dbus)
        arbiter.add(cpu.iobus)
        wiring.connect(m, arbiter.bus, decoder.bus)

        # USB CDC-ACM, on the AUX port.
        #
        # This was previously a bare USBDevice with one bulk IN endpoint and no CDC
        # descriptors -- despite the comment claiming CDC-ACM. That is why no ttyACM node
        # ever appeared: the kernel correctly declines to bind a serial driver to a
        # vendor-specific interface, and an investigation chasing the silence ended up
        # reading /dev/ttyACM1, which is an ST-LINK.
        #
        # USBSerialDevice is the same gateware measured at 195.4 Mbps CDC-ACM loopback in
        # docs/luna_ecp5_fpga/usb-performance.md -- CDC costs essentially nothing over raw
        # bulk, since it is the same two stream endpoints plus descriptors.
        from luna.gateware.usb.devices.acm import USBSerialDevice

        # AUX rather than CONTROL: CONTROL is shared with Apollo and needs an
        # ApolloAdvertiser to claim, while AUX belongs to the FPGA outright. The previous
        # code used `target_phy`, which is the port under test rather than a debug port.
        bus = platform.request("aux_phy", 0)

        # 512 is the high-speed bulk maximum. The default of 64 is the full-speed limit,
        # which enumerates at high speed and then runs at an eighth of the achievable rate.
        # ID from the central allocation, never a locally chosen number. 0x615c -- what
        # this used to claim -- is Apollo's own debugger and bootloader ID, so this
        # bitstream was impersonating the debugger. See ecp5-test/usb_ids.py.
        serial = USBSerialDevice(bus=bus,
                                 idVendor=usb_ids.VENDOR_ID,
                                 idProduct=usb_ids.product_id("riscv_console"),
                                 manufacturer_string="Great Scott Gadgets",
                                 product_string=usb_ids.product_string("riscv_console"),
                                 max_packet_size=512)
        m.submodules.serial = serial

        # Connect immediately: nothing here needs to initialise first, and a device that
        # only appears after a host poke is harder to diagnose than one simply present.
        m.d.comb += serial.connect.eq(1)

        # Console FIFO -> host. The receive direction is tied off: phase 1 only needs
        # output, and leaving rx.ready low would stall the endpoint rather than discard.
        m.d.comb += [
            serial.tx.payload.eq(console.source.payload),
            serial.tx.valid.eq(console.source.valid),
            serial.tx.first.eq(0),

            # `last` marks the final beat OF A PACKET, and the endpoint only observes it
            # on a beat where `valid` is high. An earlier version here drove
            # `last = ~valid`, which is unsatisfiable: the two are never high together, so
            # no packet was ever terminated and nothing reached the host -- with the CPU
            # running and the FIFO filling the whole time.
            #
            # USBStreamInEndpoint has a `flush` input for exactly this, but
            # USBSerialDevice does not expose it -- it lives on the endpoint the device
            # constructs internally. So the packet boundary has to come from `last`.
            #
            # One byte per packet. A console emits a line and goes quiet, so waiting for
            # 512 bytes would hold the banner indefinitely. The cost is a USB transaction
            # per byte, which for a console at human-readable rates is irrelevant, and
            # correctness here matters more than throughput.
            serial.tx.last.eq(1),

            console.source.ready.eq(serial.tx.ready),
            serial.rx.ready.eq(1),
        ]

        # The single-wire debug link to Apollo. Present in every test design
        # so a bitstream that says nothing over USB can still be asked what it
        # is doing -- USB, the PHY and the CPU are all bypassed by this path.
        sys.path.insert(0, str(ROOT / "ecp5-test"))
        from sideband_debug import SidebandDebug
        m.submodules.sideband = sideband = SidebandDebug()

        # Report whether the CPU's buses are moving at all. If USB is silent
        # and this shows zero activity, the fault is the CPU rather than
        # anything downstream of it.
        # `iobus` rather than `dbus` for the second state bit: the console lives
        # on the uncached path, so dbus activity says the CPU is running while
        # iobus activity says it is actually reaching the peripheral. Those are
        # different questions, and conflating them is what made this SoC look
        # dead when it was only mute.
        # STICKY, not live. `cyc` is a Wishbone strobe -- high only during a
        # transaction, a few cycles at a time -- and the sideband samples whenever the
        # host happens to ask. Reporting it directly answers "is a transaction in flight
        # at this instant", which is 0 most of the time even on a busy CPU, and reads as
        # a dead core. These latch on the first assertion and never clear, so they answer
        # "has this bus EVER moved" -- which is the question actually being asked.
        ever_fetched = Signal()
        ever_io      = Signal()
        ever_errored = Signal()
        ever_console = Signal()
        with m.If(cpu.ibus.cyc):
            m.d.sync += ever_fetched.eq(1)
        with m.If(cpu.iobus.cyc):
            m.d.sync += ever_io.eq(1)
        with m.If(cpu.ibus.err | cpu.dbus.err | cpu.iobus.err):
            m.d.sync += ever_errored.eq(1)
        with m.If(console.source.valid):
            m.d.sync += ever_console.eq(1)

        m.d.comb += [
            sideband.state.eq(Cat(ever_fetched, ever_io)),
            sideband.events.eq(ever_console),
            sideband.error.eq(ever_errored),
        ]

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
        build_dir=str(ROOT / "tmp" / "vexii_hello" / "build"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
