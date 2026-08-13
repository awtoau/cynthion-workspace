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
`scripts/soc_generate_pac.py`; `scripts/check.py` fails if firmware writes one
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

# Import order matters. amaranth_soc is vendored inside luna_soc rather than
# installed standalone, and importing a luna_soc peripheral is what aliases it
# onto sys.modules under the bare name. Importing the vendored path directly
# instead yields a *different* class object for wishbone.Interface, so
# Decoder.add() rejects a bus that is structurally identical -- these must come
# first, and the bare name must be used afterwards.
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
from peripherals.i2c_mux import (I2CBusMux, BUS_TARGET_C as I2C_MUX_TARGET_C,
                     BUS_AUX_C as I2C_MUX_AUX_C,
                     BUS_POWER_MONITOR as I2C_MUX_POWER)
from peripherals.stream_buffer import StreamBuffer
from bus.wishbone_pipe import RegisteredResponse
from bus.fault         import BusFault, worst_ack_cycles
from peripherals.flash_cdc import ClockCrossedPHY
from bootram import BootRAM, HYPERRAM_LATENCY_CLOCKS
from peripherals.hyperram_probe import HyperRAMProbe
from peripherals.flash import (FairSPIControlPortCrossbar, FlashPinProbe,
                         HoldableSPIController, ModalSPIFlashMemoryMap,
                         ObservablePHY, QSPIFlashPins, READ_MODES)

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
# scripts/soc_payload.py. `scripts/soc_generate_pac.py --check` reads all of
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

# The HyperRAM memory window: ordinary cached loads, stores, and instruction fetches.
#
# The base matches Cynthion's existing facedancer map. Eight MiB is the populated
# W956A8 device; the similarly named 128-Mbit part is not fitted on r1.4.
HYPERRAM_BASE = 0x20000000
HYPERRAM_SIZE = 8 * 1024 * 1024

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

# Transaction counters on the HyperRAM window (#173). Answers whether a 64-byte
# cache-line refill reaches the part as one burst or as sixteen transactions --
# a question five separate readings of the source could not settle, because every
# one of them said it should burst and the board says it costs 336 CK.
HYPERRAM_PROBE_BASE = 0xf0000280

# The HyperRAM BIST engine's register window. 512 bytes: the engine addresses its registers by number, and each number
# maps to a 32-bit CSR twice -- a parameter at 4*n and a result at 0x100 + 4*n,
# see `peripherals/bist_csr.py`. Adding a register in the gateware therefore
# does not move the others.
#
# Deliberately the SAME numbering the JTAG applet uses. Two rigs sharing it is
# what lets a number from one be compared with a number from the other (#226).
#
# 0x700 because the board block at BOARD_BASE runs 0x600..0x680 -- a fact worth
# getting from the elaborated design rather than by reading this list, since
# BOARD_BASE's extent is the sum of its sub-peripherals and is not written down
# anywhere.
HYPERRAM_BIST_BASE = 0xf0000800

# Which CK rung is live, and who owns the part. 16 bytes: four 32-bit
# registers, see `peripherals/hyperram_ck.py`.
#
# 0xa00 because the BIST window above runs 0x800..0x9ff, and 16-byte aligned
# because the Wishbone decoder requires a window aligned to its own size -- a
# misaligned one decodes to nothing and hangs the CPU on the first read, with no
# error at elaboration (#226).
HYPERRAM_CK_BASE = 0xf0000a00

# The HyperRAM boot port -- where the bootloader reads the staged firmware image from.
#
# Uncached like every other CSR here, and for the sharpest possible reason: `status.valid`
# is set by gateware while the CPU spins on it. A cached read would return the same line
# forever and the poll would never complete, giving a bootloader that hangs on a HyperRAM
# that is working perfectly.
BOOTRAM_BASE = 0xf0000400

# The interrupt controller: two registers, `cpu/intc.py`.
#
# 32 bytes of Wishbone -- six CSR bytes at the bridge's stride of 4. Inside the
# `main=0` CSR region declared in vexii_cpu.DEFAULT_REGIONS, because a cached
# controller would return a stale pending word forever, and aligned to its own
# size, which the Wishbone decoder requires of every window.
#
# 0xc00: the board block ends at 0x680 and the HyperRAM CK window at 0xa10.
INTC_BASE = 0xf0000c00

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

