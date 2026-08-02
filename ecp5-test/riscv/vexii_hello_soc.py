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

The console peripheral is a standard NS16550A (`uart16550.py`) whose stream ports
go to that USB endpoint instead of to a shift register. It is not
`luna_soc.gateware.core.uart`, which instantiates AsyncSerialTX and drives pins,
and it is no longer the bespoke two-register thing that preceded it -- that one
had a read with a side effect (popping the RX FIFO) one byte away from the
register the firmware polls, and firmware that polled it went silent. See the
module docstring in `uart16550.py`.

A second instance of the same peripheral faces Apollo on the shared JTAG pins,
with a real asynchronous serial PHY behind it. Same register map, same driver,
different transport -- which is the point of using a standard part.

Both UARTs' interrupt lines go to a standard RISC-V PLIC (`vexii_plic.py`) at
`PLIC_BASE`, whose output is the CPU's single machine external interrupt. The
console is interrupt-driven rather than polled as a result. The same argument
applies as for the 16550: QEMU's `-M virt` has a PLIC too, so
`firmware/cynthion-soc/src/plic.rs` is compiled unchanged for both targets and
`scripts/soc_test.py` exercises the interrupt path that ships.

    ./ecp5-test/riscv/hello_soc.py --build
    ./ecp5-test/riscv/hello_soc.py --build --program
