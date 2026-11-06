#!/usr/bin/env python3
#
# A RISC-V core on block RAM, printing over USB CDC-ACM on the AUX port.
# SPDX-License-Identifier: BSD-3-Clause

"""
A VexiiRiscv SoC on block RAM: a core, memory, and a way to see it run.

    ./gateware/soc/top.py --build
    ./gateware/soc/top.py --build --program

Block RAM is single-cycle and needs no cache, bus wrapper or latency tuning, so
the only things that can be wrong are the CPU, its reset vector and the
peripheral it writes to.

## Two consoles, one register map

    index  peripheral   transport                       appears as
    0      Uart16550    USB CDC-ACM on the AUX port     /dev/ttyACM*
    1      Uart16550    async serial on R14/T14         Apollo's CDC

Same NS16550A register map, same driver, different transport -- which is the
point of a standard part. `peripherals/serial_line.py` is the PHY behind index 1.

  * **USB carries the primary console** because R14/T14 are the same wires as
    JTAG TDI/TMS, so a design driving them competes with the thing loading its
    own bitstream. The CDC gateware measures 195.4 Mbps loopback
    (`../../docs/usb-performance.md`).
  * **A standard 16550, not a bespoke peripheral**, because LSR at +5 cannot
    share a 32-bit word with RBR at +0. See `peripherals/uart16550.py`, and
    `../../docs/architecture.md` for what that replaced.

## Interrupts

Every board signal goes to `cpu/intc.py` -- pending bits and enables, per-source
level or edge -- whose output is the CPU's single machine external interrupt.
The design and the source table: `../../docs/soc-interrupts.md`.

The machine timer and software interrupts come from a standard RISC-V CLINT
(`cpu/clint.py`), comparing against the same counter `rdtime` reads. A 1 ms
tick is `mtimecmp += period` in the handler; `firmware/cynthion-soc/src/timer.rs`
is the driver.

QEMU's `-M virt` has a CLINT, so `src/timer.rs` compiles unchanged for both. Its
interrupt controller is a PLIC, which `src/intc.rs` drives as pending bits and
enables and never claims -- the one `#[cfg]` in that file.

## Two ways to load firmware

    path                     needs                       script
    -----------------------  --------------------------  -----------------------
    JTAG ER1 into HyperRAM   a configured FPGA           `soc_jtag_stage.py`
    console into HyperRAM    a running shell             `soc_payload.py`

The JTAG sink holds the CPU in reset while it writes, so it works on a board whose
console is wedged -- which the console path by definition cannot. Both leave the same
header for the bootloader. See `bus/jtag_stage.py`.

## Board peripherals

GPIO (six LEDs, PWRDN, the USER button), a multiplexed I2C master reaching the
PAC1954 and both FUSB302Bs, a ULPI register window on the TARGET PHY, the
sideband link to Apollo, and memory-mapped SPI flash.

Peripheral base addresses are generated from this SoC's own memory map by
`scripts/soc/soc_generate_pac.py`; `scripts/check.py` fails if firmware writes one
as a literal.
"""

import argparse
import os
import sys
from pathlib import Path

from amaranth                       import (Cat, ClockSignal, DomainRenamer,
                                            Elaboratable, Module, Mux,
                                            ResetSignal, Signal)
from amaranth.lib                   import wiring
from amaranth.lib.cdc               import FFSynchronizer

# Not LunaECP5DomainGenerator: it clocks `sync` at 60 MHz and offers only 60/120/240
# elsewhere, so a speed ladder can only step in factors of two. Nothing in the hardware
# requires that -- the PLL runs a 480 MHz VCO and each output divides it, so 80, 96, 100
# and the rest are all reachable. This one takes an arbitrary frequency, derives real
# dividers with ecppll, and reports what it actually produced.
from clocks import SocClocks
from peripherals.clock_monitor import ClockMonitor

# Every environment variable below that changes what this file elaborates, and
# the build directory derived from them. One table, shared with `soc_run.py`'s
# bitstream cache -- see `variant.py`.
import variant

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import usb_ids

# `amaranth_soc` is the installed package; luna_soc/__init__.py only falls back
# to its vendored copy when the real one is missing.
# Use the bare `amaranth_soc` name throughout: the vendored path would give a
# different wishbone.Interface class, which Decoder.add() rejects.
from soc.peripherals.block_ram      import BlockRAM

import sys as _sys
_sys.path.insert(0, str(Path(__file__).resolve().parent))
_sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from board.cynthion_r1_4 import LEDS as _BOARD_LEDS
import cpu.cpu as vexii_cpu
from cpu.cpu import VexiiRiscv
from bus.jtag_stage import JTAGStager, UserJTAG
from peripherals.uart16550 import Uart16550
from cpu.intc import Interrupts
from cpu.clint import Clint
from peripherals.serial_line import SerialLine
from peripherals.i2c_master import I2CMaster, prescale_for
from peripherals.sideband_csr import SidebandControl
from peripherals.vbus_csr import VbusControl
from peripherals.fabric_status import FabricStatus
from peripherals.ulpi_window import UlpiRegisters
from peripherals.usb_host import UsbPort
from peripherals.i2c_mux import (I2CBusMux, BUS_TARGET_C as I2C_MUX_TARGET_C,
                     BUS_AUX_C as I2C_MUX_AUX_C,
                     BUS_POWER_MONITOR as I2C_MUX_POWER)
from peripherals.stream_buffer import StreamBuffer
from bus.wishbone_pipe import RegisteredResponse
from bus.fault         import BusFault
from peripherals.hyperram import HyperRAM
from peripherals.flash import Flash, SCK_MHZ as FLASH_SCK_MHZ, xip_worst_sync_cycles

from amaranth_soc                   import csr, gpio, wishbone
from amaranth_soc.wishbone          import Decoder

ROOT = Path(__file__).resolve().parent.parent.parent

# This variant's build directory, at import time because the CPU's netlist is
# emitted during elaboration and has to land inside it -- see `BUILD_DIR` use in
# `AwtoSoc.elaborate` and #306.
BUILD_DIR = variant.build_dir(ROOT)

# 32 KiB at address zero. The reset vector is 0x00000000, so the bootloader's
# entry point must be the first instruction.
#
# Was 64 KiB to match moondancer's allocation. Block RAM is the binding resource
# -- 50 of 56 EBRs, this region about 32 of them -- and the firmware puts only
# 12,824 B here (.data 292 + .bss 12,532); the rest is stack. Must be a power of
# two -- `luna_soc`'s blockram refused anything else, which is why
# `peripherals/block_ram.py` exists.
RAM_BASE = 0x00000000
RAM_SIZE = 32 * 1024

