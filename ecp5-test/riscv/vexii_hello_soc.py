#!/usr/bin/env python3
#
# A RISC-V core on block RAM, printing over USB CDC-ACM on the AUX port.
# SPDX-License-Identifier: BSD-3-Clause

"""
A VexiiRiscv SoC on block RAM: a core, memory, and a way to see it run.

    ./ecp5-test/riscv/vexii_hello_soc.py --build
    ./ecp5-test/riscv/vexii_hello_soc.py --build --program

Block RAM is single-cycle and needs no cache, bus wrapper or latency tuning, so
the only things that can be wrong are the CPU, its reset vector and the
peripheral it writes to.

## Two consoles, one register map

    index  peripheral   transport                       appears as
    0      Uart16550    USB CDC-ACM on the AUX port     /dev/ttyACM*
    1      Uart16550    async serial on R14/T14         Apollo's CDC

Same NS16550A register map, same driver, different transport -- which is the
point of a standard part. `serial_line.py` is the PHY behind index 1.

  * **USB carries the primary console** because R14/T14 are the same wires as
    JTAG TDI/TMS, so a design driving them competes with the thing loading its
    own bitstream. The CDC gateware measures 195.4 Mbps loopback
    (`../../docs/usb-performance.md`).
  * **A standard 16550, not a bespoke peripheral**, because LSR at +5 cannot
    share a 32-bit word with RBR at +0. See `uart16550.py`, and
    `../../docs/architecture.md` for what that replaced.

## Interrupts

Both UARTs' `irq` lines go to a standard RISC-V PLIC (`vexii_plic.py`), whose
output is the CPU's single machine external interrupt, so the consoles are
interrupt-driven.

The machine timer and software interrupts come from a standard RISC-V CLINT
(`vexii_clint.py`), comparing against the same counter `rdtime` reads. A 1 ms
tick is `mtimecmp += period` in the handler; `firmware/cynthion-soc/src/timer.rs`
is the driver.

QEMU's `-M virt` has both a PLIC and a CLINT, so `src/plic.rs` and `src/timer.rs`
compile unchanged for both and `scripts/soc_test.py` exercises the interrupt
paths that ship.

## Two ways to load firmware

    path                     needs                       script
    -----------------------  --------------------------  -----------------------
    JTAG ER1 into HyperRAM   a configured FPGA           `soc_jtag_stage.py`
    console into HyperRAM    a running shell             `soc_payload.py`

The JTAG sink holds the CPU in reset while it writes, so it works on a board whose
console is wedged -- which the console path by definition cannot. Both leave the same
header for the bootloader. See `jtag_stage.py`.

## Board peripherals

GPIO (six LEDs, PWRDN, the USER button), a multiplexed I2C master reaching the
PAC1954 and both FUSB302Bs, a ULPI register window on the TARGET PHY, the
sideband link to Apollo, and memory-mapped SPI flash.

Peripheral base addresses are generated from this SoC's own memory map by
`scripts/soc_generate_pac.py`; `scripts/check.py` fails if firmware writes one
as a literal.
"""

import argparse
import sys
from pathlib import Path

from amaranth                       import (Elaboratable, Module, Mux, Signal,
                                            Cat, ClockSignal, ResetSignal)
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
from jtag_stage import JTAGStager, UserJTAG
from uart16550 import Uart16550
from vexii_plic import Plic
from vexii_clint import Clint
from serial_line import SerialLine
from i2c_master import I2CMaster, prescale_for
from sideband_csr import SidebandControl
from vbus_csr import VbusControl
from gateware_id import GatewareId
from ulpi_window import UlpiRegisters
from i2c_mux import (I2CBusMux, BUS_TARGET_C as I2C_MUX_TARGET_C,
                     BUS_AUX_C as I2C_MUX_AUX_C,
                     BUS_POWER_MONITOR as I2C_MUX_POWER)
