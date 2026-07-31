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
from amaranth.lib.fifo              import AsyncFIFOBuffered

# Not LunaECP5DomainGenerator: it clocks `sync` at 60 MHz and offers only 60/120/240
# elsewhere, so a speed ladder can only step in factors of two. Nothing in the hardware
# requires that -- the PLL runs a 480 MHz VCO and each output divides it, so 80, 96, 100
# and the rest are all reachable. This one takes an arbitrary frequency, derives real
# dividers with ecppll, and reports what it actually produced.
from apollo_fpga.gateware.variable_clock import VariableClockDomainGenerator

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
import vexii_cpu
from vexii_cpu import VexiiRiscv
from vexii_flash import (FairSPIControlPortCrossbar, FlashILA, FlashPinProbe,
                         HoldableSPIController, ModalSPIFlashMemoryMap,
                         ObservablePHY, QSPIFlashPins)

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

# The configuration SPI flash, memory-mapped and read-only.
#
# 0x10000000 is what `repos/cynthion/.../facedancer/top.py` uses for
# `spiflash_base`, and matching it means firmware written for one SoC finds the
# flash where it expects on the other.
#
# It is a `main=1` PMA region, so the D-cache backs it and the I-cache can fetch
# from it -- see FLASH_REGION below. That is not a performance nicety: a
# `main=0` region routes to the uncached `iobus`, where every single load is a
# complete flash transaction (command, 24-bit address, dummy, data) with no
# burst continuation and no line reuse. Nothing fails; it just runs at a
# fraction of the rate, silently.
FLASH_BASE = 0x10000000

# The SPI controller's own registers -- the arbitrary-command path, used here
# only to read the JEDEC ID.
#
# This has to sit inside the CSR region declared `main=0`, alongside the
# console, and not next to the flash's memory map: these are volatile registers
# where a read has a side effect (it pops the RX FIFO). Caching them would mean
# the second read of `data` returns the first read's byte from a cache line,
# forever. The flash's *memory* wants the cache; the flash controller's
# *registers* must never see it.
FLASH_CSR_BASE = 0xf0000100

# The pin probe's registers. Same uncached CSR region as everything else here:
# these are counters that change underneath the CPU, so a cached read would
# return a stale line and report no activity on a busy bus.
FLASH_PROBE_BASE = 0xf0000200

# The logic analyser's registers, in the same uncached CSR region.
FLASH_ILA_BASE = 0xf0000300

# Capture depth, in samples of the sync clock.
#
# 32 SCK edges at divisor 0 is 64 sync cycles of clocking, plus the FSM
# transitions between the four transfers of a JEDEC read. 1024 spans all of it
# with room to spare and costs exactly one DP16KD at 8 bits wide -- the SoC uses
# 41 of 56, so nothing else has to give way. Depth over width was the right
# trade here: the question is when a strobe fires across a whole multi-transfer
# command, not what a dozen other signals are doing.
ILA_DEPTH = 1024

# 4 MiB, W25Q32, JEDEC EF 40 16. The SFDP table declares 4 MiB and everything
# above that aliases back to offset 0, so mapping more would map the same chip
# twice under two addresses.
FLASH_SIZE = 4 * 1024 * 1024

# THE BITSTREAM LIVES AT OFFSET 0. Nothing in this design writes or erases --
# `ModalSPIFlashMemoryMap` issues read opcodes only and the FSM has no write
# path -- but the offset the firmware reads for its data check is chosen well
# clear of the bitstream anyway, because a read of a region being actively
# rewritten by something else would be an unstable test.
#
# 0x00300000 is 3 MiB in, past a 308 KiB bitstream by an order of magnitude.
FLASH_TEST_OFFSET = 0x00300000

# Read mode. "single" is 0x03, one lane, no dummy cycles -- the mode to bring up
# first, because there is nothing in it to get subtly wrong. "quad" is 0xeb.
# See ecp5-test/riscv/vexii_flash.py.
FLASH_MODE = "single"

# SCK = sync / (2 * (1 + divisor)). At 80 MHz sync, divisor 0 gives 40 MHz,
# which is inside the ECP5 MCLK pin's 62 MHz specification (FPGA-TN-02039) and
# inside the flash's own 50 MHz rating for the single-lane 0x03 opcode.
#
# Divisor 1 (20 MHz at 60 MHz sync) was tried against the JEDEC failure and
# changed nothing -- the ID still read zeros while the benchmark slowed from
# 0x4873 to 0x80f3 cycles, confirming the divisor genuinely took effect. So the
# PHY's divisor-0 special case in XFER-END, where the last bit is captured a
# state later, is not the cause.
#
# Faster than this has been measured to work on this board and is not what the
# default should be: MCLK is a configuration pin reached through USRMCLK, and
# above 62 MHz Lattice publishes nothing to reason about margin from.
FLASH_DIVISOR = 0