# Where the bootloader stops and the image begins.
#
# The decoder below sees ONE window of RAM_SIZE at RAM_BASE. This split is a
# linker fiction and costs the fabric nothing: no second peripheral, no second
# address comparison on the decode path -- which matters, because that path is
# what commit 18c1fa5 had to register to recover Fmax. Cutting the address space
# here is free at any boundary, and no DP16KD granularity applies to it.
#
# What it buys: `firmware/cynthion-boot` is resident at 0x0 and changes rarely,
# and everything that grows -- the shell, the commands, the drivers -- lives
# above it and is replaceable in seconds by staging over it.
#
# 1 KiB because the bootloader measures 492 bytes and its deepest call chain
# holds 80. Must match BOOT/IMAGE in firmware/cynthion-boot/memory.x, RAM in
# firmware/cynthion-soc/memory.x, PAYLOAD in firmware/cynthion-payload/memory.x,
# MAX_IMAGE in firmware/cynthion-soc/src/hyperram.rs and PAYLOAD_SIZE in
# scripts/soc/soc_payload.py. `scripts/soc/soc_generate_pac.py --check` reads all of
# them and fails on a disagreement.
IMAGE_ORIGIN = 0x00000400

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
#   +0x18  sideband   4 bytes  sideband_csr.SidebandControl
#   +0x1c  ulpi       4 bytes  ulpi_window.UlpiRegisters, on target_phy
#   +0x20  i2c_mux    2 bytes  i2c_mux.I2CBusMux
#   +0x24  vbus       1 byte   vbus_csr.VbusControl
#   +0x40  fabric     8 bytes  fabric_status.FabricStatus
#
# The sub-addresses are the peripherals' natural sizes and each window is
# aligned to its own size, which is what MemoryMap requires. The decoder is 128
# bytes rather than the 32 the first three fit in, because a peripheral added
# later should not have to move an existing one -- and moving one changes every
# address in the generated PAC at once.
#
# `fabric` is not a board peripheral and is here anyway: it is the one window
# whose whole purpose is to be readable, and a second Wishbone window for it
# would be another comparator on the address path the paragraph above is about.
# The decoder it goes in is inside an already-decoded window and costs one more
# address bit.
BOARD_BASE     = 0xf0000600
GPIO_BASE      = BOARD_BASE + 0x00
I2C_BASE       = BOARD_BASE + 0x10
SIDEBAND_BASE  = BOARD_BASE + 0x18
ULPI_BASE      = BOARD_BASE + 0x1c
I2C_MUX_BASE   = BOARD_BASE + 0x20
VBUS_BASE      = BOARD_BASE + 0x24
# 8 bytes now that the build identity is in USERCODE (#447); the base is kept
# where it was so nothing else in the map moves.
FABRIC_BASE    = BOARD_BASE + 0x40
# What the clocks MEASURE at. The expectation it is checked against is
# `target::TIME_HZ` on the firmware side and the build record on the host's.
CLOCKS_BASE    = BOARD_BASE + 0x60

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
# The LED colour map is the platform's, not restated here. `gateware/board/
# cynthion_r1_4.py` owns it -- see `LEDS` there for why the colour cannot come
# from the toolchain and must come from the schematic (#415).
GPIO_VIOLET, GPIO_BLUE, GPIO_GREEN, GPIO_YELLOW, GPIO_ORANGE, GPIO_RED = (
    index for index, _ball, _sch, _colour in _BOARD_LEDS)

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

# The I2C bus rate, at the rated max of every part on it: two FUSB302Bs and a
# PAC1954, all Fast-mode Plus, each on its own mux segment behind 2.2k pull-ups.
# Slot arithmetic and the rise-time limit that binds: `i2c_master.py`. #269.
I2C_SCL_HZ = 1_000_000

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
# Do not size this for USB packets. 16 entries of 8 bits map to distributed LUT
# RAM (TRELLIS_DPR16X4); 1024 map to a block RAM, of which this design uses 42 of
# 56. A depth justified as "two 512-byte USB packets" costs a DP16KD and buys
# packet pipelining on a path that sends one byte per packet.
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

# Whether the flash window is cached. False routes it to the uncached `iobus`:
# every load becomes a full command/address/dummy/data transaction with no line
# reuse, which is slow and simple. See the region list for why the first
# .rodata-from-flash test wants simple.
#
# True once `.text` lives here, for two reasons worth keeping separate.
#
# The HARD requirement is `exe=1`, which permits instruction fetch from the
# window. `main` and `exe` are independent flags in VexiiRiscv's region syntax --
# `main=0,exe=1` is expressible -- but the line below writes both from this one
# flag, so today they move together. That coupling is this file's, not the CPU's.
#
# The PRACTICAL reason is that uncached instruction fetch would make every single
# instruction a complete SPI command/address/dummy/data transaction with no line
# reuse. For data that was merely slow, which is why stage one ran `.rodata`
# uncached on purpose: a first test should remove variables, not preserve
# performance. For code it is not a trade-off anyone would take.
#
# Whether `main=0,exe=1` would fetch at all on this core is untested here, and is
# not worth testing: there is no configuration in which it is the one we want.
FLASH_CACHED = True

# HyperRAM (#588): registers + a 4 KiB data window, both in the uncached CSR region.
# No memory-mapped HyperRAM: the CPU reaches it through the window and COPY.
HYPERRAM_BASE = 0xf0000800
HYPERRAM_WINDOW_BASE = 0xf0010000

# The flash command port's registers (`peripherals/flash.py`, #593) and its 256 B
# data buffer (reads: received bytes; writes: bytes to send). Both in the uncached
# CSR region: the status changes under the CPU and the buffer between commands.
FLASH_CSR_BASE = 0xf0000100
FLASH_BUF_BASE = 0xf0000200

# The interrupt controller: two registers, `cpu/intc.py`.
#
# 32 bytes of Wishbone -- six CSR bytes at the bridge's stride of 4. Inside the
# `main=0` CSR region declared in vexii_cpu.DEFAULT_REGIONS, because a cached
# controller would return a stale pending word forever, and aligned to its own
# size, which the Wishbone decoder requires of every window.
#
# 0xc00: the board block ends at 0x680 and the HyperRAM CSRs at 0x880.
INTC_BASE = 0xf0000c00

# The role mux and byte stream for target_phy (`peripherals/usb_host.py`):
# 32 bytes of CSR. 0xf0001000 is free (INTC 0xc00, HyperRAM 0x800..0x87f) and
# inside the uncached CSR region; the FIFO pop registers need that.
USB_HOST_BASE = 0xf0001000

# The machine timer and software interrupts: a standard RISC-V CLINT, in its own
# 64 KiB window. The offsets inside are not negotiable (mtime is at 0xbff8), the
# window must be aligned to its size, and it must be inside the `main=0` CSR
# region.
#
# QEMU's `-M virt` puts its CLINT at 0x02000000 (`clint@2000000` in the device
# tree), so again the difference is one constant in
# firmware/cynthion-soc/src/target.rs and src/timer.rs is the same code on both.
CLINT_BASE = 0xf0800000

# Interrupt source numbers, and each source's trigger.
#
# The numbering is `../../docs/soc-interrupts.md`'s, which lists seventeen
# sources and is the authority. Numbers with no hardware yet are left as gaps:
# `cpu/intc.py` ties them low, so wiring one up later renumbers nothing.
#
# Bit 0 is unused: the doc's table starts at 1, and bit position is the source
# number.
#
# No order here and no tie-break. The controller has no priority: every enabled
# pending source is serviced in the one trap, and which TASK runs first is
# RTIC's, on `msip`.
IRQ_CONSOLE = 1
IRQ_APOLLO = 2

# The I2C controller's completion interrupt.
#
# The gateware raises it; the firmware does not enable it. `CTR.IEN` resets to
# zero, so this line is held low until something asks for it, and the firmware's
# I2C driver polls SR.TIP instead: a shell command that reads a register is
# synchronous by construction and has nothing else to do while it waits, so an
# interrupt would buy it nothing and cost a handler that has to be right. The
# wire is here so that a future driver -- one feeding the sideband's power
# payload from a timer, say -- does not need a bitstream change to use it.
IRQ_I2C = 3

# The two FUSB302Bs' `int` lines, ONE SOURCE EACH.
#
# A SHARED LEVEL MUST BE CLEARED ON EVERY ASSERTING DEVICE before the source is
# re-enabled, or the line stays high and the interrupt re-fires immediately -- a
# storm that presents as a hung CPU (`docs/chips/fusb302b-type-c.md`). One
# source per device leaves nothing to miss, and a deferred TARGET does not blind
# AUX. These were one source until #135; `docs/architecture.md` decision 8.
#
# The handler still defers: clearing is ~1 ms of I2C on the controller the
# foreground also uses, so `src/irq.rs` masks and hands off to a task.
IRQ_TYPE_C_TARGET = 4
IRQ_TYPE_C_AUX = 5

# target_phy's USB host/raw port (`peripherals/usb_host.py`): RX FIFO not empty, or
# a transfer finished, or a line-state/error event, each gated by IRQ_EN.
# LEVEL: it holds until the FIFO is drained or EVENTS is cleared.
# Number 10 is `docs/soc-interrupts.md`'s "USB3343 TARGET link event".
IRQ_TARGET_USB = 10