"""

import argparse
import sys
from pathlib import Path

from amaranth                       import Elaboratable, Module, Mux, Signal, Cat
from amaranth.lib                   import wiring
from amaranth.lib.cdc               import FFSynchronizer

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
from uart16550 import Uart16550
from vexii_plic import Plic
from serial_line import SerialLine
from i2c_master import I2CMaster, prescale_for
from sideband_csr import SidebandControl
from stream_buffer import StreamBuffer
from wishbone_pipe import RegisteredResponse
from vexii_flash import (FairSPIControlPortCrossbar, FlashILA, FlashPinProbe,
                         HoldableSPIController, ModalSPIFlashMemoryMap,
                         ObservablePHY, QSPIFlashPins)

from amaranth_soc                   import csr, gpio, wishbone
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
#
# The 16550's eight registers occupy eight bytes here, so LSR at +5 lands in the
# SECOND 32-bit word and RBR at +0 in the first. That separation is the whole
# reason for adopting the standard map -- see uart16550.py.
CONSOLE_BASE = 0xf0000000

# The second 16550, facing Apollo over the shared JTAG pins.
#
# It is a plain 115200 8N1 line to the SAMD11's SERCOM2, which the Apollo firmware
# forwards to the host as its single CDC-ACM interface -- the same path moondancer
# logs on (`repos/cynthion/.../facedancer/top.py`, `uart0`).
#
# THE PINS ARE SHARED WITH JTAG AND THE SHARING IS ONE-SIDED. On r1.4 the `uart`
# resource is R14/T14, and those nets are JTAG TDI (R11) and TMS (T11); on the
# SAMD11 they are PA14/PA11, which its firmware moves between SERCOM2 and JTAG in
# `jtag_platform_init`/`_deinit` (`repos/apollo/firmware/src/boards/cynthion_d11/
# jtag.c:20,38`, called from `jtag_tap.c:150,183` on vendor requests 0xbf/0xbe).
#
# Nothing tells the FPGA that a JTAG session is in progress. There is no such
# signal on the board and none in the gateware, upstream or here. So the FPGA's
# only defence is the one the platform resource already provides: `dir="oe"` on
# tx, driven only while a byte is actually going out, with PULLMODE="UP" holding
# the idle mark. That is what luna_soc's UARTProvider does and it is what
# moondancer relies on.
#
# Which leaves a policy, and it belongs in the firmware: **never transmit
# unbidden on this port**. `target::ANNOUNCING` in firmware/cynthion-soc keeps the
# idle re-banner on the USB console only, so this port is electrically absent
# unless a human is typing on it -- and a human typing on the Apollo console is
# not simultaneously running `apollo jtag-scan`. moondancer does not take that
# precaution; it logs here whenever it feels like it, and contends.
APOLLO_UART_BASE = 0xf0000500

# The board's own peripherals -- LEDs, the power monitor's I2C bus, and the
# sideband payload -- behind ONE Wishbone window and one CSR bridge.
#
# Three separate windows would have been three more `WishboneCSRBridge`
# instances and three more comparators on the Wishbone decoder's address path,
# which is on the critical path this design has just finished recovering margin
# on (commit 18c1fa5). A `csr.Decoder` costs one comparator on an eight-bit
# address inside an already-decoded window, which is nothing, and it is what
# amaranth-soc provides the class for.
#
#   +0x00  gpio      16 bytes  amaranth_soc.gpio.Peripheral, 8 pins
#   +0x10  i2c        8 bytes  i2c_master.I2CMaster
#   +0x18  sideband   1 byte   sideband_csr.SidebandControl
#
# The sub-addresses are the peripherals' natural sizes and each window is
# aligned to its own size, which is what MemoryMap requires.
BOARD_BASE     = 0xf0000600
GPIO_BASE      = BOARD_BASE + 0x00
I2C_BASE       = BOARD_BASE + 0x10
SIDEBAND_BASE  = BOARD_BASE + 0x18

# What is on each GPIO pin.
#
# THE LEDS ARE NAMED BY COLOUR AND ONLY BY COLOUR. There are six of them in a
# row and their index is an implementation detail of the platform file; a bug
# report that says "LED 3" means nothing to the person holding the board, and
# has already cost time here. The order below is the physical order on the
# board and matches `LEDResources(pins="E13 C13 B14 A15 D12 C11")` in
# `cynthion_r1_4.py`.
#
# The platform declares them with `invert=True`, so they are active LOW on the
# pad and Amaranth's `PinsN` does the inversion: a 1 here lights the LED. That
# is the only place the active-low-ness appears, and neither the peripheral nor
# the firmware needs to know about it.
GPIO_RED     = 0
GPIO_ORANGE  = 1
GPIO_YELLOW  = 2
GPIO_GREEN   = 3
GPIO_BLUE    = 4
GPIO_VIOLET  = 5

# Pin 6: the power monitor's PWRDN, active low on the pad (`PinsN`), so a 1 here
# powers the PAC1954 DOWN. It is an output with no input path, and the GPIO
# peripheral only drives it once its mode says push-pull -- so the reset state
# is "not powered down" and the chip is available to the I2C bus without the
# firmware doing anything.
GPIO_PWRDN   = 6

# Pin 7: the USER button, `PinsN` on M14, so a 1 here means pressed. The only
# genuine *input* in this peripheral, and the reason the Input register is worth
# having at all.
GPIO_BUTTON  = 7

GPIO_PIN_COUNT = 8

# The I2C bus rate. See i2c_master.py for why 80 kHz rather than 100 kHz --
# briefly, at 100 kHz the repeated-START setup interval lands 0.7 us inside a
# standard-mode minimum, and a register read needs a repeated START.
I2C_SCL_HZ = 80_000

# 115200 8N1, which is what the SAMD11 side is configured for
# (`repos/apollo/firmware/src/boards/cynthion_d11/uart.c`) and what every terminal
# opens an Apollo tty at.
APOLLO_UART_BAUD = 115200

# Buffering, per transport, sized where the transport is chosen -- see
# stream_buffer.py for why this is not inside the 16550.
#
# USB CDC console. The endpoint takes one byte per packet (`serial.tx.last.eq(1)`,
# for latency: a console emits a line and goes quiet, so waiting to fill a 512-byte
# packet would hold the banner indefinitely). So the stall this covers is one host
# poll interval, which is a handful of bytes, and the 16550's own 16-byte FIFO
# already absorbs most of it.
#
# The predecessor used 1024, justified as "two 512-byte USB packets" -- a reason
# that stopped being true when the endpoint went to one byte per packet, and went
# on costing a DP16KD anyway. 16 entries of 8 bits map to distributed LUT RAM
# (TRELLIS_DPR16X4); 1024 map to a block RAM, of which this design uses 44 of 56.
CONSOLE_TX_DEPTH = 16
CONSOLE_RX_DEPTH = 16

# The Apollo serial line. 115200 is ~87 us per byte, which is four orders of
# magnitude slower than the CPU can produce them, so this is the one path here
# where deep buffering earns its keep: without it a `help` listing would spend its
# entire length inside the 16550's bounded write spin, dropping most of itself.
#
# 64 bytes is a line of shell output. Beyond that the CPU should wait, because a
# port nobody is draining is a port nobody is reading.
APOLLO_TX_DEPTH = 64

# Receive is a human at a keyboard. 16 is already more than anyone types between
# polls, and the 16550 has 16 of its own behind it.
APOLLO_RX_DEPTH = 16

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

# The HyperRAM boot port -- where the bootloader reads the staged firmware image from.
#
# Uncached like every other CSR here, and for the sharpest possible reason: `status.valid`
# is set by gateware while the CPU spins on it. A cached read would return the same line
# forever and the poll would never complete, giving a bootloader that hangs on a HyperRAM
# that is working perfectly.
BOOTRAM_BASE = 0xf0000400

# The interrupt controller: a standard RISC-V PLIC, in its own 4 MiB window.
#
# 4 MiB because that is the smallest window a spec-compliant PLIC fits in -- the
# claim register is at offset 0x200004 and the map is not negotiable, since the
# whole point of being standard is that a driver that has never heard of this SoC
# can find its way around. See ecp5-test/riscv/vexii_plic.py.
#
# 0xf0400000 rather than somewhere tidier: it must be 4 MiB aligned (the Wishbone
# decoder requires a window aligned to its size), it must be inside the `main=0`
# CSR region declared in vexii_cpu.DEFAULT_REGIONS -- a cached PLIC would return
# a stale pending word forever -- and it must clear the peripherals above, which
# all live below 0xf0001000.
#
# QEMU's `-M virt` puts its PLIC at 0x0c000000 with the 16550 on source 10. Same
# register map, different base, and that difference is two constants in
# firmware/cynthion-soc/src/target.rs -- which is what keeps src/plic.rs the same
# code on both targets, exactly as src/uart.rs already is.
PLIC_BASE = 0xf0400000

# Interrupt source numbers. 0 is reserved by the spec as "nothing pending", so
# these start at 1 and the order matches UART_BASES in src/target.rs.
#
# The console is the LOWER number deliberately. The PLIC breaks a priority tie by
# lowest source number, and if both ports are busy at equal priority the one a
# human is watching should be serviced first.
IRQ_CONSOLE = 1
IRQ_APOLLO = 2

# The I2C controller's completion interrupt. Third, so the two consoles keep the
# numbers -- and the tie-break priority -- they already had.
#
# The gateware raises it; the firmware does not enable it. `CTR.IEN` resets to
# zero, so this line is held low until something asks for it, and the firmware's
# I2C driver polls SR.TIP instead: a shell command that reads a register is
# synchronous by construction and has nothing else to do while it waits, so an
# interrupt would buy it nothing and cost a handler that has to be right. The
# wire is here so that a future driver -- one feeding the sideband's power
# payload from a timer, say -- does not need a bitstream change to use it.
IRQ_I2C = 3

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


class HelloSoC(Elaboratable):
    """VexRiscv, 64 KiB of block RAM, and a USB serial console."""

    def __init__(self, firmware):
        self.firmware = firmware

    def elaborate(self, platform):
        m = Module()

        # No `fast` domain: HyperRAMPHY (the non-DQS PHY, which is what BootRAM uses)
        # drives ODDRX1F/IDDRX1F, single-clock DDR primitives that produce double-rate
        # output from `sync` alone. Only HyperRAMDQSPHY needs an ECLK at 2x, and we do
        # not use it. Requesting `fast` anyway cost a PLL output, a global buffer, and
        # forced CLKOP_DIV to be even -- which needlessly restricts which sync
        # frequencies are reachable.
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

        # Two 16550s, identical apart from where the decoder puts them and what
        # their streams are attached to. `Uart16550` holds no module-level state
        # and knows nothing about its own address, so a third would be three more
        # lines.
        m.submodules.console = console = Uart16550()
        m.submodules.apollo_uart = apollo_uart = Uart16550()

        # Wishbone: RAM only. The consoles are CSR peripherals behind their own
        # bridges, added to the same decoder through wishbone-to-CSR bridges.
        decoder = Decoder(addr_width=30, data_width=32, granularity=8,
                          features={"cti", "bte", "err"})
        m.submodules.decoder = decoder

        # Kept on the instance, not just as a local.
        #
        # The memory map is the SoC's own description of itself, and two tools want to
        # read it without building a bitstream: `scripts/soc_generate_pac.py`, which
        # turns it into an SVD and then into Rust register definitions, and
        # `scripts/soc_diagram.py`. Both had to work around its being a local -- the PAC
        # generator simply failed with "could not find a memory map on the SoC", which is
        # why every peripheral address in the firmware is still hand-transcribed. That is
        # the class of error that once had firmware sending `0x9f << 24` because a comment
        # disagreed with the hardware.
        self.decoder = decoder
        decoder.add(ram.bus, addr=RAM_BASE, name="ram")

        from amaranth_soc.csr.wishbone import WishboneCSRBridge
        csr_bridge = WishboneCSRBridge(console.bus, data_width=32)
        m.submodules.csr_bridge = csr_bridge
        decoder.add(csr_bridge.wb_bus, addr=CONSOLE_BASE, name="console")

        apollo_csr_bridge = WishboneCSRBridge(apollo_uart.bus, data_width=32)
        m.submodules.apollo_csr_bridge = apollo_csr_bridge
        decoder.add(apollo_csr_bridge.wb_bus, addr=APOLLO_UART_BASE,
                    name="apollo_uart")

        # The interrupt controller, and the two UART lines into it.
        #
        # Both 16550s already have an `irq` output; before this it went nowhere
        # and both consoles were polled round-robin by the firmware. The lines
        # are LEVELS, held for as long as the condition holds, which is what the
        # PLIC's gateway expects -- see the docstrings in vexii_plic.py and
        # uart16550.py for why an edge here would lose everything after the
        # first burst.
        # Indexed by the IRQ_* constants rather than concatenated in order, so
        # the source numbers the firmware writes into the PLIC's enable register
        # and the wires they select are the same names in the same file. A Cat()
        # here would encode them positionally and silently renumber everything
        # if a third source were ever inserted in the middle.
        # The board's peripherals: LEDs and two other pins on a GPIO block, the
        # power monitor's I2C bus, and the sideband payload. One CSR decoder in
        # front of all three, one Wishbone window -- see BOARD_BASE.
        #
        # The GPIO peripheral is `amaranth_soc.gpio`, upstream and unmodified. A
        # bespoke LED register would have been fewer gates and would have had to
        # be documented, tested and explained; this one is already all three.
        m.submodules.board_gpio = board_gpio = gpio.Peripheral(
            pin_count=GPIO_PIN_COUNT, addr_width=4, data_width=8)

        m.submodules.i2c = i2c = I2CMaster()
        m.submodules.sideband_ctrl = sideband_ctrl = SidebandControl()

        board_csr = csr.Decoder(addr_width=5, data_width=8)
        m.submodules.board_csr = board_csr
        board_csr.add(board_gpio.bus,    addr=GPIO_BASE     - BOARD_BASE,
                      name="gpio")
        board_csr.add(i2c.bus,           addr=I2C_BASE      - BOARD_BASE,
                      name="i2c")
        board_csr.add(sideband_ctrl.bus, addr=SIDEBAND_BASE - BOARD_BASE,
                      name="sideband")

        board_bridge = WishboneCSRBridge(board_csr.bus, data_width=32)
        m.submodules.board_bridge = board_bridge
        decoder.add(board_bridge.wb_bus, addr=BOARD_BASE, name="board")

        m.submodules.plic = plic = Plic(sources=3)
        m.d.comb += [
            plic.sources[IRQ_CONSOLE].eq(console.irq),
            plic.sources[IRQ_APOLLO].eq(apollo_uart.irq),
            plic.sources[IRQ_I2C].eq(i2c.irq),
        ]

        # The same three lines, keyed by the decoder window each peripheral lives
        # in, so `scripts/soc_generate_pac.py` can put an <interrupt> element on
        # the right peripheral in the SVD.
        #
        # Here rather than at the top of the file, and immediately below the
        # wiring it describes: a source number that is written down somewhere else
        # is a source number that can disagree with the wire, and a firmware that
        # enables the wrong PLIC source produces a console that never interrupts
        # with nothing to see anywhere. The names are the `name=` arguments to
        # `decoder.add()` and `board_csr.add()`, joined -- see `walk()` in the
        # generator for why the board's three sub-windows are named that way.
        self.interrupt_sources = {
            "console":     IRQ_CONSOLE,
            "apollo_uart": IRQ_APOLLO,
            "board_i2c":   IRQ_I2C,
        }

        plic_bridge = WishboneCSRBridge(plic.bus, data_width=32)
        m.submodules.plic_bridge = plic_bridge
        decoder.add(plic_bridge.wb_bus, addr=PLIC_BASE, name="plic")

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
            flash_ila.dq_o0       .eq(flash_phy.o_dq_o0),
        ]

        flash_ila_bridge = WishboneCSRBridge(flash_ila.bus, data_width=32)
        m.submodules.flash_ila_bridge = flash_ila_bridge
        decoder.add(flash_ila_bridge.wb_bus, addr=FLASH_ILA_BASE,
                    name="flash_ila")

        # HyperRAM, and the JTAG path Apollo stages firmware through.
        #
        # This is what makes a firmware change cost seconds instead of a ~60 s
        # resynthesis: the image goes into HyperRAM over JTAG, and a resident
        # bootloader copies it into block RAM. See ecp5-test/riscv/vexii_bootram.py.
        from vexii_bootram import BootRAM

        m.submodules.bootram = bootram = BootRAM()
        bootram_bridge = WishboneCSRBridge(bootram.port.bus, data_width=32)
        m.submodules.bootram_bridge = bootram_bridge
        decoder.add(bootram_bridge.wb_bus, addr=BOOTRAM_BASE, name="bootram")

        # No external reset source: the CPU reboots itself by jumping to `_start`, which
        # re-runs riscv-rt's init. Held reset only mattered while Apollo staged images
        # over JTAG, and that path is gone.
        m.d.comb += cpu.ext_reset.eq(0)

        # The machine external interrupt, from the PLIC.
        #
        # This input existed and was connected to nothing -- an undriven `In`
        # port of a Component reads as zero, so the SoC had an interrupt path
        # that could never fire and nothing said so. That is why the firmware
        # polled.
        #
        # The other two are tied off EXPLICITLY rather than left undriven, for
        # the same reason: "0 because nobody wired it" and "0 because there is
        # deliberately no source" look identical in the netlist and completely
        # different when something stops working.
        #
        #   irq_timer     needs a CLINT (mtimecmp). The CPU has --with-rdtime,
        #                 so `rdtime` counts, but nothing compares it against a
        #                 target. RTIC's monotonic timer will want this.
        #   irq_software  needs a CLINT msip register. No use for one yet.
        m.d.comb += [
            cpu.irq_external.eq(plic.irq_out),
            cpu.irq_timer.eq(0),
            cpu.irq_software.eq(0),
        ]

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

        # A flip-flop between the arbiter and the decoder, on the return path.
        #
        # Without it one clock edge has to carry the grant register through the
        # address mux, the decoder's window compare, every subordinate's
        # acknowledge, the gather back, and then VexiiRiscv's PMA check and
        # commit -- 16.45 ns measured, of which 13.64 ns was wire. That is 60.8
        # MHz against a 60 MHz constraint: it passes, and a placement run that
        # went slightly differently would not.
        #
        # The cost is one cycle per bus beat and it is paid only by traffic that
        # reaches the bus at all; a cache hit never does. See wishbone_pipe.py
        # for the duplicate-strobe hazard this handles, which is the reason it
        # is not simply a register on `ack`, and scripts/soc_bus_sim.py for the
        # measurement of both.
        m.submodules.bus_pipe = bus_pipe = RegisteredResponse(
            addr_width=30, data_width=32, granularity=8,
            features={"cti", "bte", "err"})
        wiring.connect(m, arbiter.bus, bus_pipe.intr_bus)
        wiring.connect(m, bus_pipe.sub_bus, decoder.bus)

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

        # The elastic buffers between the 16550 and the USB endpoint.
        #
        # ASYNC, and that is not optional: the CPU and its 16550 run in `sync`, the
        # endpoint runs in `usb`, and those are different clocks the moment SYNC_MHZ
        # is not 60. A synchronous FIFO here worked only because both happened to be
        # 60 MHz; raising sync to 80 produced a stream with correct counter VALUES
        # and dropped CHARACTERS -- `tic 00000`, `tck 000001` -- because bytes were
        # lost in transit while the arithmetic that produced them was untouched.
        #
        # The 16550 keeps its own 16-byte FIFOs and knows nothing about any of this.
        # See stream_buffer.py.
        m.submodules.console_tx_buf = console_tx_buf = StreamBuffer(
            depth=CONSOLE_TX_DEPTH, i_domain="sync", o_domain="usb")
        m.submodules.console_rx_buf = console_rx_buf = StreamBuffer(
            depth=CONSOLE_RX_DEPTH, i_domain="usb", o_domain="sync")

        wiring.connect(m, console.source, console_tx_buf.sink)
        wiring.connect(m, console_rx_buf.source, console.sink)

        # Console -> host.
        m.d.comb += [
            serial.tx.payload.eq(console_tx_buf.source.payload),
            serial.tx.valid.eq(console_tx_buf.source.valid),
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

            console_tx_buf.source.ready.eq(serial.tx.ready),

            # Host -> CPU. This used to be `serial.rx.ready.eq(1)`, which accepted every
            # byte and threw it away, so typing at the console did nothing at all.
            console_rx_buf.sink.payload.eq(serial.rx.payload),
            console_rx_buf.sink.valid.eq(serial.rx.valid),
            serial.rx.ready.eq(console_rx_buf.sink.ready),
        ]

        # ---- the Apollo-facing serial port ---------------------------------
        #
        # The same peripheral, on a transport that genuinely is a UART. The 16550
        # supplies no baud rate -- it is a byte pipe by design -- so the bit timing
        # lives here, in the module that chose the wire.
        #
        # R14/T14 are shared with JTAG TDI/TMS. `dir="oe"` on tx, the idle
        # qualifier inside `SerialLine`, and the policy of never transmitting
        # unbidden are the whole of the mitigation; see the comment on
        # APOLLO_UART_BASE and the docstring of serial_line.py.
        apollo_pins = platform.request("uart", 0)

        # divisor = clock / baud, in whole `sync` cycles. At 60 MHz and 115200 that
        # is 520, an error of 0.03% -- a UART tolerates about 2%, and the error
        # scales with the clock, so a design that raises SYNC_MHZ and leaves this
        # expression alone stays correct by construction. Hardcoding 520 would not.
        #
        # SerialLine rather than a bare AsyncSerial. This used to be an
        # AsyncSerial wired straight to the pads here, and all three of the ways
        # that was wrong are issue #113: the receive pad reached the FSM with no
        # synchroniser, framing errors were delivered as characters, and the
        # output enable came off `~tx.rdy`, which falls at the start of the stop
        # bit rather than after it. serial_line.py has the full account.
        m.submodules.apollo_line = apollo_line = SerialLine(
            divisor=int(SYNC_MHZ * 1e6 // APOLLO_UART_BAUD), data_bits=8)

        # 115200 is four orders of magnitude slower than the CPU, so this is the
        # path where a deep transmit buffer earns its keep. Same domain both sides,
        # so this is a plain synchronous FIFO -- the crossing is a property of the
        # USB transport, not of buffering.
        m.submodules.apollo_tx_buf = apollo_tx_buf = StreamBuffer(
            depth=APOLLO_TX_DEPTH)
        m.submodules.apollo_rx_buf = apollo_rx_buf = StreamBuffer(
            depth=APOLLO_RX_DEPTH)

        wiring.connect(m, apollo_uart.source, apollo_tx_buf.sink)
        wiring.connect(m, apollo_rx_buf.source, apollo_uart.sink)
        wiring.connect(m, apollo_tx_buf.source, apollo_line.sink)
        wiring.connect(m, apollo_line.source, apollo_rx_buf.sink)

        m.d.comb += [
            # The pad, and nothing else. SerialLine owns the synchroniser, the
            # idle qualifier and the output enable -- which is the point of it
            # being a module rather than nine lines of comb here.
            apollo_line.rx_i.eq(apollo_pins.rx.i),
            apollo_pins.tx.o.eq(apollo_line.tx_o),
            apollo_pins.tx.oe.eq(apollo_line.tx_oe),
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
        #
        # THESE ARE NOW THE DEFAULT RATHER THAN THE ONLY THING. The GPIO
        # peripheral above can take any LED, one at a time, by putting that pin
        # in push-pull mode -- and until it does, the diagnostic below drives it,
        # exactly as before. `amaranth_soc.gpio` calls that mode INPUT_ONLY and
        # documents it as "the pin output is disabled", which is precisely the
        # claim being made: while the CPU is not driving, something else is.
        #
        # The reset value of Mode is INPUT_ONLY for every pin, so a bitstream
        # whose firmware never runs still lights the LEDs with the fabric's own
        # account of itself. That is the whole reason these six exist.
        led_pads = [platform.request("led", n).o for n in range(6)]
        leds = Signal(6)

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

        # Where a byte gets to, as two sticky HANDSHAKE flags.
        #
        # `ever_console` above is only `valid`, which a byte sitting in a FIFO that
        # nothing drains asserts forever -- so it says "the CPU wrote one", not "it
        # went anywhere". These two say where it went, and between them they split
        # the path at the only two places it can stop:
        #
        #   state[0]  the 16550 handed a byte to the elastic buffer (sync side)
        #   state[1]  the elastic buffer handed a byte to the USB endpoint (usb side)
        #
        # Both zero with `events` set means the transmit chain is stalled at its
        # first stage; [0] set and [1] clear means the domain crossing or the
        # endpoint is the problem; both set means the bytes left the FPGA and the
        # fault is on the host side of the wire. That is three distinct diagnoses
        # from a link that works when USB does not, which is the entire reason the
        # sideband is in this design.
        ever_buffered = Signal()
        with m.If(console.source.valid & console.source.ready):
            m.d.sync += ever_buffered.eq(1)

        # Set in `usb`, read in `sync`. Sticky and one-directional, so the only
        # hazard is sampling the 0->1 edge, which FFSynchronizer covers.
        usb_took = Signal()
        with m.If(serial.tx.valid & serial.tx.ready):
            m.d.usb += usb_took.eq(1)
        ever_usb = Signal()
        m.submodules.usb_took_sync = FFSynchronizer(usb_took, ever_usb,
                                                    o_domain="sync")

        m.d.comb += [
            # The fabric's account, which the CPU may override one register
            # write at a time -- see sideband_csr.py. `reconfigured` has never
            # had anything to report from this design, so the fabric side of it
            # is zero and the firmware side is the only one that can set it.
            sideband_ctrl.fabric_state.eq(Cat(ever_buffered, ever_usb)),
            sideband_ctrl.fabric_events.eq(ever_console),
            sideband_ctrl.fabric_error.eq(ever_errored),
            sideband_ctrl.fabric_reconfigured.eq(0),

            sideband.state.eq(sideband_ctrl.state),
            sideband.events.eq(sideband_ctrl.events),
            sideband.error.eq(sideband_ctrl.error),
            sideband.reconfigured.eq(sideband_ctrl.reconfigured),

            leds.eq(Cat(ever_errored,          # red    -- error, latched
                        ever_fetched,          # orange -- fetching
                        ever_io,               # yellow -- I/O bus reached
                        heartbeat_on,          # green  -- heartbeat, flashing
                        ever_console,          # blue   -- console data queued
                        serial.connect)),      # violet -- USB up
        ]

        # ---- the board GPIO pins --------------------------------------------
        #
        # Six LEDs, one power-monitor control output, and the USER button.
        #
        # The LEDs and PWRDN are `dir="o"` resources: the pad has an `o` and no
        # `oe`, so the GPIO peripheral's output enable cannot reach the pin and
        # is used here as an OWNERSHIP bit instead. Push-pull means "the CPU is
        # driving this one"; anything else leaves it to the fabric. That is the
        # same meaning the peripheral's own documentation gives the mode, and it
        # is why the LED handover needed no new register.
        for index in range(6):
            m.d.comb += led_pads[index].eq(
                Mux(board_gpio.pins[index].oe,
                    board_gpio.pins[index].o,
                    leds[index]))
            # Input reads back the value ON THE NET, not the Output register --
            # so it answers "what is this LED doing" whichever side is driving
            # it, which is the question worth asking from a shell that cannot
            # see the board. It is not a measurement: nothing on an ECP5 reads
            # an output pad back, and the platform's `PinsN` inversion happens
            # below this point, so what is read is the logical value rather than
            # the pad voltage.
            m.d.comb += board_gpio.pins[index].i.eq(led_pads[index])

        power_monitor = platform.request("power_monitor", 0)

        # PWRDN is active low on the pad. The GPIO block drives it only in
        # push-pull mode, so the reset state is a 0 here, a 1 on the pad, and a
        # PAC1954 that is running -- which is what the I2C bus below needs.
        #
        # `slow` and `gpio` on this resource are left as inputs (o and oe both
        # default to 0 on a `dir="io"` pin). `slow` selects the chip's
        # low-bandwidth sampling mode and `gpio` is its general-purpose pin;
        # neither is needed to read a measurement, and driving a pin whose
        # purpose has not been established is how a board gets damaged.
        m.d.comb += power_monitor.pwrdn.o.eq(
            board_gpio.pins[GPIO_PWRDN].o & board_gpio.pins[GPIO_PWRDN].oe)
        m.d.comb += board_gpio.pins[GPIO_PWRDN].i.eq(power_monitor.pwrdn.o)

        button = platform.request("button_user", 0)
        m.d.comb += board_gpio.pins[GPIO_BUTTON].i.eq(button.i)

        # ---- the power monitor's I2C bus ------------------------------------
        #
        # One bus, two wires, and whatever is on it. `scl` is `dir="o"` on this
        # platform, so it is driven push-pull and nothing on the bus may stretch
        # the clock; `sda` is `dir="io"` and is driven properly open-drain. See
        # i2c_master.py for what that rules out.
        m.d.comb += [
            power_monitor.scl.o.eq(i2c.scl_o),
            power_monitor.sda.o.eq(i2c.sda_o),
            power_monitor.sda.oe.eq(i2c.sda_oe),
            i2c.sda_i.eq(power_monitor.sda.i),
        ]

        return m


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--program", action="store_true")
    parser.add_argument("--placeholder", type=Path,
                        help="build with this random image as block RAM init, so real "
                             "firmware can be swapped in later without resynthesis")
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

    # --placeholder builds a bitstream whose block RAM holds a known RANDOM image
    # instead of firmware, so `scripts/soc_swap_firmware.py` can substitute real
    # firmware later with ecpbram -- one second, no synthesis.
    #
    # It has to be random rather than the real image. ecpbram locates the old contents
    # BY VALUE, and a real firmware image is ~87% zeroes; those zeroes also fill every
    # unused BRAM tile on the die, so the pattern is not unique and ecpbram refuses with
    # "Conflicting from pattern". A random image appears exactly once.
    if args.placeholder:
        if not args.placeholder.exists():
            print(f"no placeholder at {args.placeholder}")
            print("generate one with:")
            print(f"  ecpbram -g {args.placeholder} -w 32 -d {RAM_SIZE // 4} -s 1")
            return 1
        raw = bytes.fromhex("".join(
            # The hex file is one big-endian word per line; block RAM init is a list of
            # integers, so parse rather than concatenate.
            f"{int(line, 16):08x}"
            for line in args.placeholder.read_text().split()))
        print(f"placeholder image: {len(raw)} bytes "
              f"(swap real firmware in with scripts/soc_swap_firmware.py)")

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

    build_dir = ROOT / "tmp" / "vexii_hello" / "build"

    CynthionPlatformRev1D4().build(
        HelloSoC(firmware=words),
        do_program=args.program,
        build_dir=str(build_dir))

    # Record the exact BRAM contents alongside the bitstream.
    #
    # This is what `scripts/soc_swap_firmware.py` matches against: ecpbram finds the old
    # contents BY VALUE and substitutes new ones, replacing firmware in a built bitstream
    # in about a second instead of a ~60 s resynthesis. It can only do that if it is
    # handed exactly what was synthesised, so this is written here rather than
    # reconstructed later -- a reconstruction differing by one word would simply fail to
    # match.
    #
    # Padded to the full RAM, because that is the geometry that ends up in the BRAM:
    # `words` covers only the image, while the initialiser covers every location.
    hex_words = words + [0] * (RAM_SIZE // 4 - len(words))
    (build_dir / "firmware.hex").write_text(
        "".join(f"{word:08x}\n" for word in hex_words))

    return 0


if __name__ == "__main__":
    sys.exit(main())