from stream_buffer import StreamBuffer
from wishbone_pipe import RegisteredResponse
from flash_cdc import ClockCrossedPHY
from hyperram_probe import HyperRAMProbe
from vexii_flash import (FairSPIControlPortCrossbar, FlashILA, FlashPinProbe,
                         HoldableSPIController, ModalSPIFlashMemoryMap,
                         ObservablePHY, QSPIFlashPins)

from amaranth_soc                   import csr, gpio, wishbone
from amaranth_soc.wishbone          import Decoder

ROOT = Path(__file__).resolve().parent.parent.parent

# 64 KiB at address zero, matching what moondancer allocates. The reset vector
# is 0x00000000, so the bootloader's entry point must be the first instruction.
RAM_BASE = 0x00000000
RAM_SIZE = 64 * 1024

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
#   +0x40  gateware  32 bytes  gateware_id.GatewareId
#
# The sub-addresses are the peripherals' natural sizes and each window is
# aligned to its own size, which is what MemoryMap requires. The decoder is 128
# bytes rather than the 32 the first three fit in, because a peripheral added
# later should not have to move an existing one -- and moving one changes every
# address in the generated PAC at once.
#
# `gateware` is not a board peripheral and is here anyway: it is the one window
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
GATEWARE_BASE  = BOARD_BASE + 0x40

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

# The machine timer and software interrupts: a standard RISC-V CLINT, in its own
# 64 KiB window. Same reasoning as PLIC_BASE above -- the offsets inside are not
# negotiable (mtime is at 0xbff8), the window must be aligned to its size and
# inside the `main=0` CSR region, and it must clear the PLIC's 4 MiB below it.
#
# QEMU's `-M virt` puts its CLINT at 0x02000000 (`clint@2000000` in the device
# tree), so again the difference is one constant in
# firmware/cynthion-soc/src/target.rs and src/timer.rs is the same code on both.
CLINT_BASE = 0xf0800000

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

# The two FUSB302Bs' `int` lines, ONE SOURCE EACH.
#
# Fourth and fifth, so nothing above them renumbers -- TARGET keeps the number
# the OR-ed source had. These were one source until #135; the reasoning that put
# them there was true about the I2C mux and false about the PLIC. With one muxed
# controller only one device can be addressed at a time, so this buys no
# concurrency and never will. What it buys is knowing WHICH, and it deletes an
# obligation rather than documenting one:
#
# A SHARED LEVEL MUST BE CLEARED ON EVERY ASSERTING DEVICE before the source is
# re-enabled, or the line stays high and the interrupt re-fires immediately -- a
# storm that presents as a hung CPU, which is the trap
# `docs/chips/fusb302b-type-c.md` warns about and the symptom this project has
# repeatedly misread. One source per device makes that unmissable by
# construction: there is only ever one device behind the level being cleared.
#
# The PLIC supports 31 sources and this design now uses 5, so the OR conserved
# nothing scarce. See `docs/architecture.md` decision 8 for the reversal.
#
# What does NOT change: the handler still defers. Clearing is ~1 ms of I2C on the
# controller the foreground also uses, so `src/irq.rs` masks the asserting source
# and records the event, and normal context clears that one device and re-enables
# that one source. Per-device masking means a deferred TARGET no longer blinds
# AUX, which the shared source could not offer.
#
# `fault` gets no source at all. It means something different from `int`, and
# nothing in the firmware can clear it -- it drops when the device's fault does.
# An interrupt on an uncleanable level would have to stay masked until a poll saw
# the level go away, so it would add a handler and keep the poll. It stays in
# LINES, read every 50 ms by `src/typec.rs`.
IRQ_TYPE_C_TARGET = 4
IRQ_TYPE_C_AUX = 5

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
# first, because there is nothing in it to get subtly wrong. "quad" is 0xeb,
# address and data on four lanes with `dummy_value=0xff0000`.
# See ecp5-test/riscv/vexii_flash.py.
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
# TWO CLAIMS THAT USED TO BE HERE ARE WRONG, and they are why this sat at 30 MHz.
# `docs/chips/w25q32-config-flash.md` supersedes both:
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