# The PAC1954's ALERT, on GPIO/ALERT2 -- ECP5 ball D6, U1 pin 15, pulled up by
# R86 (10k). #270.
#
# EDGE, on the pad's falling edge. Threshold alerts latch low (DS20006539B
# s5.16) and would survive as a level, but conversion-complete is a 5 us pulse
# that sets no status bit (s5.16.1) -- as a level source it is silently lost.
# #514.
#
# `gpio` is an input here and `oe` stays 0; R86 holds the line high when nothing
# is asserting, which is what an open-drain ALERT requires.
IRQ_POWER_ALERT = 6

# The two DPO2036s' `FAULTB` pins, one source each -- TARGET U13, AUX U14, both
# pin 6. #506.
#
# EDGE. The part auto-recovers: `FAULTB` is low for ~30-42 ms per event and then
# released, so a repeating fault is a train of assertions that aliases against
# any periodic sampler. The 50 ms poll in `src/typec.rs` can miss a whole event;
# a latch cannot. `docs/chips/dpo2036-cc-sbu-protection.md`.
#
# `PinsN` in the platform, so a 1 on the port means asserted and the edge is a
# rise. Synchronised in `peripherals/i2c_mux.py`, which owns these pins.
IRQ_TARGET_FAULT = 8
IRQ_AUX_FAULT = 9

# The USER button, ball M14. `PinsN`, so a 1 means pressed and the edge is a
# rise.
#
# NOT debounced in fabric: one press raises several interrupts and software
# settles it. A press is a human event, so the handler has milliseconds of
# slack; debouncing here would be a timer per source for something the CPU can
# do in a few instructions.
IRQ_BUTTON = 13

# The PLL losing lock.
#
# EDGE, on the FALLING edge of `locked`, so the source is the loss and not the
# state. `ClockMonitor` keeps the level as a CSR bit for anything that wants to
# ask; this is what says so without being asked. `locked` is low out of reset,
# so coming up produces a rise and no interrupt.
IRQ_PLL_LOSS = 17

# HyperRAM op done / error (#588), gated by its IRQ_EN.
IRQ_HYPERRAM = 18

# Flash command done / error (#593), gated by its IRQ_EN.
IRQ_FLASH = 19

# Every source's trigger, fixed at elaboration. Firmware cannot change one: an
# edge source latches, a level source does not, and which is which is a fact
# about the signal.
#
# The one table the gateware and `scripts/soc/soc_intc_sim.py` both read, and the
# sim checks it against the table in `../../docs/soc-interrupts.md`.
IRQ_TRIGGERS = {
    IRQ_CONSOLE:       "level",
    IRQ_APOLLO:        "level",
    IRQ_I2C:           "level",
    IRQ_TYPE_C_TARGET: "level",
    IRQ_TYPE_C_AUX:    "level",
    IRQ_POWER_ALERT:   "fall",
    IRQ_TARGET_FAULT:  "rise",
    IRQ_AUX_FAULT:     "rise",
    IRQ_TARGET_USB:    "level",
    IRQ_BUTTON:        "rise",
    IRQ_PLL_LOSS:      "fall",
    IRQ_HYPERRAM:      "level",
    IRQ_FLASH:         "level",
}

# 4 MiB, W25Q32, JEDEC EF 40 16. The SFDP table declares 4 MiB and everything
# above that aliases back to offset 0, so mapping more would map the same chip
# twice under two addresses.
FLASH_SIZE = 4 * 1024 * 1024

# The CPU clock. `usb` stays at 60 MHz inside the domain generator -- the ULPI PHY
# requires it and it is not a free parameter -- while this is arbitrary.
#
# The CPU clock. Bounded by:
#   - the PLL: 13 in-spec frequencies 63..130 MHz, listed in `clocks.py`
#   - the fabric: what nextpnr closes, which varies with placement
#   - the flash clocks share its VCO: a multiple of both, or `SocClocks` refuses
# `usb` is the A8 oscillator, so the PHY does not constrain this.
#
# ONE VALUE, for one design. `CYNTHION_SYNC_MHZ=<n>` overrides it, which is what
# lets one elaboration be built at two clocks without editing this file, and is
# how `scripts/riscv/riscv_clock_ladder.py` and `scripts/soc/soc_sync_ladder.py` pick a
# rung since #439.
#
# 50, and it is measured: `scripts/soc/soc_sync_ladder.py`, 7 rungs x 4 nextpnr
# seeds, `--no-parallel-refine`. 60 closed on 3 of 4 seeds, worst -3.2%; 50 on
# 4 of 4. Conditions, the whole ladder and the caveat about four seeds against a
# 9 MHz placement distribution: #432, #467.
#
# `--relaxed-btb` (jumpAt = 2) is already on and is NOT the remaining limit --
# see `cpu/cpu.py`, where it was added for exactly this symptom at 57.55 MHz.
SYNC_MHZ = float(variant.value("CYNTHION_SYNC_MHZ"))