# The CPU clock. `usb` stays at 60 MHz inside the domain generator -- the ULPI PHY
# requires it and it is not a free parameter -- while this is arbitrary.
#
# 60 is a constraint here rather than a limit: the design already meets 72-91 MHz by
# nextpnr's own estimate, and the die is a 25F sharing a speed grade with the 12F it is
# marked as (ecp5-test/fabric/FABRIC_TEST.md). See #110.
SYNC_MHZ = 60


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

        # ASYNC, not Sync. The CPU writes from `sync` and the USB endpoint reads from
        # `usb`, which are different domains the moment sync is not 60 MHz.
        #
        # A SyncFIFOBuffered here worked only because sync and usb were both 60 MHz, so
        # the crossing was accidentally safe. Raising sync to 80 produced a stream with
        # correct counter VALUES and dropped CHARACTERS -- `tic 00000`, `tck 000001`,
        # `ick 0000` -- because bytes were being lost in transit while the CPU-side
        # arithmetic was untouched. That is the signature of an unsynchronised crossing:
        # its pointers are not gray-coded and its ready flags are sampled in the wrong
        # domain.
        fifo = AsyncFIFOBuffered(width=8, depth=self.depth,
                                 w_domain="sync", r_domain="usb")
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

        m.submodules.car = car = VariableClockDomainGenerator(sync_mhz=SYNC_MHZ)

        # The variant moondancer ships. Pre-generated Verilog, so the Scala
        # toolchain freeze against Java 25 does not apply -- that blocks
        # regenerating the core, not using it.
        # Caches are not optional on the Wishbone path: the cacheless bridge
        # asserts !withAmo, and the firmware needs atomics.
        # The PMA regions, spelled out. VexiiRiscv routes an access by which
        # declared region it falls in, and an address in no region traps -- so
        # this list is the address map as far as the CPU is concerned, and the
        # decoder below is only the address map as far as the fabric is
        # concerned. The two have to agree.
        #
        # main=1 for the flash is the point of this whole exercise. It puts
        # flash accesses on the cached `dbus` and lets the I-cache fetch from
        # it; exe=1 permits instruction fetch, so code can execute in place.
        regions = list(vexii_cpu.DEFAULT_REGIONS) + [
            f"base={FLASH_BASE:08x},size={FLASH_SIZE:08x},main=1,exe=1",
        ]

        cpu = VexiiRiscv(reset_addr=RAM_BASE, cache_sets=64, regions=regions)
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

        # The configuration SPI flash, memory-mapped read-only.
        #
        # Four objects, each doing one thing, and all four must be submodules --
        # `flash_bus` in particular looks like a passive adapter and is not: its
        # elaborate() is what instantiates USRMCLK, so leaving it out produces a
        # design with no clock reaching the flash at all, and reads that return
        # a constant.
        #
        #   flash_pins  requests `qspi_flash` (T8 T7 M7 N7, CS on N8)
        #   flash_bus   routes SCK through USRMCLK -- see below
        #   flash_phy   shift registers and clock generation
        #   flash_mmap  Wishbone side: address decode, burst continuation
        #
        # SCK is not a pin that can be requested. On the ECP5 the configuration
        # clock MCLK is tristated on entry to user mode and stays owned by the
        # configuration block; the only route to it is the USRMCLK macro. The
        # platform's `qspi_flash` resource accordingly has dq and cs but no
        # clock, and `ECP5ConfigurationFlashInterface` exists to bridge that
        # gap: it proxies every other attribute through to the real pins while
        # supplying `sck` as a plain signal that USRMCLK consumes.
        from luna_soc.gateware.core.spiflash import ECP5ConfigurationFlashInterface, SPIPHYController

        m.submodules.flash_pins = flash_pins = QSPIFlashPins("qspi_flash")
        m.submodules.flash_bus  = flash_bus  = ECP5ConfigurationFlashInterface(
            bus=flash_pins.pins)
        # ObservablePHY, not SPIPHYController: behaviourally identical (verified
        # in simulation -- same edge count, same completion cycle) but with the
        # internal input-capture strobes brought out so the ILA below can see
        # WHEN a bit is latched, which is the question left after the pin probe.
        m.submodules.flash_phy  = flash_phy  = ObservablePHY(
            pads=flash_bus, divisor=FLASH_DIVISOR, domain="sync")
        # `spiflash.Peripheral` builds the mmap core, a CSR-poked controller,
        # and the round-robin crossbar that lets both share one PHY.
        #
        # The controller is here for one reason: the JEDEC ID. `0x9f` is a
        # register read, not an address read, so it cannot come through the
        # memory map -- the mmap FSM knows exactly one opcode and it is a read
        # of a 24-bit address. Identifying the chip needs a path that can issue
        # an arbitrary command, and that is what the controller's `phy`/`data`
        # registers are.
        #
        # It can also write. Nothing here does, and nothing should: the
        # bitstream is at offset 0. It is an arbitrary-command path, so the
        # discipline has to come from the firmware rather than the gateware.

        # The two cores and the crossbar are built here rather than by
        # `spiflash.Peripheral`, which would construct all three -- but with
        # upstream's `SPIControlPortCrossbar`, whose arbitration starves the
        # controller whenever the memory map holds chip select. That is a real
        # bug with a precise and misleading symptom: memory-mapped reads work
        # perfectly while every controller command returns zeros, which reads
        # as a broken controller when in fact its requests never reach the PHY.
        # It cost a hardware run to find and a simulation to prove
        # (scripts/riscv_flash_crossbar_sim.py). See
        # FairSPIControlPortCrossbar for the mechanism.
        #
        # Upstream's Peripheral also hardcodes the mmap read opcode at 0xeb with
        # no parameter to change it, which is the other reason it is not used.
        flash_mmap = ModalSPIFlashMemoryMap(
            size=FLASH_SIZE, mode=FLASH_MODE, name="spiflash", domain="sync")
        m.submodules.flash_mmap = flash_mmap

        # HoldableSPIController, not SPIController: upstream's chip select
        # collapses to the TX FIFO's occupancy, so it drops between transfers of
        # a multi-part command and the flash resets its command state each time.
        # The ILA caught this directly -- four separate 8-bit windows for a
        # JEDEC read with CS deasserted for 81, 36 and 36 samples between them.
        # This adds a latching `hold` register so software can keep CS asserted
        # across a whole command.
        flash_ctrl = HoldableSPIController(data_width=32, name="spi0",
                                           domain="sync")
        m.submodules.flash_ctrl = flash_ctrl

        m.submodules.flash_xbar = flash_xbar = FairSPIControlPortCrossbar(
            data_width=32, num_ports=2, domain="sync")

        # Port 0 is the controller, port 1 the memory map. The order is not
        # arbitrary: the round-robin starts from the port after the current
        # holder, so the memory map -- which requests far more often -- yielding
        # to port 0 is the common case and the one that must work.
        wiring.connect(m, flash_ctrl.source, flash_xbar.slave0.source)
        wiring.connect(m, flash_ctrl.sink,   flash_xbar.slave0.sink)
        m.d.comb += flash_xbar.slave0.cs.eq(flash_ctrl.cs)

        wiring.connect(m, flash_mmap.source, flash_xbar.slave1.source)
        wiring.connect(m, flash_mmap.sink,   flash_xbar.slave1.sink)
        m.d.comb += flash_xbar.slave1.cs.eq(flash_mmap.cs)

        wiring.connect(m, flash_xbar.controller.source, flash_phy.source)
        wiring.connect(m, flash_xbar.controller.sink,   flash_phy.sink)
        m.d.comb += flash_phy.cs.eq(flash_xbar.controller.cs)

        decoder.add(flash_mmap.bus, addr=FLASH_BASE, name="spiflash")

        # The controller's CSRs, on the same CSR bridge shape as the console.
        flash_csr_bridge = WishboneCSRBridge(flash_ctrl.bus, data_width=32)
        m.submodules.flash_csr_bridge = flash_csr_bridge
        decoder.add(flash_csr_bridge.wb_bus, addr=FLASH_CSR_BASE,
                    name="spi0")

        # Instrumentation on the pins themselves.
        #
        # Simulation and hardware disagree about this path and only the pins can
        # settle it. Every stage passes in simulation -- controller, PHY,
        # crossbar, and the whole chain end to end, all producing the expected
        # eight SCK edges -- while on hardware the JEDEC ID reads zeros and an
        # erase "completes" 14,000 times faster than the datasheet allows.
        #
        # These taps are downstream of everything: `flash_pins.pins` is what
        # QSPIFlashPins drives onto the pads, so a count here means the signal
        # genuinely left the fabric. `flash_bus.sck` is the signal handed to
        # USRMCLK, which is as close to the clock pad as this device allows --
        # MCLK has no ordinary I/O buffer and cannot be read back.
        #
        # The crossbar grant is included so the starvation fix can be confirmed
        # on hardware rather than resting on a simulation result, which is
        # precisely the kind of claim this investigation has found unreliable.
        m.submodules.flash_probe = flash_probe = FlashPinProbe()
        m.d.comb += [
            flash_probe.cs         .eq(flash_pins.pins.cs.o),
            flash_probe.sck        .eq(flash_bus.sck),
            flash_probe.dq_oe      .eq(flash_pins.pins.dq.oe),
            # Port 0 is the controller. `grant == 0` means the controller holds
            # the PHY; the probe counts rising edges of that condition.
            flash_probe.grant_ctrl .eq(flash_xbar.grant == 0),
        ]

        flash_probe_bridge = WishboneCSRBridge(flash_probe.bus, data_width=32)
        m.submodules.flash_probe_bridge = flash_probe_bridge
        decoder.add(flash_probe_bridge.wb_bus, addr=FLASH_PROBE_BASE,
                    name="flash_probe")

        # The logic analyser, on the same signals plus the PHY's internals.
        #
        # Triggered by software immediately before the transaction under test,
        # rather than by chip select falling. If the fault were that nothing is
        # issued, triggering on the symptom's absence would capture an empty
        # window and confirm only what is already known -- and the trigger has
        # to cover the gaps BETWEEN the four transfers of a JEDEC read, because
        # "does the capture strobe still fire on transfer 2" is the hypothesis.
        m.submodules.flash_ila = flash_ila = FlashILA(sample_depth=ILA_DEPTH)
        m.d.comb += [
            flash_ila.sck         .eq(flash_bus.sck),
            flash_ila.dq_i1       .eq(flash_phy.o_dq_i1),
            flash_ila.cs          .eq(flash_pins.pins.cs.o),
            flash_ila.sr_in_shift .eq(flash_phy.o_sr_in_shift),
            flash_ila.sample_stb  .eq(flash_phy.o_sample),
            flash_ila.update_stb  .eq(flash_phy.o_update),
            flash_ila.in_xfer     .eq(flash_phy.o_in_xfer),
            flash_ila.in_xfer_end .eq(flash_phy.o_in_xfer_end),
        ]

        flash_ila_bridge = WishboneCSRBridge(flash_ila.bus, data_width=32)
        m.submodules.flash_ila_bridge = flash_ila_bridge
        decoder.add(flash_ila_bridge.wb_bus, addr=FLASH_ILA_BASE,
                    name="flash_ila")

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
        # The sideband's bit period is a cycle count derived from the domain frequency, so a
        # design that raises `sync` and leaves this at its default gets a DEAD link rather
        # than a slow one -- a UART tolerates about +/-2% and the error scales with the
        # clock. Passing SYNC_MHZ keeps the two in step by construction.
        m.submodules.sideband = sideband = SidebandDebug(clk_freq_hz=SYNC_MHZ * 1e6)

        # Report whether the CPU's buses are moving at all. If USB is silent
        # and this shows zero activity, the fault is the CPU rather than
        # anything downstream of it.
        # `iobus` rather than `dbus` for the second state bit: the console lives
        # on the uncached path, so dbus activity says the CPU is running while
        # iobus activity says it is actually reaching the peripheral. Those are
        # different questions, and conflating them is what made this SoC look
        # dead when it was only mute.
        #
        # Status LEDs. The board has six FPGA LEDs and nothing was driving them, so a
        # working design and a dead one looked identical -- which is most of why the
        # silence here was hard to diagnose.
        #
        # Colour order on r1.4 is red, orange, yellow, green, blue, violet.
        #
        #   red     ERROR -- solid on any bus error, and it LATCHES. A fault that clears
        #           itself is still a fault, and one that blinks past unobserved is worse
        #           than one that stays lit.
        #   orange  the CPU has fetched at least one instruction
        #   yellow  the CPU has reached the I/O bus -- the third master is alive
        #   green   HEARTBEAT, flashing. Flashing rather than solid because a stuck-high
        #           output and a healthy design must not look the same; motion proves the
        #           clock is running and the design is not frozen.
        #   blue    console data has been queued at least once
        #   violet  USB is connected and configured
        #
        # Every one except green is sticky, for the same reason the sideband bits are:
        # these events are brief, and a human glancing at the board samples at an
        # arbitrary moment.
        leds = Cat(platform.request("led", n).o for n in range(6))

        # ~0.36 s on, ~0.36 s off at 60 MHz. Fast enough to read as deliberate, slow
        # enough to be unmistakably a flash rather than a flicker.
        heartbeat = Signal(range(int(SYNC_MHZ * 1e6 // 2) + 1))
        heartbeat_on = Signal()
        with m.If(heartbeat == int(SYNC_MHZ * 1e6 // 2)):
            m.d.sync += [heartbeat.eq(0), heartbeat_on.eq(~heartbeat_on)]
        with m.Else():
            m.d.sync += heartbeat.eq(heartbeat + 1)

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

            leds.eq(Cat(ever_errored,          # red    -- error, latched
                        ever_fetched,          # orange -- fetching
                        ever_io,               # yellow -- I/O bus reached
                        heartbeat_on,          # green  -- heartbeat, flashing
                        ever_console,          # blue   -- console data queued
                        serial.connect)),      # violet -- USB up
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