# The CPU clock. `usb` stays at 60 MHz inside the domain generator -- the ULPI PHY
# requires it and it is not a free parameter -- while this is arbitrary.
#
# 60, and it is now pinned by the FLASH rather than by the CPU -- the opposite of what
# the old comment here described.
#
# `sync` no longer has to serve the flash: the PHY has its own domain. But both outputs
# divide ONE VCO, so the ratio is an integer and `fast = 2 x sync`. The flash domain
# closes at 124.77 MHz in this design (measured -- see FLASH_FAST_RATIO), so `fast`
# must be <= 120 and `sync` is therefore 60.
#
# The CPU could run faster alone: "the design already meets 72-91 MHz by nextpnr's own
# estimate, and the die is a 25F sharing a speed grade with the 12F it is marked as
# (#116). See #110." Reaching that WITH a fast flash needs a third PLL output or a
# non-integer ratio, neither of which this generator offers.
SYNC_MHZ = 30

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
# (`flash_cdc.py`); it is switched off because turning it on today would cost CPU clock
# for no net gain.
FLASH_PHY_FAST = False

# Use the DQS HyperRAM PHY rather than the non-DQS one. See #92.
#
# THE ONLY ROUTE PAST ~52 MB/s. `HyperRAMPHY` makes double-rate output from `sync`
# alone, so CK is `SYNC_MHZ` and the bus tops out with it. `HyperRAMDQSPHY` uses
# the ECP5's gearing primitives with an ECLK at 2x, which is what lets CK rise
# toward the 192 the withdrawn 334 MB/s figure was taken
# at.
#
# Two consequences, both real work rather than a flag flip:
#
#   * it REQUIRES a `fast` domain -- `HyperRAMDQSPHY` reads `ClockSignal("fast")`
#     for every ECLK -- so `with_fast` is forced on below;
#   * its data path is 32 BITS per beat where the non-DQS one is 16, so the
#     `wide`/`second_word` assembly in `vexii_bootram.py` -- which exists only to
#     build a 32-bit Wishbone beat out of two 16-bit words -- has nothing to do
#     and must be bypassed rather than left to halve the rate.
#
# It also needs READCLKSEL calibrated (#148); the sweep harness exists and has
# never been run on silicon.
HYPERRAM_DQS = False

# Active Clock Stop, and coalescing with it. See #185.
#
# These go together: `sustained` is only legal when the master can stall the
# device, and Active Clock Stop is the only thing that lets it. Section 11 of
# `scripts/soc_hyperram_sim.py` has the pair returning 16/16 both ways in one
# transaction against section 9's 8/16 in 48 words.
#
# On silicon the READ half is unproven -- the model returns read data in the
# same cycle as the CK that asked for it and hardware does not. The delay is
# swept at runtime via `hr sel` bits 5:4 rather than guessed. Turn this on to
# run that sweep; leave it off otherwise.
HYPERRAM_CLOCK_STOP = False

# Sets in each of the two L1 caches, one way each. A constant rather than a
# literal at the instantiation because `gateware_id.py` reports it to the
# firmware, and a geometry reported from a different number than the one the
# core was generated with would be worse than not reporting it.
CACHE_SETS = 64