# How long a Wishbone request may go unanswered before the fabric answers ERR.
#
# - waits for: ACK from a decoded subordinate
# - expected: an XIP line fill queued behind one maximal flash command transaction
#   (`flash.xip_worst_sync_cycles`); CSRs answer in 5, block RAM and windows in 1-2
# - 1.25x (+4 for the ack path); the XIP's own fill watchdog fires first
# - on expiry: ERR to the CPU (an access fault the trap handler names), red LED (#411)
BUS_TIMEOUT_CYCLES = -(-5 * (xip_worst_sync_cycles(sync_mhz=SYNC_MHZ) + 4) // 4)

# HyperRAM CK, MHz: 180, clean in the #581 sweep (#588). Own PLL off the 60 MHz `usb`.
HYPERRAM_CK_MHZ = float(variant.value("CYNTHION_HYPERRAM_CK_MHZ"))

# Geometry of each L1 cache: 64 sets x 1 way x 64 B line = 4 KiB each.
# - Reference is upstream VexRiscv (4 KiB I / 2 KiB D, one way), docs/chips/vexiiriscv-cpu.md.
# - One constant for both: the PAC and `usercode_map.py` report it.
# - Ways must be a power of two (Plru asserts `isPow2`).
CACHE_SETS = 64
CACHE_WAYS = 1


class AwtoSoc(Elaboratable):
    """This project's SoC. VexiiRiscv RV32IMAC, with:

    - an interrupt controller (17 numbered sources, 11 built) and a CLINT
    - block RAM, memory-mapped SPI flash, and HyperRAM behind registers + a window
    - the board: LEDs, button, VBUS control, ULPI registers, sideband
    - a PAC1954 power monitor behind an I2C mux
    - a JTAG debug tap, and a JTAG bitstream stager
    - two 16550 consoles: one on USB serial, one on the Apollo UART
    """

    def __init__(self, firmware):
        self.firmware = firmware

    def elaborate(self, platform):
        m = Module()

        # `sync` and the flash clocks (`fsck`, `fcap`, MCLK) on PLL0; `usb` is the
        # 60 MHz oscillator itself. See `clocks.py`.
        m.submodules.car = car = SocClocks(sync_mhz=SYNC_MHZ, flash_mhz=FLASH_SCK_MHZ)
        m.domains += car.domains

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
        # FLASH_CACHED selects between the two, and the first test of running
        # .rodata from flash deliberately uses main=0.
        #
        # Uncached is the simple case: every load is a complete flash transaction
        # and nothing depends on line fills, burst continuation or cache
        # coherency. It is much slower and that is the point -- a first test
        # should remove variables, not preserve performance. If .rodata reads
        # correctly uncached, the mechanism works; turning the cache back on then
        # changes speed rather than correctness, and any failure after that is
        # the cache's.
        regions = list(vexii_cpu.DEFAULT_REGIONS) + [
            f"base={FLASH_BASE:08x},size={FLASH_SIZE:08x},"
            f"main={1 if FLASH_CACHED else 0},exe={1 if FLASH_CACHED else 0}",
        ]

        # CACHE_SETS, not a literal: the build record reports this same
        # constant, so a literal here would let the core and the geometry it
        # advertises drift apart silently.
        # `netlist` is this variant's own copy, and it is what makes concurrent
        # builds safe. The generator writes into the submodule at a fixed path
        # shared by every build (#306); with nowhere else to put it, one build
        # could read a netlist another was halfway through writing -- yosys then
        # fails in whatever way a truncated 1.2 MB Verilog file happens to break
        # it, or worse, succeeds on a stale but valid one.
        cpu = VexiiRiscv(reset_addr=RAM_BASE, cache_sets=CACHE_SETS,
                         cache_ways=CACHE_WAYS, regions=regions,
                         netlist=BUILD_DIR / "VexiiRiscv.v")
        m.submodules.cpu = cpu

        # The die's one `JTAGG`, and both taps off it.
        #
        # ER2 goes to the CPU's debug module, ER1 to the HyperRAM staging sink below.
        # There is exactly one of this primitive on the part, so it is instantiated
        # here and handed out rather than claimed by whichever module wants JTAG.
        m.submodules.user_jtag = user_jtag = UserJTAG()
        m.d.comb += [
            cpu.jtag_tck.eq(user_jtag.tck),
            cpu.jtag_tdi.eq(user_jtag.tdi),
            cpu.jtag_ce.eq(user_jtag.ce2),
            cpu.jtag_shift.eq(user_jtag.shift),
            cpu.jtag_update.eq(user_jtag.update),
            cpu.jtag_rstn.eq(user_jtag.rstn),
            user_jtag.tdo2.eq(cpu.jtag_tdo),
        ]

        ram = BlockRAM(size=RAM_SIZE, init=self.firmware)
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
        # read it without building a bitstream: `scripts/soc/soc_generate_pac.py`, which
        # turns it into an SVD and then into Rust register definitions, and
        # `scripts/soc/soc_diagram.py`. Both had to work around its being a local -- the PAC
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
        # are LEVELS, held for as long as the condition holds -- see
        # uart16550.py for why an edge here would lose everything after the
        # first burst.
        # Indexed by the IRQ_* constants rather than concatenated in order, so
        # the source numbers the firmware writes into the enable register and
        # the wires they select are the same names in the same file. A Cat()
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
        m.submodules.vbus_ctrl = vbus_ctrl = VbusControl()

        # The ULPI register window on TARGET_PHY, and only on TARGET_PHY.
        #
        # AUX_PHY is deliberately untouched: the USB console runs over it, and a
        # second master issuing register commands on that bus would corrupt the
        # link this design reports through. CONTROL_PHY is shared with Apollo.
        # TARGET is the port nothing here drives, which is what makes it the one
        # that can be probed from a running system rather than from a bitstream
        # that evicted the SoC to make room.
        m.submodules.target_ulpi = target_ulpi = UlpiRegisters()

        # The role mux between UlpiRegisters and the host SIE / raw byte stream,
        # on the same pins. Reset role is `off`, which routes UlpiRegisters.
        m.submodules.target_usb = target_usb = UsbPort(name="target", trace_depth=1024 if variant.flag("CYNTHION_USB_TRACE") else 0)
        usb_host_bridge = WishboneCSRBridge(target_usb.bus, data_width=32)
        m.submodules.usb_host_bridge = usb_host_bridge
        decoder.add(usb_host_bridge.wb_bus, addr=USB_HOST_BASE, name="usb_host")

        # The bus select for the one I2C controller, and the four Type-C signals
        # that come with it. See i2c_mux.py: both FUSB302Bs answer to 0x22, so
        # they are on separate pin-sets and a mux is forced rather than chosen.
        m.submodules.i2c_mux = i2c_mux = I2CBusMux()

        # The die's temperature and the bus's fault counters -- the two things
        # only the fabric knows. What this bitstream IS lives in USERCODE and in
        # `gateware/usercode_map.py`, not here (#447, #450).
        m.submodules.fabric_status = fabric_status = FabricStatus()

        # What the clocks ARE, counted against the oscillator, alongside what
        # they were declared to be. A PLL that never locked reported 30 MHz from
        # a constant while `sync` was not oscillating at all.
        m.submodules.clock_monitor = clock_monitor = ClockMonitor(lock=car.locked)

        board_csr = csr.Decoder(addr_width=7, data_width=8)
        m.submodules.board_csr = board_csr
        board_csr.add(board_gpio.bus,    addr=GPIO_BASE     - BOARD_BASE,
                      name="gpio")
        board_csr.add(i2c.bus,           addr=I2C_BASE      - BOARD_BASE,
                      name="i2c")
        board_csr.add(sideband_ctrl.bus, addr=SIDEBAND_BASE - BOARD_BASE,
                      name="sideband")
        board_csr.add(target_ulpi.bus,   addr=ULPI_BASE     - BOARD_BASE,
                      name="ulpi")
        board_csr.add(i2c_mux.bus,       addr=I2C_MUX_BASE  - BOARD_BASE,
                      name="i2c_mux")
        board_csr.add(vbus_ctrl.bus,     addr=VBUS_BASE     - BOARD_BASE,
                      name="vbus")
        board_csr.add(fabric_status.bus, addr=FABRIC_BASE - BOARD_BASE,
                      name="fabric")
        board_csr.add(clock_monitor.bus, addr=CLOCKS_BASE   - BOARD_BASE,
                      name="clocks")

        board_bridge = WishboneCSRBridge(board_csr.bus, data_width=32)
        m.submodules.board_bridge = board_bridge
        decoder.add(board_bridge.wb_bus, addr=BOARD_BASE, name="board")

        m.submodules.intc = intc = Interrupts(IRQ_TRIGGERS)
        m.d.comb += [
            intc.lines[IRQ_CONSOLE].eq(console.irq),
            intc.lines[IRQ_APOLLO].eq(apollo_uart.irq),
            intc.lines[IRQ_I2C].eq(i2c.irq),
            intc.lines[IRQ_TYPE_C_TARGET].eq(i2c_mux.target_irq),
            intc.lines[IRQ_TYPE_C_AUX].eq(i2c_mux.aux_irq),
            intc.lines[IRQ_TARGET_FAULT].eq(i2c_mux.target_fault_irq),
            intc.lines[IRQ_AUX_FAULT].eq(i2c_mux.aux_fault_irq),
            intc.lines[IRQ_TARGET_USB].eq(target_usb.irq),
            # IRQ_POWER_ALERT and IRQ_BUTTON are wired where their resources are
            # requested, some way below: a resource may be requested once, and
            # this block runs first. IRQ_PLL_LOSS is wired below too, beside the
            # synchroniser it needs.
        ]

        # The same lines, keyed by the decoder window each peripheral lives in,
        # so `scripts/soc/soc_generate_pac.py` can put an <interrupt> element on the
        # right peripheral in the SVD.
        #
        # Immediately below the wiring it describes: a source number written
        # down somewhere else is a source number that can disagree with the
        # wire, and firmware that enables the wrong one produces a console that
        # never interrupts with nothing to see anywhere. The names are the
        # `name=` arguments to `decoder.add()` and `board_csr.add()`, joined.
        #
        # A dict value where ONE window raises more than one source: each entry
        # becomes its own <interrupt> and its own `<WINDOW>_<SUFFIX>_IRQ`
        # constant, because the numbers are not interchangeable.
        self.interrupt_sources = {
            "console":       IRQ_CONSOLE,
            "apollo_uart":   IRQ_APOLLO,
            "board_i2c":     IRQ_I2C,
            # Every pin behind the mux, on the mux's window: none of these
            # devices has a CSR window of its own, and servicing one means
            # selecting its segment. A source must name a window that exists --
            # the generator checks -- and this is the window whose driver
            # clears it.
            "board_i2c_mux": {"TARGET":       IRQ_TYPE_C_TARGET,
                              "AUX":          IRQ_TYPE_C_AUX,
                              "POWER_ALERT":  IRQ_POWER_ALERT,
                              "TARGET_FAULT": IRQ_TARGET_FAULT,
                              "AUX_FAULT":    IRQ_AUX_FAULT},
            "usb_host":      IRQ_TARGET_USB,
            "board_gpio":    {"BUTTON":       IRQ_BUTTON},
            "board_clocks":  {"PLL_LOSS":     IRQ_PLL_LOSS},
            "hyperram":      IRQ_HYPERRAM,
            "spi0":          IRQ_FLASH,
        }

        intc_bridge = WishboneCSRBridge(intc.bus, data_width=32)
        m.submodules.intc_bridge = intc_bridge
        decoder.add(intc_bridge.wb_bus, addr=INTC_BASE, name="intc")

        # The CLINT, comparing against the CPU's own `rdtime` counter rather
        # than one of its own. Two counters could disagree; one cannot, and
        # `csrr time` and a load from mtime have to return the same number.
        m.submodules.clint = clint = Clint()
        m.d.comb += clint.mtime.eq(cpu.mtime)

        clint_bridge = WishboneCSRBridge(clint.bus, data_width=32)
        m.submodules.clint_bridge = clint_bridge
        decoder.add(clint_bridge.wb_bus, addr=CLINT_BASE, name="clint")

        # The configuration SPI flash (#593): `peripherals/flash.py` on the #590 core.
        # - `spiflash`: XIP, read-only, cached by the CPU (FLASH_CACHED)
        # - `spi0`: command CSRs; `spi0_buf`: the command's data bytes
        # - SCK is MCLK through DCCA + USRMCLK inside the peripheral; clocks from `car`
        m.submodules.flash = flash = Flash(sck_mhz=car.actual_flash_mhz, sync_mhz=SYNC_MHZ,
                                           n_steps=car.flash_phase_steps)
        m.d.comb += [flash.locked.eq(car.locked), car.flash_phasestep.eq(flash.phasestep),
                     flash.mclk.eq(car.flash_mclk), intc.lines[IRQ_FLASH].eq(flash.irq)]
        decoder.add(flash.xip, addr=FLASH_BASE, name="spiflash")
        decoder.add(flash.bus, addr=FLASH_CSR_BASE, name="spi0")
        decoder.add(flash.buf, addr=FLASH_BUF_BASE, name="spi0_buf")

        # HyperRAM (#588): `peripherals/hyperram.py`. Stream ring tied off until #580.
        m.submodules.hyperram = hyperram = HyperRAM(ck_mhz=HYPERRAM_CK_MHZ,
                                                    sync_mhz=SYNC_MHZ)
        hyperram_bridge = WishboneCSRBridge(hyperram.bus, data_width=32)
        m.submodules.hyperram_bridge = hyperram_bridge
        decoder.add(hyperram_bridge.wb_bus, addr=HYPERRAM_BASE, name="hyperram")
        decoder.add(hyperram.window, addr=HYPERRAM_WINDOW_BASE, name="hyperram_window")
        m.d.comb += [hyperram.stream_w_en.eq(0), hyperram.stream_r_en.eq(0),
                     intc.lines[IRQ_HYPERRAM].eq(hyperram.irq)]

        # The JTAG sink, on ER1, and the reset it holds the CPU in while it works.
        #
        # `ext_reset` has no other source. The CPU reboots itself by jumping to
        # `_start`, so nothing else needs one -- but a JTAG-staged image has to land
        # while the CPU is not executing, or the shell it is replacing is the thing
        # writing over its own staging buffer.
        m.submodules.stager = stager = JTAGStager()
        m.d.comb += [
            stager.tck.eq(user_jtag.tck),
            stager.tdi.eq(user_jtag.tdi),
            stager.ce.eq(user_jtag.ce1),
            stager.shift.eq(user_jtag.shift),
            user_jtag.tdo1.eq(stager.tdo),

            # Also held until the flash's first calibration: `.text` executes from it.
            cpu.ext_reset.eq(stager.cpu_reset | ~flash.boot_ready),
        ]

        m.d.comb += [
            hyperram.pair_req.eq(stager.req),
            hyperram.pair_addr.eq(stager.addr),
            hyperram.pair_data.eq(stager.data),
            stager.ack.eq(hyperram.pair_ack),
        ]

        # The machine external interrupt, from the interrupt controller.
        #
        # This input existed and was connected to nothing -- an undriven `In`
        # port of a Component reads as zero, so the SoC had an interrupt path
        # that could never fire and nothing said so. That is why the firmware
        # polled.
        #
        # The other two come from the CLINT above, and nothing here is tied off
        # any more. `irq_timer` is `mtime >= mtimecmp` and is what the firmware's
        # 1 ms tick rides on; `irq_software` is msip, which nothing raises yet
        # but which a driver can now reach rather than write into a hole.
        m.d.comb += [
            cpu.irq_external.eq(intc.irq_out),
            cpu.irq_timer.eq(clint.irq_timer),
            cpu.irq_software.eq(clint.irq_software),
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
        # is not simply a register on `ack`, and scripts/soc/soc_bus_sim.py for the
        # measurement of both.
        # The CSR PMA region is narrowed to the top of the CLINT (#452), so a
        # window added above it would be decoded by the fabric and unreachable
        # from the CPU -- an access there traps before any bus cycle.
        _csr_top = vexii_cpu.CSR_REGION_BASE + vexii_cpu.CSR_REGION_SIZE
        for _window, _name, (_start, _end, _ratio) in decoder.bus.memory_map.windows():
            if _start >= vexii_cpu.CSR_REGION_BASE and _end > _csr_top:
                raise ValueError(
                    f"decoder window {_name} ends at {_end:#010x}, above "
                    f"the CSR PMA region's {_csr_top:#010x}: the CPU would trap "
                    f"on it rather than reach it. Raise CSR_REGION_SIZE in "
                    f"cpu/cpu.py.")

        m.submodules.bus_pipe = bus_pipe = RegisteredResponse(
            addr_width=30, data_width=32, granularity=8,
            features={"cti", "bte", "err"})
        # And a terminator behind it, because the decoder does not have one.
        #
        # `amaranth_soc.wishbone.Decoder.elaborate` is one Switch with a Case per
        # window and NO DEFAULT: an address matching none of them gets neither
        # ACK nor ERR, and a classic initiator waits for one of those forever.
        # The CPU's I/O PMA region is `f0000000+10000000` -- the whole top 256
        # MiB -- while this decoder claims about twenty windows inside it, so
        # every gap is a hang the core believes is a legal access. That is what
        # took the board out when `bist status` read the BIST engine's window on
        # a variant that has no engine (#409).
        #
        # Two mechanisms, and the second is not redundant: an address compare
        # cannot see a peripheral that IS decoded and has stopped answering,
        # which is the other half of #409. See bus/fault.py.
        m.submodules.bus_fault = bus_fault = BusFault(
            decoder=decoder, timeout=BUS_TIMEOUT_CYCLES,
            addr_width=30, data_width=32, granularity=8,
            features={"cti", "bte", "err"})

        wiring.connect(m, arbiter.bus, bus_pipe.intr_bus)
        wiring.connect(m, bus_pipe.sub_bus, bus_fault.intr_bus)
        wiring.connect(m, bus_fault.sub_bus, decoder.bus)

        # And its account, where the CPU can read it. `worst` is what keeps
        # BUS_TIMEOUT_CYCLES honest: a high-water mark near the bound is a board
        # about to fault on legitimate traffic, and a margin nobody can read is
        # a margin nobody can check.
        m.d.comb += [
            fabric_status.fault_unclaimed.eq(bus_fault.unclaimed_count),
            fabric_status.fault_timeouts.eq(bus_fault.timeout_count),
            fabric_status.fault_worst.eq(bus_fault.worst_wait),
            # TEMPORARY (#593 debug)
            stager.debug.eq(Cat(flash.debug, bus_fault.timeout_count,
                                stager.cpu_reset | ~flash.boot_ready)),
        ]

        # USB CDC-ACM, on the AUX port.
        #
        # CDC descriptors are what make a /dev/ttyACM node appear at all -- the kernel
        # declines to bind a serial driver to a vendor-specific interface, and a bulk
        # endpoint without them is silent with nothing to say why.
        #
        # USBSerialDevice measures 195.4 Mbps CDC-ACM loopback in
        # docs/usb-performance.md; CDC costs essentially nothing over raw
        # bulk, being the same two stream endpoints plus descriptors.
        from luna.gateware.usb.devices.acm import USBSerialDevice

        # AUX rather than CONTROL or TARGET: CONTROL is shared with Apollo and has to be
        # claimed (sideband bit 5, `gateware/probes/sideband/sideband_advertise.py`), TARGET is the port
        # under test, and AUX belongs to the FPGA outright.
        bus = platform.request("aux_phy", 0)

        # 512 is the high-speed bulk maximum. The default of 64 is the full-speed limit,
        # which enumerates at high speed and then runs at an eighth of the achievable rate.
        # ID from the central allocation in gateware/usb_ids.py, never a locally chosen
        # number: 0x615c is Apollo's own debugger and bootloader ID, and a bitstream
        # claiming it impersonates the debugger on the host's device list.
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
            # on a beat where `valid` is high. `last = ~valid` is unsatisfiable:
            # the two are never high together, so no packet is ever terminated and
            # nothing reaches the host, with the CPU running and the FIFO filling.
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

            # Host -> CPU. `serial.rx.ready` must come from the buffer, not be tied
            # high: tied high accepts every byte and discards it, and typing at the
            # console then does nothing at all.
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
        # SerialLine, not a bare AsyncSerial wired to the pads. AsyncSerial alone
        # leaves the receive pad unsynchronised, delivers framing errors as
        # characters, and gives no output enable that survives the stop bit --
        # issue #113, and serial_line.py has the full account.
        #
        # From the SOLVED rate, like the console's divisor 60 lines below and for
        # the same reason: a divisor computed from a clock the hardware is not
        # running produces framing errors, and framing errors read as a cable or
        # a driver fault. Nothing downstream can catch it -- `info`'s
        # CLOCK MISMATCH compares the reported rate against the measured one, and
        # cannot see a divisor baked into a peripheral from a third number.
        m.submodules.apollo_line = apollo_line = SerialLine(
            divisor=int(car.actual_sync_mhz * 1e6 // APOLLO_UART_BAUD),
            data_bits=8)

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
            # What the line loses, on into LSR.
            #
            # This is the port where a byte can actually be destroyed: an async
            # serial line has no flow control, `SerialLine.source.valid` is one
            # cycle whatever the buffer says, and a frame with a bad stop bit is
            # dropped rather than delivered. The 16550 cannot see either -- its
            # own `sink` backpressures, so a full FIFO there is a stall and not a
            # loss -- so the transport reports both and the peripheral latches
            # them as LSR.OE and LSR.FE.
            #
            # The USB console has no equivalent and drives neither input: the CDC
            # endpoint NAKs while its buffer is full and the host retries, so
            # that path loses nothing to report.
            apollo_uart.overrun.eq(apollo_line.overrun),
            apollo_uart.frame_error.eq(apollo_line.frame_error),

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
        sys.path.insert(0, str(ROOT / "gateware"))
        sys.path.insert(0, str(ROOT / "gateware" / "probes"))
        sys.path.insert(0, str(ROOT / "gateware" / "probes" / "sideband"))
        from sideband_debug import SidebandDebug
        # The sideband's bit period is a cycle count derived from the domain frequency, so a
        # design that raises `sync` and leaves this at its default gets a DEAD link rather
        # than a slow one -- a UART tolerates about +/-2% and the error scales with the
        # clock. Passing SYNC_MHZ keeps the two in step by construction.
        # The ACTUAL solved frequency, not `SYNC_MHZ`. The PLL does not always
        # land on the request, and the baud divisor is computed from this at
        # elaboration with nothing checking it after.
        # Requesting 61 MHz builds 60.0, which is 227273 baud against 230769:
        # -1.5%, inside a UART's ~2% tolerance with the margin gone, silently.
        m.submodules.sideband = sideband = SidebandDebug(
            clk_freq_hz=car.actual_sync_mhz * 1e6)

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
        # THE DIAGNOSTIC IS THE DEFAULT, NOT THE ONLY DRIVER. The GPIO peripheral
        # above can take any LED, one at a time, by putting that pin in push-pull
        # mode; while it does not, the diagnostic below drives it.
        # `amaranth_soc.gpio` calls the other mode INPUT_ONLY and documents it as
        # "the pin output is disabled", which is exactly the claim being made:
        # while the CPU is not driving, something else is.
        #
        # The reset value of Mode is INPUT_ONLY for every pin, so a bitstream
        # whose firmware never runs still lights the LEDs with the fabric's own
        # account of itself. That is the whole reason these six exist.
        led_pads = [platform.request("led", n).o for n in range(6)]
        leds = Signal(6)

        # Half a second on, half a second off, at whatever `sync` the PLL SOLVED
        # for -- not at what was requested. Fast enough to read as deliberate,
        # slow enough to be unmistakably a flash rather than a flicker.
        #
        # This one is cosmetic and is fixed anyway: it is the only heartbeat the
        # board has, so it is the thing someone will time with a stopwatch to
        # ask whether the clock is what it says. A heartbeat derived from the
        # request would answer that question with the request.
        half_second = int(car.actual_sync_mhz * 1e6 // 2)
        heartbeat = Signal(range(half_second + 1))
        heartbeat_on = Signal()
        with m.If(heartbeat == half_second):
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
            # A byte each way, so the link carries a message and not only a
            # heartbeat. See sideband_csr.py for the register discipline.
            sideband.message.eq(sideband_ctrl.message),
            sideband_ctrl.received.eq(sideband.received),
            sideband_ctrl.received_strobe.eq(sideband.received_strobe),
            # Asking Apollo for the CONTROL port. Off until firmware sets bit 5,
            # so this AUX-only design keeps behaving exactly as it did.
            sideband.advertise.eq(sideband_ctrl.advertise),

            # OFF IS GOOD, MOTION IS ALIVE. Cat is by PIN INDEX and the colour
            # beside each is what the board actually fits (#415).
            #
            # `ever_fetched` and `ever_io` are deliberately NOT here any more:
            # both latch within microseconds of any boot and have never
            # distinguished anything since. A lamp that is always on is the
            # dead-instrument problem #411 was filed about.
            #
            # The two heartbeats sit ADJACENT on purpose -- yellow at 1 Hz from
            # the fabric, orange at 2 Hz from the RTIC task -- so the rates can
            # be compared by eye. That comparison is what caught the reversed
            # colour map.
            leds.eq(Cat(0,                     # 0 E13 violet -- unassigned
                        ever_console,          # 1 C13 blue   -- a byte has moved
                        serial.connect,        # 2 B14 green  -- USB console up
                        heartbeat_on,          # 3 A15 yellow -- FPGA alive, 1 Hz
                        0,                     # 4 D12 orange -- CPU takes this;
                                               #   DARK until firmware drives
                                               #   it, so no OS reads as no blink
                        ever_errored)),        # 5 C11 red    -- ANY bus error
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

        # The two Type-C controllers' buses and their `int` / `fault` lines. All
        # four signals are declared `PinsN` in the platform, so Amaranth has
        # already undone the active-low sense and a 1 on `.i` means the device is
        # asserting.
        type_c_target = platform.request("target_type_c", 0)
        type_c_aux = platform.request("aux_type_c", 0)

        # The VBUS distribution switches. Active high, and open out of reset
        # because `VbusControl.enable` clears -- see vbus_csr.py for why the gate
        # is combinational rather than a latched copy.
        #
        # `control_vbus_in_en` (K13) and `aux_vbus_in_en` (L13) are deliberately
        # NOT requested: hardware overvoltage protection above 5.5 V (D17, a
        # 5.6 V zener) already does that job. `VbusControl` has no ports for
        # them, so this is a fact about the board rather than two dangling
        # outputs (#305).
        m.d.comb += [
            platform.request("target_c_vbus_en", 0).o
                .eq(vbus_ctrl.target_c_vbus_en),
            platform.request("control_vbus_en", 0).o
                .eq(vbus_ctrl.control_vbus_en),
            platform.request("aux_vbus_en", 0).o
                .eq(vbus_ctrl.aux_vbus_en),
            platform.request("target_a_discharge", 0).o
                .eq(vbus_ctrl.target_a_discharge),
        ]

        # PWRDN is active low on the pad. The GPIO block drives it only in
        # push-pull mode, so the reset state is a 0 here, a 1 on the pad, and a
        # PAC1954 that is running -- which is what the I2C bus below needs.
        #
        # SLOW IS DRIVEN LOW: leaving it an input costs 15x the sample rate.
        # **The board fits R85, 10k to +3V3**, so an input is not neutral -- it
        # is pulled HIGH, and DS20006539B section 3.8 is unambiguous about what
        # that means: "the SLOW pin is asserted, the sample rate is 8 SPS".
        #
        # 8 SPS is 125 ms per conversion against a 50 ms poll, so two of every
        # three REFRESH cycles latched a conversion that had not changed. Seen
        # on the board as runs of bit-identical readings across both rails and
        # both quantities:
        #
        #     5.129 V  176.239 mA
        #     5.129 V  176.239 mA      <- identical
        #     5.130 V  177.154 mA
        #     5.130 V  177.154 mA      <- identical
        #     5.130 V  177.154 mA      <- identical
        #
        # Two independent conversions of a live rail do not agree to 488 uV and
        # 152 uA. That is one conversion, read three times.
        #
        # Driving it is safe: the pin's purpose is established in the datasheet
        # and the board pulls it up precisely so it has a defined state. The part
        # defaults `CTRL.SLOW_ALERT1` to the SLOW function and `power.rs` writes
        # only `NEG_PWR_FSR`, so this is an input on the part and there is
        # nothing to contend with.
        #
        # `gpio` (D6) stays an input, and that one IS unfinished business rather
        # than a defect: R86 pulls it up as the open-drain ALERT2 the part can
        # assert on conversion complete, which is #270.
        m.d.comb += [
            power_monitor.slow.o.eq(0),
            power_monitor.slow.oe.eq(1),
        ]

        # The ALERT (#270). Wired here rather than with the other sources
        # because a resource may be requested once and that block runs first.
        #
        # The raw pad, not inverted: the source's trigger is `fall`, which is
        # the assertion of an active-low pin. Synchronised because it is an
        # asynchronous input and the edge detector is one flop.
        #
        # `gpio` stays an input: `oe` is left at its default 0, and R86 (10k to
        # +3V3) holds the line high when nothing is asserting, which is what an
        # open-drain ALERT needs.
        power_alert = Signal(init=1)
        m.submodules.power_alert_cdc = FFSynchronizer(power_monitor.gpio.i,
                                                      power_alert, init=1)
        m.d.comb += intc.lines[IRQ_POWER_ALERT].eq(power_alert)
        m.d.comb += power_monitor.pwrdn.o.eq(
            board_gpio.pins[GPIO_PWRDN].o & board_gpio.pins[GPIO_PWRDN].oe)
        m.d.comb += board_gpio.pins[GPIO_PWRDN].i.eq(power_monitor.pwrdn.o)

        # The USER button, to the GPIO peripheral's input bit and to its own
        # source. Synchronised once, here, so the CSR bit and the interrupt
        # cannot disagree about which cycle the press landed on.
        button = platform.request("button_user", 0)
        button_pressed = Signal()
        m.submodules.button_cdc = FFSynchronizer(button.i, button_pressed)
        m.d.comb += [
            board_gpio.pins[GPIO_BUTTON].i.eq(button_pressed),
            intc.lines[IRQ_BUTTON].eq(button_pressed),
        ]

        # PLL lock, into the source that reports its LOSS -- trigger `fall`.
        # `car.locked` is in no clock domain of ours, so it is synchronised
        # before an edge detector looks at it.
        pll_locked = Signal()
        m.submodules.pll_locked_cdc = FFSynchronizer(car.locked, pll_locked)
        m.d.comb += intc.lines[IRQ_PLL_LOSS].eq(pll_locked)

        # ---- the TARGET PHY's ULPI bus --------------------------------------
        #
        # The FPGA sources the clock (`clk_dir='o'` in the platform), so the PHY
        # runs at whatever `usb` runs at -- 60 MHz, which is what a USB3343
        # requires and is why `usb` is not a free parameter the way `sync` is.
        #
        # `rst` is declared `rst_invert=True`, so the pad is active low and a 1
        # here holds the PHY in reset. It has two drivers, ORed: `car.phy_reset`
        # is the power-on pulse, counted in `usb` and specified in `clocks.py`,
        # and `ulpi.phy_rst` is the CSR-driven one firmware uses to recover a
        # PHY that has glitched. Tying it to 0 -- which this line did between
        # `soc-clocks` and #241 -- leaves a glitched PHY with no way back, since
        # firmware cannot reconfigure the FPGA it is running on.
        #
        # Pins go through `target_usb` (`peripherals/usb_host.py`), whose reset role
        # routes `target_ulpi` exactly as before; `host`/`raw` take the pins over.
        # No device stack on this port.
        target_phy = platform.request("target_phy", 0)
        m.d.comb += [
            target_phy.clk.o.eq(ClockSignal("usb")),
            target_phy.rst.o.eq(car.phy_reset | target_ulpi.phy_rst),
            # Pins <-> role mux <-> UlpiRegisters. In role off/regs this is the
            # old direct connection through one mux level.
            target_phy.stp.o.eq(target_usb.stp_o),
            target_phy.data.o.eq(target_usb.data_o),
            target_phy.data.oe.eq(target_usb.data_oe),
            target_usb.data_i.eq(target_phy.data.i),
            target_usb.dir_i.eq(target_phy.dir.i),
            target_usb.nxt_i.eq(target_phy.nxt.i),
            target_ulpi.data_i.eq(target_phy.data.i),
            target_ulpi.dir_i.eq(target_usb.regs_dir),
            target_ulpi.nxt_i.eq(target_usb.regs_nxt),
            target_usb.regs_data_o.eq(target_ulpi.data_o),
            target_usb.regs_data_oe.eq(target_ulpi.data_oe),
            target_usb.regs_stp_o.eq(target_ulpi.stp_o),
            target_usb.regs_idle.eq(target_ulpi.usb_idle),
        ]

        # ---- the three I2C buses, from one controller -----------------------
        #
        # `scl` is `dir="o"` on all three, so it is driven push-pull and nothing
        # on any of them may stretch the clock; `sda` is `dir="io"` and is driven
        # properly open-drain. See i2c_master.py for what that rules out.
        #
        # `power_monitor` was requested above for its PWRDN pin, so it is passed
        # in rather than requested again -- a resource may only be requested
        # once, and the second request is an error rather than a second copy.
        m.d.comb += [
            i2c_mux.idle.eq(i2c.idle),
            i2c_mux.target_int.eq(type_c_target.int.i),
            i2c_mux.aux_int.eq(type_c_aux.int.i),
            i2c_mux.target_fault.eq(type_c_target.fault.i),
            i2c_mux.aux_fault.eq(type_c_aux.fault.i),
        ]

        for select_value, port in ((I2C_MUX_TARGET_C, type_c_target),
                                   (I2C_MUX_AUX_C, type_c_aux),
                                   (I2C_MUX_POWER, power_monitor)):
            chosen = i2c_mux.select == select_value
            m.d.comb += [
                # Unselected buses are driven IDLE -- SCL high, SDA released --
                # rather than left undriven. These pins carry PULLMODE="NONE", so
                # an undriven line floats rather than idling high, and a floating
                # SDA is a transient that a device listening on that bus reads as
                # a START.
                port.scl.o.eq(Mux(chosen, i2c.scl_o, 1)),
                # Open drain: a one is sent by releasing the line, so the output
                # value is always zero and only the enable moves.
                port.sda.o.eq(i2c.sda_o),
                port.sda.oe.eq(chosen & i2c.sda_oe),
            ]

        # Idle high by default, so a select value with no bus behind it -- 3,
        # which two bits can hold and nothing assigns -- reads as an idle bus
        # rather than as SDA held low. An undriven `sda_i` reads 0, which this
        # controller correctly reports as arbitration lost, and chasing
        # "something is holding SDA down" when the answer is "you selected bus 3"
        # is a bad afternoon.
        m.d.comb += i2c.sda_i.eq(1)
        for select_value, port in ((I2C_MUX_TARGET_C, type_c_target),
                                   (I2C_MUX_AUX_C, type_c_aux),
                                   (I2C_MUX_POWER, power_monitor)):
            with m.If(i2c_mux.select == select_value):
                m.d.comb += i2c.sda_i.eq(port.sda.i)

        return m



def _map_digest():
    """The peripheral-map digest, from the PAC generator in its own process.

    A subprocess because the generator patches the CPU generator to elaborate
    the map; that must not leak into this build (#307).
    """
    import subprocess
    root = Path(__file__).resolve().parents[2]
    out = subprocess.run([sys.executable, str(root / "scripts" / "soc" / "soc_generate_pac.py"),
                          "--digest"], capture_output=True, text=True, check=True)
    return out.stdout.strip().splitlines()[-1]


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
                        default=ROOT / "tmp" / "riscv_hello" / "hello.bin",
                        help="the IMAGE, linked for IMAGE_ORIGIN")
    parser.add_argument("--bootloader", type=Path,
                        default=BUILD_DIR / "rust_boot.bin",
                        help="the resident bootloader, linked for 0x0; omit for a "
                             "single-image build that boots --firmware directly")
    args = parser.parse_args()

    if not args.firmware.exists():
        print(f"no firmware at {args.firmware}")
        print("build it with ./scripts/riscv/riscv_firmware.py")
        return 1

    # Two images in one 64 KiB init, at their two origins.
    #
    # This is what makes the split work at all. The bitstream initialises ALL of block
    # RAM, not just the low half, so one build carries the resident bootloader at 0x0
    # AND a default image at IMAGE_ORIGIN. A board with nothing staged in HyperRAM
    # therefore comes up on the image the bitstream placed -- a fallback that exists at
    # power-on by construction and cannot be missing, which is exactly what lets the
    # bootloader treat every failure the same way.
    #
    # Without --bootloader the image is packed at 0 and runs from the reset vector. That
    # is the C generator's layout (`scripts/riscv/riscv_firmware.py`), which has no bootloader
    # and is linked for 0.
    boot = b""
    if args.bootloader and args.bootloader.exists():
        boot = args.bootloader.read_bytes()
        if len(boot) > IMAGE_ORIGIN:
            print(f"bootloader is {len(boot)} bytes; it must fit under "
                  f"IMAGE_ORIGIN ({IMAGE_ORIGIN})")
            return 1

    image = args.firmware.read_bytes()
    origin = IMAGE_ORIGIN if boot else 0
    if origin + len(image) > RAM_SIZE:
        print(f"image is {len(image)} bytes at {origin:#x}, block RAM is {RAM_SIZE}")
        return 1

    if boot:
        # Zero fill between them. Nothing executes there -- it is the bootloader's
        # stack, growing down from IMAGE_ORIGIN.
        raw = boot + b"\x00" * (IMAGE_ORIGIN - len(boot)) + image
        print(f"bootloader: {len(boot)} bytes at 0x0")
        print(f"image:      {len(image)} bytes at {IMAGE_ORIGIN:#x}")
    else:
        raw = image
        print(f"image: {len(image)} bytes at 0x0 (no bootloader)")

    # --placeholder builds a bitstream whose block RAM holds a known RANDOM image
    # instead of firmware, so `ecpbram` can substitute real firmware into the built
    # bitstream later -- one second, no synthesis.
    #
    # Kept because it is the only path that replaces BLOCK RAM INIT without a rebuild --
    # the bootloader and the default image both -- where a staged image goes in over
    # JTAG (`scripts/soc/soc_jtag_stage.py`) or the console (`load`) and touches neither.
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
        print(f"placeholder image: {len(raw)} bytes; substitute firmware with "
              f"`ecpbram -f {{old}}.hex -t {{new}}.hex -i top.config -o out.config`")

    # Block RAM is 32 bits wide, so the init is loaded as words.
    padded = raw + b"\x00" * (-len(raw) % 4)
    words = [int.from_bytes(padded[i:i + 4], "little")
             for i in range(0, len(padded), 4)]
    print(f"block RAM init: {len(raw)} bytes, {len(words)} words")

    if not (args.build or args.program):
        print("nothing to do; pass --build")
        return 0

    # THE VENDORED PLATFORM, `gateware/board/cynthion_r1_4.py` (#416): upstream's pin
    # map with local LEDResources/ULPIResource/CynthionPlatform, so no amaranth_boards.
    _sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from board.cynthion_r1_4 import CynthionPlatformRev1D4
    from build_helpers import ecppack_opts, source_digest, usercode

    # One directory per variant, not one for all of them.
    #
    # A fixed path meant two builds could not run at once: a CK ladder is one
    # bitstream per rung and twenty of them are twenty sequential synthesis runs
    # on a 31-core machine (#351). The name comes from `variant.py`, which is the
    # same table `soc_run.py` hashes into the bitstream cache key -- one notion of
    # what makes a build distinct, so the cache and the directory cannot disagree.
    build_dir = BUILD_DIR
    build_dir.mkdir(parents=True, exist_ok=True)

    # THE BUILD IDENTITY, stamped at pack time (#447, #450).
    #
    # `AMARANTH_ecppack_opts`, not a `build(ecppack_opts=...)` kwarg: the platform
    # passes its own in `toolchain_prepare` before `**kwargs`, so a kwarg is a
    # duplicate keyword and the build fails outright. Amaranth's
    # `_extract_override` reads the environment FIRST, so this adds to the
    # platform's command line without patching the platform.
    #
    # Set here rather than by the runner so that every route into this build --
    # `soc_run.py`, `soc_repro_check.py`, a bare `top.py --build` -- stamps the
    # same value, computed once and reused for the record below.
    platform = CynthionPlatformRev1D4()
    code = usercode()
    os.environ["AMARANTH_ecppack_opts"] = ecppack_opts(code=code)["ecppack_opts"]

    platform.build(
        AwtoSoc(firmware=words),
        do_program=args.program,
        build_dir=str(build_dir))

    # What those 32 bits MEAN, beside the bitstream they were stamped into.
    # `scripts/soc_confirm.py` reads USERCODE over JTAG and resolves it here.
    import usercode_map
    from clocks import SocClocks

    # Solved for its frequencies, never elaborated -- so tell Amaranth's
    # MustUse, or every build log carries an UnusedElaboratable that hides
    # the warnings worth reading.
    solved = SocClocks(sync_mhz=SYNC_MHZ)
    solved._MustUse__used = True
    record = usercode_map.record_for_build(
        usercode=code,
        build_dir=build_dir,
        variant_slug=variant.slug(),
        source_digest=source_digest(),
        map_digest=_map_digest(),
        device=platform.device,
        speed=platform.speed,
        sync_hz=round(solved.actual_sync_mhz * 1e6),
        usb_hz=round(solved.actual_usb_mhz * 1e6),
        cache_sets=CACHE_SETS, cache_ways=CACHE_WAYS,
        isa=vexii_cpu.isa_generated())
    # The packed file must agree with the record on the part (cynthion#185).
    problems = usercode_map.bitstream_problems(build_dir / "top.bit", record)
    if problems:
        raise SystemExit("bitstream/record part mismatch:\n  " + "\n  ".join(problems))
    beside, index = usercode_map.write(record, build_dir)
    print(f"usercode {code:#010x} -> {beside}")

    # Record the exact BRAM contents alongside the bitstream.
    #
    # This is what `ecpbram` matches against: it finds the old contents BY VALUE and
    # substitutes new ones, replacing firmware in a built bitstream in about a second
    # instead of a ~60 s resynthesis. It can only do that if it is handed exactly what
    # was synthesised, so this is written here rather than reconstructed later -- a
    # reconstruction differing by one word would simply fail to match.
    #
    # Padded to the full RAM, because that is the geometry that ends up in the BRAM:
    # `words` covers only the image, while the initialiser covers every location.
    hex_words = words + [0] * (RAM_SIZE // 4 - len(words))
    (build_dir / "firmware.hex").write_text(
        "".join(f"{word:08x}\n" for word in hex_words))

    return 0


if __name__ == "__main__":
    sys.exit(main())