# Every source's trigger, fixed at elaboration. Firmware cannot change one: an
# edge source latches, a level source does not, and which is which is a fact
# about the signal.
#
# The one table the gateware and `scripts/soc_intc_sim.py` both read, and the
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
    IRQ_BUTTON:        "rise",
    IRQ_PLL_LOSS:      "fall",
}

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
# first, because there is nothing in it to get subtly wrong. "quad" is 0xeb,
# address and data on four lanes with `dummy_value=0xff0000`.
# See gateware/soc/peripherals/flash.py.
#
# Quad needs no register write on this part: QE (SR2 bit 1) is already set, read
# back as 0x02 (docs/chips/w25q32-config-flash.md). And 0xeb's dummy count is
# fixed at four clocks in SPI mode -- the configurable one is a QPI command --
# so there is nothing to tune and nothing that can be left half-configured.
#
# Measured on this board, cache-line refill for 64 B: 0x03 3833 ns, 0xeb 1083 ns
# (#100). What that buys the CPU is in the bench rows, not in the ratio: the
# flash 16 KiB random walk misses the D-cache on every access and is therefore
# very nearly pure refill.
FLASH_MODE = "quad"

# SCK = sync / (2 * (1 + divisor)), so at SYNC_MHZ = 60 and divisor 0 this design
# clocks the flash at 30 MHz. That is the SLOWEST rung on the measured table and
# the reason is below.
#
# TWO CLAIMS ABOUT THIS PATH ARE WRONG. `docs/chips/w25q32-config-flash.md`
# supersedes both:
#
#   * "inside the ECP5 MCLK pin's 62 MHz specification". THERE IS NO SUCH
#     SPECIFICATION. The 62 MHz is `fCCLK` in the sysCONFIG port timing table --
#     the configuration engine's oscillator ceiling, which has nothing to do with
#     user mode. `USRMCLK` does not appear in the ECP5 datasheet at all, and
#     prjtrellis has no timing entry for the path in any speed grade.
#   * "divisor 0 produces no clock; SCK capped at sync/2". Divisor 0 reads
#     byte-exact at every rung, at exactly half the cycle count of divisor 1.
#
# WHAT THE PART ACTUALLY DOES: 60 points -- five modes x four divisors x three
# sync rates -- all PASS, up to 144 MHz SCK and 71.70 MB/s on `0xEB` continuous.
# Nothing failed. `0x03` runs at 144 MHz against a 50 MHz datasheet rating. The
# limit reached was the TEST DESIGN'S OWN FMAX (it closes at 149 MHz), not the
# flash and not the pin.
#
# So the ceiling here is not the divisor: it is that SCK is derived from `sync`,
# and `sync` is the CPU's clock. Raising it is a CPU change. Reaching the
# measured speeds without touching the CPU needs the flash PHY in its own clock
# domain -- see #100.
FLASH_DIVISOR = 0