class HelloSoC(Elaboratable):
    """VexRiscv, 64 KiB of block RAM, and a USB serial console."""

    def __init__(self, firmware):
        self.firmware = firmware

    def elaborate(self, platform):
        m = Module()

        # A `fast` domain, for the flash and nothing else.
        #
        # This used to say "no `fast` domain", because the only candidate was
        # HyperRAMDQSPHY and we do not use it -- HyperRAMPHY makes double-rate output
        # from `sync` alone. The flash is a second candidate and a better one: SCK is
        # derived from the PHY's domain, so while the PHY sat in `sync` the flash rate
        # was a function of the CPU clock, and the CPU clock is chosen for the CPU.
        #
        # SYNC_MHZ 72 with FLASH_FAST_RATIO 2 solves to exactly 144.000 MHz, which is
        # the fastest rung on the measured table (71.70 MB/s, `0xEB` continuous) and
        # the fastest the flash has ever been driven on this board.
        #
        # The cost the old comment names is real and is now paid deliberately: a PLL
        # output, a global buffer, and CLKOP_DIV forced even, which restricts which
        # sync frequencies are reachable. 72 is reachable and is inside the 72-91 MHz
        # nextpnr already estimates for this design.
        # `fast` is needed if EITHER the flash PHY is decoupled or the HyperRAM
        # uses its DQS PHY -- the latter reads `ClockSignal("fast")` for every
        # ECLK, so it cannot elaborate without one.
        m.submodules.car = car = VariableClockDomainGenerator(
            sync_mhz=SYNC_MHZ, with_fast=FLASH_PHY_FAST or HYPERRAM_DQS,
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

        cpu = VexiiRiscv(reset_addr=RAM_BASE, cache_sets=64, regions=regions)
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

        # What this bitstream is, so the firmware can say whether it was built
        # against this one. The frequencies are what the PLL solver landed on
        # rather than what was asked for -- see gateware_id.py.
        m.submodules.gateware_id = gateware_id = GatewareId(
            sync_hz=round(car.actual_sync_mhz * 1e6),
            usb_hz=round(car.actual_usb_mhz * 1e6),
            cache_sets=CACHE_SETS)

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
        board_csr.add(gateware_id.bus,   addr=GATEWARE_BASE - BOARD_BASE,
                      name="gateware")

        board_bridge = WishboneCSRBridge(board_csr.bus, data_width=32)
        m.submodules.board_bridge = board_bridge
        decoder.add(board_bridge.wb_bus, addr=BOARD_BASE, name="board")

        m.submodules.plic = plic = Plic(sources=5)
        m.d.comb += [
            plic.sources[IRQ_CONSOLE].eq(console.irq),
            plic.sources[IRQ_APOLLO].eq(apollo_uart.irq),
            plic.sources[IRQ_I2C].eq(i2c.irq),
            plic.sources[IRQ_TYPE_C_TARGET].eq(i2c_mux.target_irq),
            plic.sources[IRQ_TYPE_C_AUX].eq(i2c_mux.aux_irq),
        ]

        # The same five lines, keyed by the decoder window each peripheral lives
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
        #
        # A dict value rather than a number where ONE window raises more than one
        # source, which the mux does: each entry becomes its own <interrupt> and
        # its own `<WINDOW>_<SUFFIX>_IRQ` constant. SVD allows several per
        # peripheral and svd2rust wants their names distinct, which is also what
        # firmware wants -- the two numbers are not interchangeable.
        self.interrupt_sources = {
            "console":       IRQ_CONSOLE,
            "apollo_uart":   IRQ_APOLLO,
            "board_i2c":     IRQ_I2C,
            "board_i2c_mux": {"TARGET": IRQ_TYPE_C_TARGET,
                              "AUX":    IRQ_TYPE_C_AUX},
        }

        plic_bridge = WishboneCSRBridge(plic.bus, data_width=32)
        m.submodules.plic_bridge = plic_bridge
        decoder.add(plic_bridge.wb_bus, addr=PLIC_BASE, name="plic")

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
        # `flash_cdc.py` has the argument for FIFOs over a timing constraint, and for
        # synchronising `cs` rather than queueing it.
        flash_phy_domain = "fast" if FLASH_PHY_FAST else "sync"
        flash_phy_inner = ObservablePHY(pads=flash_bus, divisor=FLASH_DIVISOR,
                                        domain=flash_phy_domain)
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

        # HyperRAM, and the two paths that stage firmware into it.
        #
        # This is what makes a firmware change cost seconds instead of a ~60 s
        # resynthesis: the image goes into HyperRAM and a resident bootloader copies
        # it into block RAM. See ecp5-test/riscv/vexii_bootram.py.
        from vexii_bootram import BootRAM

        # `sustained` is left at its default of False, and that is a decision
        # about the MASTER: `RegisteredResponse` below withholds STB for a cycle
        # after every acknowledgement, so the CPU delivers a 32-bit beat every
        # three cycles where a held-open HyperBus transaction consumes two words
        # -- one per CK, with no way to stall it. Coalescing under that deficit
        # wrote 48 words for a 32-word line and transposed every odd beat, which
        # is the fault `hr cross` reports and section 9 of
        # `scripts/soc_hyperram_sim.py` reproduces.
        #
        # `clock_stop` (Active Clock Stop, `ClockStopPHY`) is what would let
        # `sustained` be true. Section 11 of that same file has it returning
        # 16/16 both ways in one transaction; it has never been on silicon, and
        # the read half of it depends on a round-trip latency the model does not
        # represent. Both stay off until a board run says otherwise.
        #
        # `ck_mhz` is passed rather than duplicated. The tCSM burst cap is a TIME
        # limit expressed in words, so it has to know CK: `HyperRAMPHY` emits one
        # CK per `sync` cycle, `HyperRAMDQSPHY` gears off `fast` at twice that.
        # `riscv_clock_ladder.py` rewrites `SYNC_MHZ` here, and a second copy of
        # the number inside `vexii_bootram` would drift the first time it did.
        m.submodules.bootram = bootram = BootRAM(
            dqs=HYPERRAM_DQS, ck_mhz=2 * SYNC_MHZ if HYPERRAM_DQS else SYNC_MHZ,
            clock_stop=HYPERRAM_CLOCK_STOP, sustained=HYPERRAM_CLOCK_STOP)
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
        m.d.comb += [
            hyper_probe.start_transfer.eq(bootram.probe_start),
            hyper_probe.beat.eq(bootram.probe_beat),
            hyper_probe.is_burst.eq(bootram.probe_burst),
            hyper_probe.word.eq(bootram.probe_word),
            hyper_probe.busy.eq(bootram.probe_busy),
            hyper_probe.want.eq(bootram.probe_want),
            hyper_probe.arming.eq(bootram.probe_arming),
            hyper_probe.cyc.eq(bootram.probe_cyc),
            hyper_probe.dll_locked.eq(bootram.probe_dll_locked),
            hyper_probe.dll_ready.eq(bootram.probe_dll_ready),
            hyper_probe.burstdet.eq(bootram.probe_burstdet),
            hyper_probe.stall.eq(bootram.probe_stall),
            bootram.readclksel.eq(hyper_probe.sel),
            bootram.read_stall_cycles.eq(hyper_probe.sel[4:6]),
        ]
        hyper_probe_bridge = WishboneCSRBridge(hyper_probe.bus, data_width=32)
        m.submodules.hyper_probe_bridge = hyper_probe_bridge
        decoder.add(hyper_probe_bridge.wb_bus, addr=HYPERRAM_PROBE_BASE,
                    name="hyperram_probe")

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

            bootram.jtag_req.eq(stager.req),
            bootram.jtag_addr.eq(stager.addr),
            bootram.jtag_data.eq(stager.data),
            stager.ack.eq(bootram.jtag_ack),

            cpu.ext_reset.eq(stager.cpu_reset),
        ]

        # The machine external interrupt, from the PLIC.
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
            cpu.irq_external.eq(plic.irq_out),
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
        m.submodules.bus_pipe = bus_pipe = RegisteredResponse(
            addr_width=30, data_width=32, granularity=8,
            features={"cti", "bte", "err"})
        wiring.connect(m, arbiter.bus, bus_pipe.intr_bus)
        wiring.connect(m, bus_pipe.sub_bus, decoder.bus)

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
        # claimed (sideband bit 5, `ecp5-test/sideband_advertise.py`), TARGET is the port
        # under test, and AUX belongs to the FPGA outright.
        bus = platform.request("aux_phy", 0)

        # 512 is the high-speed bulk maximum. The default of 64 is the full-speed limit,
        # which enumerates at high speed and then runs at an eighth of the achievable rate.
        # ID from the central allocation in ecp5-test/usb_ids.py, never a locally chosen
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
        sys.path.insert(0, str(ROOT / "ecp5-test"))
        from sideband_debug import SidebandDebug
        # The sideband's bit period is a cycle count derived from the domain frequency, so a
        # design that raises `sync` and leaves this at its default gets a DEAD link rather
        # than a slow one -- a UART tolerates about +/-2% and the error scales with the
        # clock. Passing SYNC_MHZ keeps the two in step by construction.
        # The ACTUAL solved frequency, not `SYNC_MHZ`. The PLL does not always
        # land on the request -- `GatewareId` above already reports
        # `car.actual_sync_mhz` for exactly that reason -- and the baud divisor
        # is computed from this at elaboration with nothing checking it after.
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
            # A byte each way, so the link carries a message and not only a
            # heartbeat. See sideband_csr.py for the register discipline.
            sideband.message.eq(sideband_ctrl.message),
            sideband_ctrl.received.eq(sideband.received),
            sideband_ctrl.received_strobe.eq(sideband.received_strobe),
            # Asking Apollo for the CONTROL port. Off until firmware sets bit 5,
            # so this AUX-only design keeps behaving exactly as it did.
            sideband.advertise.eq(sideband_ctrl.advertise),

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
        # `control_vbus_in_en` and `aux_vbus_in_en` are deliberately NOT
        # requested. They are the board's own input shutoff on the two ports that
        # power it, backed by hardware overvoltage protection above 5.5 V (D17, a
        # 5.6 V zener). Nothing in this SoC has a reason to command a power input
        # closed, and an undriven output is one this design cannot get wrong.
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

        # ---- the TARGET PHY's ULPI bus --------------------------------------
        #
        # The FPGA sources the clock (`clk_dir='o'` in the platform), so the PHY
        # runs at whatever `usb` runs at -- 60 MHz, which is what a USB3343
        # requires and is why `usb` is not a free parameter the way `sync` is.
        #
        # `rst` is declared `rst_invert=True`, so the pad is active low and a 1
        # here holds the PHY in reset. Driving it from `ResetSignal("usb")`
        # means the PHY comes out of reset with the domain and is held only
        # while the domain is, which is what a PHY expects; tying it to 0 would
        # leave a PHY that had glitched during configuration with no way back.
        #
        # This is a register path only. There is no UTMI translator, no packet
        # handling and no device stack on this port -- see `ulpi_window.py`.
        target_phy = platform.request("target_phy", 0)
        m.d.comb += [
            target_phy.clk.o.eq(ClockSignal("usb")),
            target_phy.rst.o.eq(ResetSignal("usb")),
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
                        default=ROOT / "tmp" / "rust_boot.bin",
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

    # The installed cynthion package, not the in-repo source tree: the repo
    # copy pulls in amaranth_boards, which is not installed here, while the
    # packaged platform has no such dependency.
    from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4

    build_dir = ROOT / "tmp" / "vexii_hello" / "build"

    # No `**ecppack_opts()` here, and it was tried: `CynthionPlatformRev1D4`
    # passes its own `ecppack_opts` in `toolchain_prepare`
    # (`repos/cynthion/.../platform/core.py:59-64`) before **kwargs, so supplying
    # one is a duplicate keyword and the build fails outright. Stamping this
    # bitstream's USERCODE therefore means patching the vendored platform.
    #
    # Until then the identity lives in a register instead -- `gateware_id.py`,
    # same encoding, read by the CPU rather than by JTAG. USERCODE is not
    # fabric-readable on this part in any case: it is a command in the
    # bitstream's command stream rather than a bit in a tile, so there is
    # nothing for a primitive to read.
    CynthionPlatformRev1D4().build(
        HelloSoC(firmware=words),
        do_program=args.program,
        build_dir=str(build_dir))

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