# How long a Wishbone request may go unanswered before the fabric answers ERR.
#
#   waits for   ACK from a decoded subordinate
#   expected    the memory-mapped flash, worst legitimate case: a cold quad read
#               (69 cycles) with one maximal SPI-controller transfer stealing the
#               crossbar before each of its four phases. `worst_ack_cycles`
#               derives it; nothing else here is close -- CSR peripherals answer
#               in 5, block RAM in 1, the HyperRAM window in 20.
#   multiplier  1.25x
#   on expiry   ERR to the CPU -- a load/store access fault the trap handler
#               names -- and the red LED, which means any bus error (#411).
#
# In cycles, so it does not move with SYNC_MHZ, and derived from FLASH_MODE and
# FLASH_DIVISOR, so raising either does not silently make it tight. Without it
# an unanswered request is a hang, and a hang is what #409 is.
BUS_TIMEOUT_CYCLES = -(-5 * worst_ack_cycles(mode=READ_MODES[FLASH_MODE],
                                             divisor=FLASH_DIVISOR) // 4)

# The CPU clock. `usb` stays at 60 MHz inside the domain generator -- the ULPI PHY
# requires it and it is not a free parameter -- while this is arbitrary.
#
# The CPU clock. Bounded by:
#   - the PLL: 13 in-spec frequencies 63..130 MHz, listed in `clocks.py`
#   - the fabric: what nextpnr closes, which varies with placement
#   - `fast` = FLASH_FAST_RATIO x sync when built, and the flash PHY closes at
#     124.77 MHz -- so it caps sync whenever FLASH_PHY_FAST is on
# `usb` is the A8 oscillator, so the PHY does not constrain this.
#
# ONE VALUE, for one design. `CYNTHION_SYNC_MHZ=<n>` overrides it, which is what
# lets one elaboration be built at two clocks without editing this file, and is
# how `scripts/riscv_clock_ladder.py` and `scripts/soc_sync_ladder.py` pick a
# rung since #439.
#
# 50, and it is measured: `scripts/soc_sync_ladder.py`, 7 rungs x 4 nextpnr
# seeds, `--no-parallel-refine`. 60 closed on 3 of 4 seeds, worst -3.2%; 50 on
# 4 of 4, and the binding domain leaves `clk` for `hr_clk` there -- so a rung
# below 50 buys nothing. Conditions, the whole ladder and the caveat about four
# seeds against a 9 MHz placement distribution: #432, #467.
#
# What binds at 60 is the Wishbone/CSR arbiter's grant fan-out, not the HyperRAM
# path: registering that grant is the work that would lift this.
#
# `--relaxed-btb` (jumpAt = 2) is already on and is NOT the remaining limit --
# see `cpu/cpu.py`, where it was added for exactly this symptom at 57.55 MHz.
SYNC_MHZ = float(variant.value("CYNTHION_SYNC_MHZ"))

# The flash domain is this multiple of `sync`, and the pair is ONE decision.
#
# Both outputs divide one VCO, so `sync` and `fast` cannot be picked independently.
# OFF by default, because at `sync` 60 it buys nothing. See the measurements below.
#
# 60 x 2 = 120.000 MHz would give SCK 60 MHz through the PHY's /2 clock generator.
#
# 144 WAS TRIED AND DOES NOT CLOSE. nextpnr: "Max frequency for clock
# '$glbnet$fast_clk': 124.77 MHz (FAIL at 144.01 MHz)". The PHY is the ONLY thing in
# this domain, so 124.77 MHz is the PHY's own critical path inside a full SoC -- the
# 149 MHz in the chip doc belongs to a design that contained the flash and nothing
# else, and that difference is the whole of the gap.
#
# So 120 is the fastest rung that closes, and it doubles SCK from 30 to 60 MHz.
# Reaching the measured 144 MHz SCK needs BOTH an ODDR clock output -- which removes
# the /2 and would give 120 MHz SCK at this same domain rate -- and timing work on the
# PHY to lift 124.77 toward 144.
FLASH_FAST_RATIO = 2

# Whether the flash PHY gets its own domain at all.
#
# THE MEASUREMENTS, on this SoC, from nextpnr:
#
#     fast 144 MHz -> "Max frequency for '$glbnet$fast_clk': 124.77 MHz (FAIL)"
#     fast 120 MHz -> "Max frequency for '$glbnet$fast_clk': 111.26 MHz (FAIL)"
#
# The PHY is the only thing in that domain, so those are the PHY's own fmax, and the
# spread between two runs at different targets shows it is placement-dependent around
# 110-125 MHz. The 149 MHz in `docs/chips/w25q32-config-flash.md` belongs to a design
# containing the flash and nothing else; that difference is the whole gap.
#
# WHY THAT MAKES THE DOMAIN USELESS ON ITS OWN. `fast` must divide the same VCO as
# `sync` by an integer, so at `sync` 60 the only choices are 60 (SCK 30, no change) and
# 120 (does not close). Getting SCK above 30 this way means dropping `sync` to 50 for
# `fast` 100 -- trading 17% of the CPU for 67% more flash, roughly one for one, on a
# firmware that now executes from flash.
#
# WHAT ACTUALLY UNLOCKS IT is the /2 in `SPIClockGenerator`, which toggles SCK as a
# register in the PHY's domain so SCK can never exceed half of it. Driving SCK through
# an ODDR instead makes SCK equal the domain rate: at `fast` 100 MHz -- which closes --
# that is 100 MHz SCK and roughly 50 MB/s, against 14.95 today, for a CPU that drops
# only 60 to 50 MHz.
#
# So the order is ODDR first, then this. The crossing itself is built and works
# (`peripherals/flash_cdc.py`); it is switched off because turning it on today would cost CPU clock
# for no net gain.
FLASH_PHY_FAST = False

# SCK at the domain rate rather than half it: 60 MHz instead of 30, no clock
# change and no new domain. `peripherals/flash_sck_full.py` has the mechanism --
# a gated clock at the pad, which is the only path a global clock has to
# USRMCLKI -- and what it costs. Apollo's controller reaches the same rate and
# has run byte-exact at 144 MHz SCK on this board (#100).
FLASH_FULL_SCK = False

# Use the DQS HyperRAM PHY rather than the non-DQS one. See #92.
#
# ONE FLAG FOR ONE PHY. The staging path and the BIST engine share the pins, the
# PHY and the controller (`hyperram_share.py`), so this is the whole design's
# choice and #431's instrument/product mismatch cannot exist.
#
# THE ONLY ROUTE PAST ~52 MB/s. `HyperRAMPHY` makes double-rate output from its
# own domain, so CK is `hr` there; `HyperRAMDQSPHY` uses the ECP5's gearing
# primitives with an ECLK at twice `hr`, so the same fabric closure buys twice
# the CK.
#
#   * DQS needs an ECLK at 2x, which `hyperram_clocks.py` supplies as `hr_fast`;
#   * its data path is 32 BITS per beat where the non-DQS one is 16, and
#     `bootram.py`'s `wide`/`second_word` assembly follows this flag.
#
# DEFAULTS OFF, and that is about booting. One bitstream stages firmware through
# this path, and the SoC-side DQS write path has never been on silicon (#186,
# #212) while the non-DQS one is what every boot to date has used. The ceiling
# work sets `CYNTHION_HYPERRAM_DQS=1`, which is also #432's definition of done:
# DQS at CK 160 with the same bitstream able to boot a staged image.
#
# DQS also needs READCLKSEL calibrated (#148); the sweep harness exists and has
# never been run on silicon.
HYPERRAM_DQS = variant.flag("CYNTHION_HYPERRAM_DQS")

# Device CK, in MHz. Independent of SYNC_MHZ: the part runs off the second PLL
# (`hyperram_clocks.py`), which refuses a value it cannot reach exactly rather
# than silently rounding to one it can.
#
# From the environment so that walking the ladder does not mean editing a
# tracked file once per rung.
#
# COMMA-SEPARATED for a runtime-selectable rung -- `CYNTHION_HYPERRAM_CK_MHZ=100,120`
# builds both into one bitstream and the CPU picks one through
# `peripherals/hyperram_ck.py`. Two is the ceiling and the DQS path takes one;
# `hyperram_clocks.py` argues both limits and refuses the rest.
HYPERRAM_CK_RUNGS = [float(ck) for ck in
                     variant.value("CYNTHION_HYPERRAM_CK_MHZ").split(",")]

# Rung 0, for everything that needs a single number. The BIST engine's timing
# constants are derived from it, so a two-rung build gives the OTHER rung the
# delays of this one -- longer in real time at a lower CK, which is the safe
# direction, and the reason rung 0 should be the FASTEST of a pair.
HYPERRAM_CK_MHZ = HYPERRAM_CK_RUNGS[0]

# The highest CK the HyperRAM fabric has been measured to CLOSE at, per path.
# Not the PLL's limit -- `hyperram_clocks.py` covers that -- and not the
# part's, which is 166 MHz.
#
# Refused HERE because the alternative is #410: the failure arrives ~200 s later
# as a raw nextpnr ERROR about `hr_clk`, with nothing naming CK as the cause.
# Same class as `SYNC_CEILING_MHZ` in `clocks.py`, and the same escape hatch --
# the ceiling is one measurement of one design and will move, so raising it is
# an environment variable rather than an edit, and is visible in the command
# that did it. It does not change what elaborates, so it is not in VARIANT_ENV.
#
# Rungs, conditions and the builds behind these two numbers: #410.
HYPERRAM_CK_CEILING_MHZ = {True: 180.0, False: 84.0}


def _refuse_ck_past_the_fabric():
    ceiling = float(os.environ.get("CYNTHION_HYPERRAM_CK_CEILING_MHZ")
                    or HYPERRAM_CK_CEILING_MHZ[HYPERRAM_DQS])
    over = [ck for ck in HYPERRAM_CK_RUNGS if ck > ceiling]
    if not over:
        return
    from hyperram_clocks import reachable_ck

    path = "DQS" if HYPERRAM_DQS else "non-DQS"
    fits = [ck for ck in reachable_ck(ceiling / 2, ceiling,
                                      dqs=HYPERRAM_DQS) if ck <= ceiling]
    raise SystemExit(
        f"CYNTHION_HYPERRAM_CK_MHZ={','.join(f'{ck:g}' for ck in over)} is past "
        f"the {path} fabric ceiling of {ceiling:g} MHz, so place-and-route will "
        f"fail on hr_clk after ~200 s (#410).\n"
        f"  rungs that close, top of the ladder: "
        f"{', '.join(f'{ck:g}' for ck in fits[-6:])}\n"
        f"  CYNTHION_HYPERRAM_CK_CEILING_MHZ raises this if you mean to try one")


_refuse_ck_past_the_fabric()

# The geometry of each of the two L1 caches: 64 sets x 2 ways x 64 B line = 8
# KiB each. A constant rather than a literal at the instantiation because
# `scripts/soc_generate_pac.py` and `gateware/usercode_map.py` both report it,
# and a geometry reported from a different number than the core was generated
# with would be worse than not reporting it.
#
# 8 KiB rather than 4: the matched superloop-vs-RTIC runs in `docs/rtic.md`
# (#245) measured the RTIC dispatcher's +1,700 B of `.text` moving frontend
# stalls from 44/1000 cycles to 452/1000 through a 4 KiB I-cache, while `.bss`
# uses 9,728 bytes of a 63 KiB RAM whose remainder is all stack slack. Code size
# costs real cycles here; data size does not.
#
# Ways are not ruled out by `flash_cache_flush()`: PLRU replacement means a
# sweep of the full cache size still evicts every way, and that flush is only in
# the generated C test firmware -- the Rust firmware uses a real `fence.i`.
#
# 2 ways, because RTIC's handlers are separate instruction working sets that
# preempt each other, and in a DIRECT-MAPPED cache two hot ones sharing an index
# evict each other however large the cache is. That is a conflict miss;
# associativity is the only fix for it, capacity is not.
#
# Block RAM per geometry, off each geometry's own netlist -- 43 (64x1), 47
# (128x1, 32x2), 49 (this), 55 (256x1), 57 (128x2, 32x4). The last two do not
# place: 57 blocks on a die with 56. 3 ways does not exist -- SpinalHDL's PLRU
# asserts `isPow2`.
#
# TIMING SAYS 128x1, and it is the open half of this decision: +3.50 MHz
# [+2.20, +4.80] over 40 seeds a side, 2 blocks cheaper, and it holds at the
# real speed grade (#494, #481). What is not measured is the hit rate that
# 2 ways is for -- `STALLED_CYCLES_FRONTEND` under a preempting workload.
CACHE_SETS = 64
CACHE_WAYS = 2


class AwtoSoc(Elaboratable):
    """This project's SoC. VexiiRiscv RV32IMAC, with:

    - an interrupt controller (17 numbered sources, 10 built) and a CLINT
    - 64 KiB of block RAM, HyperRAM, and memory-mapped SPI flash
    - the board: LEDs, button, VBUS control, ULPI registers, sideband
    - a PAC1954 power monitor behind an I2C mux
    - a flash ILA, a flash pin probe, and a HyperRAM probe
    - a JTAG debug tap, and a JTAG bitstream stager
    - two 16550 consoles: one on USB serial, one on the Apollo UART
    """

    def __init__(self, firmware):
        self.firmware = firmware

    def elaborate(self, platform):
        m = Module()

        # A `fast` domain, for the flash and nothing else.
        #
        # SCK is derived from the PHY's domain, so a flash PHY in `sync` makes the
        # flash rate a function of the CPU clock -- and the CPU clock is chosen
        # for the CPU. HyperRAMPHY needs no `fast`; the flash does.
        #
        # SYNC_MHZ 72 with FLASH_FAST_RATIO 2 solves to exactly 144.000 MHz, which is
        # the fastest rung on the measured table (71.70 MB/s, `0xEB` continuous) and
        # the fastest the flash has ever been driven on this board.
        #
        # The cost is paid deliberately: a PLL output, a global buffer, and
        # CLKOP_DIV forced even, which restricts which sync frequencies are
        # reachable. 72 is reachable and is inside the 72-91 MHz nextpnr already
        # estimates for this design.
        # The DQS PHY's ECLK is `hr_fast`, off the second PLL, so it does not
        # ask this one for a `fast`.
        # `usb` comes from the 60 MHz oscillator directly, not from this PLL --
        # the FPGA sources the ULPI clock, so it is exactly 60.000 by
        # construction. That is what frees `sync`: it does not share a VCO with
        # a domain pinned to 60. See `clocks.py`.
        m.submodules.car = car = SocClocks(
            sync_mhz=SYNC_MHZ, with_fast=FLASH_PHY_FAST,
            fast_ratio=FLASH_FAST_RATIO)

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
            f"base={HYPERRAM_BASE:08x},size={HYPERRAM_SIZE:08x},main=1,exe=1",
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
            # IRQ_POWER_ALERT and IRQ_BUTTON are wired where their resources are
            # requested, some way below: a resource may be requested once, and
            # this block runs first. IRQ_PLL_LOSS is wired below too, beside the
            # synchroniser it needs.
        ]

        # The same lines, keyed by the decoder window each peripheral lives in,
        # so `scripts/soc_generate_pac.py` can put an <interrupt> element on the
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
            "board_gpio":    {"BUTTON":       IRQ_BUTTON},
            "board_clocks":  {"PLL_LOSS":     IRQ_PLL_LOSS},
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
        # The PHY runs in `fast`, and `ClockCrossedPHY` presents it in `sync`.
        #
        # SCK comes from the PHY's domain, so this is the whole of what decouples the
        # flash rate from the CPU clock. Everything upstream -- the mmap core, the
        # controller, the crossbar -- is unchanged and still beside the CPU; the wrapper
        # has the same flipped SPIControlPort, so the crossbar cannot tell.
        #
        # `peripherals/flash_cdc.py` has the argument for FIFOs over a timing constraint, and for
        # synchronising `cs` rather than queueing it.
        flash_phy_domain = "fast" if FLASH_PHY_FAST else "sync"
        flash_phy_inner = ObservablePHY(pads=flash_bus, divisor=FLASH_DIVISOR,
                                        domain=flash_phy_domain,
                                        full_sck=FLASH_FULL_SCK)
        if FLASH_PHY_FAST:
            flash_phy = ClockCrossedPHY(flash_phy_inner, phy_domain="fast")
        else:
            flash_phy = flash_phy_inner
        m.submodules.flash_phy = flash_phy
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

        # HyperRAM: one PHY, one controller, and a boot-time mode saying who
        # drives them (#432).
        #
        #     mode 0  STAGE  the memory window and both staging paths
        #     mode 1  BIST   the ceiling engine
        #
        # The masters are never concurrent, because the boot sequence frees the
        # part: stage an image into HyperRAM, the resident bootloader copies it
        # into block RAM, jump. That is what makes a firmware change cost
        # seconds instead of a ~60 s resynthesis, and what leaves the part with
        # no live user afterwards.
        #
        # PHY, controller and engine are all `hr`, off the second PLL, so a CK
        # rung does not drag the console divisor or the CLINT tick with it.
        # BootRAM stays in `sync` and reaches them through `HyperRAMHandover`.
        from hyperram_clocks import HyperRAMDomains
        from hyperram_share import HyperRAMHandover, HyperRAMShared
        from peripherals.hyperram_bist import HyperRAMBist
        from peripherals.hyperram_ck import HyperRAMClockSelect

        # The DEVICE number goes in, not the fabric one: the DQS PHY emits two
        # CK per `hr` cycle, so `hr = ck / 2` there, and taking `ck_mhz` here
        # means a caller cannot get that factor of two wrong.
        m.submodules.hr_car = hr_car = HyperRAMDomains(
            ck_mhz=HYPERRAM_CK_RUNGS, dqs=HYPERRAM_DQS)
        # `usb` rather than `clk_60MHz`: the SoC's own generator has already
        # requested that resource, and Amaranth allows one requester. `usb`
        # is the same 60 MHz -- the FPGA sources the ULPI clock from it --
        # so the two PLLs see the same rate, if not the same net.
        m.d.comb += hr_car.clki.eq(ClockSignal("usb"))

        hr_domains = DomainRenamer({"sync": "hr", "fast": "hr_fast"})
        m.submodules.hyper_ram = hyper_ram = hr_domains(HyperRAMShared(
            dqs=HYPERRAM_DQS, ck_mhz=HYPERRAM_CK_MHZ,
            latency_clocks=HYPERRAM_LATENCY_CLOCKS))

        # `sustained` is left at its default of False, and that is a decision
        # about the MASTER: `RegisteredResponse` below withholds STB for a cycle
        # after every acknowledgement, so the CPU delivers a 32-bit beat every
        # three cycles where a held-open HyperBus transaction consumes two words
        # -- one per CK, with no way to stall it. Coalescing under that deficit
        # wrote 48 words for a 32-word line and transposed every odd beat, which
        # is the fault `hr cross` reports and section 9 of
        # `scripts/soc_hyperram_sim.py` reproduces. It is also what a
        # transaction-at-a-time crossing cannot carry; see `HyperRAMHandover`.
        #
        # `clock_stop` (Active Clock Stop, `ClockStopPHY`) is what would let
        # `sustained` be true, and it sits under a PHY this module no longer
        # owns. Both stay off; #185 and the async bridge in #425 are where they
        # come back.
        #
        # `ck_mhz` is passed rather than duplicated. The tCSM burst cap is a
        # TIME limit expressed in words, so it has to know CK -- which is now
        # the second PLL's rung and not a function of `SYNC_MHZ` at all.
        m.submodules.hyper_handover = hyper_handover = HyperRAMHandover(
            port=hyper_ram.stage, width=hyper_ram.width)
        m.submodules.bootram = bootram = BootRAM(
            dqs=HYPERRAM_DQS, ck_mhz=HYPERRAM_CK_MHZ,
            interface=hyper_handover.interface)
        bootram_bridge = WishboneCSRBridge(bootram.port.bus, data_width=32)
        m.submodules.bootram_bridge = bootram_bridge
        decoder.add(bootram_bridge.wb_bus, addr=BOOTRAM_BASE, name="bootram")

        # This extra decoder window is the timing risk in #90: its address compare is
        # on the path that needed RegisteredResponse to recover Fmax. Simulation can
        # establish protocol and data integrity; only a build can measure the margin.
        decoder.add(bootram.mmap.bus, addr=HYPERRAM_BASE, name="hyperram")

        # The HyperRAM transaction counters (#173). Inputs only, taken from
        # signals `BootRAM` already computes, so this cannot alter the timing of
        # the thing it measures.
        m.submodules.hyper_probe = hyper_probe = HyperRAMProbe()
        # The PHY's three status bits are `hr`; the probe counts in `sync`.
        # Levels, and nothing acts on them, so two flops each is the whole
        # crossing.
        dll_locked = Signal()
        dll_ready = Signal()
        burstdet = Signal()
        m.submodules += [
            FFSynchronizer(hyper_ram.stage.dll_locked, dll_locked),
            FFSynchronizer(hyper_ram.stage.dll_ready, dll_ready),
            FFSynchronizer(hyper_ram.stage.burstdet, burstdet),
        ]
        m.d.comb += [
            hyper_probe.start_transfer.eq(bootram.probe_start),
            hyper_probe.beat.eq(bootram.probe_beat),
            hyper_probe.is_burst.eq(bootram.probe_burst),
            hyper_probe.word.eq(bootram.probe_word),
            hyper_probe.busy.eq(bootram.probe_busy),
            hyper_probe.want.eq(bootram.probe_want),
            hyper_probe.arming.eq(bootram.probe_arming),
            hyper_probe.cyc.eq(bootram.probe_cyc),
            hyper_probe.dll_locked.eq(dll_locked),
            hyper_probe.dll_ready.eq(dll_ready),
            hyper_probe.burstdet.eq(burstdet),
            hyper_probe.stall.eq(bootram.probe_stall),
        ]
        # The DQS tap, out to the PHY through the staging side of the mux. Two
        # flops for metastability, not for coherence: the bits are a
        # configuration this firmware changes between passes, and a change is
        # already a step nothing is timed across.
        m.submodules += FFSynchronizer(hyper_probe.sel[:4],
                                       Cat(hyper_ram.stage.readclksel,
                                           hyper_ram.stage.read_phase),
                                       o_domain="hr")
        hyper_probe_bridge = WishboneCSRBridge(hyper_probe.bus, data_width=32)
        m.submodules.hyper_probe_bridge = hyper_probe_bridge
        decoder.add(hyper_probe_bridge.wb_bus, addr=HYPERRAM_PROBE_BASE,
                    name="hyperram_probe")

        # The BIST engine, on the other side of the mode mux. It runs in `hr`,
        # so device CK is not the CPU clock.
        # `scripts/soc_bist_transport_sim.py` is where the CPU's read of an
        # engine-driven register is shown to survive unequal clocks -- in 1.4 s,
        # against three failures on the retired branch that each cost a ~90 s
        # synthesis and a reconfigure to diagnose.
        hyper_bist = HyperRAMBist(ck_mhz=HYPERRAM_CK_MHZ, dqs=HYPERRAM_DQS,
                                  port=hyper_ram.bist)
        m.submodules.hyper_bist = hyper_bist
        hyper_bist_bridge = WishboneCSRBridge(hyper_bist.bus, data_width=32)
        m.submodules.hyper_bist_bridge = hyper_bist_bridge
        decoder.add(hyper_bist_bridge.wb_bus, addr=HYPERRAM_BIST_BASE,
                    name="hyperram_bist")

        # Which CK rung is live, who owns the part, and what the rungs ARE.
        # Present even with one rung: a driver then reads what it has instead
        # of being built to know, and `rungs`/`rung0` answer the same way
        # either way.
        hyper_ck = HyperRAMClockSelect(ck_rungs=hr_car.ck_rungs)
        m.submodules.hyper_ck = hyper_ck
        m.d.comb += [
            hr_car.sel.eq(hyper_ck.sel),
            hyper_ck.locked.eq(hr_car.locked),
            hyper_ram.sel.eq(hyper_ck.bist),
            hyper_ck.mode.eq(hyper_ram.mode),
            hyper_ck.refused.eq(hyper_handover.refused),
            hyper_handover.refused_clear.eq(hyper_ck.refused_clear),
        ]
        hyper_ck_bridge = WishboneCSRBridge(hyper_ck.bus, data_width=32)
        m.submodules.hyper_ck_bridge = hyper_ck_bridge
        decoder.add(hyper_ck_bridge.wb_bus, addr=HYPERRAM_CK_BASE,
                    name="hyperram_ck")

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

            cpu.ext_reset.eq(stager.cpu_reset),
        ]

        m.d.comb += [
            bootram.jtag_req.eq(stager.req),
            bootram.jtag_addr.eq(stager.addr),
            bootram.jtag_data.eq(stager.data),
            stager.ack.eq(bootram.jtag_ack),
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
        # is not simply a register on `ack`, and scripts/soc_bus_sim.py for the
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

        # Before the USBDevice elaborates: luna names the second of CDC's two IN
        # endpoints after `id()`, which is an address and changes every process
        # (#441). Endpoint number instead, so the RTLIL repeats.
        from luna_stable_names import stable_endpoint_names
        stable_endpoint_names()

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
        # This is a register path only. There is no UTMI translator, no packet
        # handling and no device stack on this port -- see `peripherals/ulpi_window.py`.
        target_phy = platform.request("target_phy", 0)
        m.d.comb += [
            target_phy.clk.o.eq(ClockSignal("usb")),
            target_phy.rst.o.eq(car.phy_reset | target_ulpi.phy_rst),
            target_phy.stp.o.eq(target_ulpi.stp_o),
            target_phy.data.o.eq(target_ulpi.data_o),
            target_phy.data.oe.eq(target_ulpi.data_oe),
            target_ulpi.data_i.eq(target_phy.data.i),
            target_ulpi.dir_i.eq(target_phy.dir.i),
            target_ulpi.nxt_i.eq(target_phy.nxt.i),
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
        print("build it with ./scripts/riscv_firmware.py")
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
    # is the C generator's layout (`scripts/riscv_firmware.py`), which has no bootloader
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
    # JTAG (`scripts/soc_jtag_stage.py`) or the console (`load`) and touches neither.
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

    # THE VENDORED PLATFORM, `gateware/board/cynthion_r1_4.py`.
    #
    # This imported the installed `cynthion` package until #416, on the reasoning
    # that `repos/cynthion/`'s copy pulls in `amaranth_boards`. True of THAT
    # copy, and never true of the vendored one -- it takes `LEDResources`,
    # `ULPIResource` and `CynthionPlatform` from local modules for exactly this
    # reason, which is what vendoring it was for.
    #
    # The cost of not switching: the vendored file's one deliberate divergence is
    # the HyperRAM pins' electrical attributes (#311), so the SoC built with NO
    # `DRIVE` on any RAM pin -- silicon default, the condition #311 was filed
    # about -- while the file that fixes it sat unused. Pin LOCATIONS are
    # identical in both, so every build was correct about where nets land and
    # nothing failed.
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
        device=platform.device,
        speed=platform.speed,
        sync_hz=round(solved.actual_sync_mhz * 1e6),
        usb_hz=round(solved.actual_usb_mhz * 1e6),
        cache_sets=CACHE_SETS, cache_ways=CACHE_WAYS,
        isa=vexii_cpu.isa_generated())
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
