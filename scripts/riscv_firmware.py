#!/usr/bin/env python3
#
# Build RV32 firmware for the bring-up SoC.
# SPDX-License-Identifier: BSD-3-Clause

"""
Builds bare-metal RV32 firmware to run on the block-RAM SoC.

Three targets, in increasing order of how much has to work for them to run:

    hello       a few dozen instructions: set the stack, write a string to the
                console register, halt. If this does not print, the fault is
                the CPU, the reset vector or the peripheral -- there is nothing
                else it could be.
    dhrystone   the classic integer benchmark. Needs a working stack, .data and
                .bss, and enough of libc to be interesting.
    coremark    the benchmark the earlier RV32 comparison quoted but never ran
                here. Phase 2 of the bring-up wants this number.

Start with `hello`. Debugging a benchmark harness and a CPU bring-up at the
same time is how a week disappears.

The toolchain is riscv64-linux-gnu-gcc, which targets Linux but produces fine
bare-metal RV32 with -nostdlib and an explicit -march/-mabi. No separate
embedded toolchain is needed.

    ./scripts/riscv_firmware.py
    ./scripts/riscv_firmware.py --target hello
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from devlog import emit, spawn  # noqa: E402
OUT = ROOT / "tmp" / "riscv_hello"

# CoreMark is vendored in the VexiiRiscv submodule rather than fetched.
COREMARK = ROOT / "repos" / "vexiiriscv" / "ext" / "NaxSoftware" / "baremetal" / "coremark"
PORT = ROOT / "gateware" / "soc" / "coremark_port"

# Enough iterations that the run is dominated by work rather than by startup,
# and few enough that it finishes in seconds on a 60 MHz core.
ITERATIONS = 300

CC = "riscv64-linux-gnu-gcc"
OBJCOPY = "riscv64-linux-gnu-objcopy"
OBJDUMP = "riscv64-linux-gnu-objdump"

# rv32im: multiply is worth having (the ECP5 has DSP blocks, so it is cheap)
# and compressed instructions are omitted to keep the disassembly readable
# during bring-up. ilp32 is the soft-float ABI; there is no FPU.
#
# _zicsr is named explicitly because recent binutils split the CSR
# instructions out of the base ISA; without it `csrw` is an unrecognised
# opcode. The hardware has always had them -- this is a toolchain spelling
# change, not a core feature.
# rv32imac, matching what the CPU generates (--with-rvm --with-rvc --with-rva) and what
# moondancer targets (riscv32imac-unknown-none-elf).
#
# This was rv32im_zicsr, which is a safe subset -- a CPU always runs less than it
# implements -- but it left the C and A extensions completely untested while both are
# enabled in hardware. Moondancer would have been the first code to use them, which is a
# poor place to discover a decode fault. Compressed instructions also shrink the image by
# roughly a quarter, and block RAM is 64 KiB.
ARCH = ["-march=rv32imac_zicsr", "-mabi=ilp32"]

# Must match the SoC's reset vector. The CPU resets to 0x00000000, so the entry point has
# to be the first thing in the image.
RAM_BASE = 0x00000000
RAM_SIZE = 64 * 1024
# Bit 31 set: below 0x80000000 the data cache absorbs the stores.

CONSOLE_BASE = 0xf0000000

# The memory-mapped configuration SPI flash and the SPI controller's registers.
# Must match gateware/soc/vexii_hello_soc.py -- FLASH_BASE, FLASH_CSR_BASE
# and FLASH_TEST_OFFSET there.
FLASH_BASE = 0x10000000
FLASH_CSR_BASE = 0xf0000100
FLASH_PROBE_BASE = 0xf0000200
FLASH_ILA_BASE = 0xf0000300
ILA_DEPTH = 1024
# The offset the firmware reads twice, and benchmarks. Read only -- the write
# tests use FLASH_SCRATCH below.
#
# Choosing this took three tries, and the two rejected values are worth
# recording because the mistake is easy to repeat. Both were CORRECT data, and
# both were useless as test values:
#
#   0x300000  erased flash, reads ffffffff -- which is also what a floating bus
#             returns when nothing drives it. The first run here was briefly
#             misread as a flash fault on exactly that basis.
#   0x020000  reads 00000000 on this board, because the bitstream actually
#             stored in flash is shorter than the one built locally. That is
#             also what a bus with nothing driving it at all returns.
#
# A test whose passing value is identical to a likely failure value cannot
# distinguish success from failure.
#
# 0x40 is inside the bitstream header and reads 2a558800 -- varied in all four
# bytes, stable, and not confusable with either degenerate pattern. Confirmed
# independently with `apollo flash-read --offset 0 --length 256`, indexed to
# 0x40, because flash-read requires a page-aligned offset and silently writes
# an empty file for one that is not.
FLASH_TEST_OFFSET = 0x00000040

# The 4 KiB sector the write and erase tests own outright.
#
# 0x00200000 is 2 MiB in. Two things it must clear, both of which cost a
# reflash rather than a board if got wrong:
#
#   offset 0        holds the FPGA bitstream. Erasing it means the board no
#                   longer configures from flash. It still accepts a bitstream
#                   over JTAG, so it is recoverable, but there is no reason to
#                   go near it. moondancer's linker script puts firmware at
#                   0xb0000 (720 KiB) for the same reason.
#   4 MiB and above aliases back to offset 0 -- SFDP declares 4 MiB and the
#                   address wraps -- so a write at 4 MiB+ lands ON the
#                   bitstream while appearing to be nowhere near it. Verified
#                   in docs/chips/w25q32-config-flash.md.
#
# 2 MiB is comfortably above the largest bitstream this device takes (~320 KiB)
# and comfortably below the 4 MiB wrap.
FLASH_SCRATCH = 0x00200000

LINKER_SCRIPT = """
/* Generated by scripts/riscv_firmware.py -- do not edit. */
OUTPUT_ARCH(riscv)
ENTRY(_start)

MEMORY {{
    ram (rwx) : ORIGIN = {ram_base:#x}, LENGTH = {ram_size:#x}
}}

SECTIONS {{
    /* .init first: the reset vector is the base of RAM, so _start must be the
       very first instruction in the image. */
    .text : {{
        *(.init)
        *(.text .text.*)
        *(.rodata .rodata.*)
    }} > ram

    .data : {{
        *(.data .data.*)
        *(.sdata .sdata.*)
    }} > ram

    .bss (NOLOAD) : {{
        __bss_start = .;
        *(.bss .bss.*)
        *(.sbss .sbss.*)
        *(COMMON)
        __bss_end = .;
    }} > ram

    /* Stack at the top of RAM, growing down towards .bss. Nothing checks for
       collision -- with 64 KiB and a hello-world there is no realistic risk,
       but a benchmark with deep recursion would need a guard. */
    __stack_top = ORIGIN(ram) + LENGTH(ram);
}}
"""

START_S = """
/* Generated by scripts/riscv_firmware.py -- do not edit. */
    .section .init
    .global _start
_start:
    /* No interrupts, no traps: nothing here enables them, and a trap during
       bring-up would jump somewhere undefined rather than report anything. */
    csrw    mie, zero
    csrw    mip, zero

    la      sp, __stack_top

    /* Zero .bss. C promises it, and a benchmark reading uninitialised counters
       would produce numbers that look plausible and mean nothing. */
    la      t0, __bss_start
    la      t1, __bss_end
1:  bgeu    t0, t1, 2f
    sw      zero, 0(t0)
    addi    t0, t0, 4
    j       1b
2:
    call    main

    /* main returning is not expected. Spin rather than fall into whatever
       follows in memory. */
3:  j       3b
"""

CONSOLE_H = """
/* Generated by scripts/riscv_firmware.py -- do not edit. */
#ifndef CONSOLE_H
#define CONSOLE_H

#define CONSOLE_BASE  {console_base:#x}u

/* An NS16550A -- Uart16550 in gateware/soc/uart16550.py, and byte-identical
   to the one QEMU's -M virt presents. Write THR at +0, poll LSR at +5 for room.

   LSR is at +5 and not next to THR on purpose: it is the register a poll loop
   reads, and it must not share a 32-bit word with anything whose read has a
   side effect. A FIFO-popping read one byte from the polled one is what makes
   firmware that polls go silent while firmware that never reads prints fine. */
#define CONSOLE_THR   (*(volatile unsigned char *)(CONSOLE_BASE + 0))
#define CONSOLE_LSR   (*(volatile unsigned char *)(CONSOLE_BASE + 5))
#define CONSOLE_THRE  0x20u   /* transmit holding register empty */

static inline void putch(char c) {{
    while (!(CONSOLE_LSR & CONSOLE_THRE)) {{ }}
    CONSOLE_THR = (unsigned char)c;
}}

static inline void print(const char *s) {{
    while (*s) putch(*s++);
}}

static inline void print_hex(unsigned int v) {{
    for (int shift = 28; shift >= 0; shift -= 4) {{
        unsigned int nibble = (v >> shift) & 0xf;
        putch(nibble < 10 ? ('0' + nibble) : ('a' + nibble - 10));
    }}
}}

#endif
"""

FLASH_H = """
/* Generated by scripts/riscv_firmware.py -- do not edit. */
#ifndef FLASH_H
#define FLASH_H

/* The configuration SPI flash, two ways.

   FLASH_BASE is the memory map: ordinary loads, cached, backed by
   ModalSPIFlashMemoryMap issuing read opcodes. This is the thing under test.

   FLASH_CSR_BASE is luna_soc's SPIController -- a FIFO pair and a shift
   register, able to issue any command at all. It is here only because the
   JEDEC ID (0x9f) is a register read rather than an address read, so no memory
   map can express it.

   NOTHING HERE WRITES OR ERASES. The FPGA bitstream is at flash offset 0 and a
   page program or sector erase would destroy it. The controller path is
   perfectly capable of doing that; the restraint is in this file, not in the
   gateware. */

#define FLASH_BASE      {flash_base:#x}u
#define FLASH_CSR_BASE  {flash_csr_base:#x}u

/* An offset chosen for the read-repeatability check: 3 MiB in, far past a
   ~308 KiB bitstream. Reads are harmless anywhere; this is about reading
   something that is not being changed by anything else. */
#define FLASH_TEST_OFFSET {flash_test_offset:#x}u

/* The only region anything here is permitted to modify: a 4 KiB sector 2 MiB
   into the part. Clear of the bitstream at offset 0, and well below the 4 MiB
   point where addresses alias back onto it. Every erase and program address
   below is derived from this constant and nothing else. */
#define FLASH_SCRATCH {flash_scratch:#x}u

/* Where flash_cache_flush() reads from: 1 MiB in, a whole 4 KiB clear of both
   FLASH_SCRATCH and the bitstream, so displacing the cache never pulls in the
   lines under test. Read only. */
#define FLASH_FLUSH_REGION 0x00100000u

/* SPIController register map. Byte offsets from luna_soc's csr.Builder layout
   (addr_width=5, data_width=8), verified against the elaborated memory map
   rather than assumed:

     0x0  phy     length[5:0] | width[9:6] | mask[17:10]   (32-bit access)
     0x4  cs      select[0]
     0x5  status  rx_ready[0], tx_ready[1]
     0x8  data.rx 32 bits, read side  -- reading POPS the RX FIFO
     0xc  data.tx 32 bits, write side

   `data.rx` having a side effect on read is why FLASH_CSR_BASE must be in an
   uncached PMA region. In a cached one the second read returns the first
   read's value out of a cache line, indefinitely. */
#define FLASH_PHY    (*(volatile unsigned int  *)(FLASH_CSR_BASE + 0x0))
#define FLASH_CS     (*(volatile unsigned char *)(FLASH_CSR_BASE + 0x4))
#define FLASH_STATUS (*(volatile unsigned char *)(FLASH_CSR_BASE + 0x5))
#define FLASH_RX     (*(volatile unsigned int  *)(FLASH_CSR_BASE + 0x8))
#define FLASH_TX     (*(volatile unsigned int  *)(FLASH_CSR_BASE + 0xc))

/* Chip select hold, a LATCHING register at offset 0x20 -- see
   HoldableSPIController in gateware/soc/vexii_flash.py.

   The `cs` register at 0x4 is a write-pulse and cannot hold chip select across
   transfers; upstream's CS collapses to the TX FIFO's occupancy, so it drops
   whenever the CPU is slower than the PHY, which is always. The ILA caught the
   consequence directly: four separate 8-bit windows for a JEDEC read with CS
   deasserted between them, so the flash reset its command state each time and
   drove nothing for the 24 response clocks.

   Assert this before a multi-transfer command and release it after. */
#define FLASH_HOLD   (*(volatile unsigned char *)(FLASH_CSR_BASE + 0x20))

#define FLASH_STATUS_RX_READY 0x1u
#define FLASH_STATUS_TX_READY 0x2u

/* mcycle, the RISC-V cycle counter. One instruction to read, so it can bracket
   even a short operation without distorting it -- which is the whole reason
   these measurements belong on the CPU. A JTAG register read from the host is
   ~35 ms against ~1 us for a flash read, so every small-transfer figure ever
   quoted for this part is arithmetic rather than measurement. */
static inline unsigned int flash_cycles(void) {{
    unsigned int c;
    __asm__ volatile ("csrr %0, mcycle" : "=r"(c));
    return c;
}}

/* Pack the phy register: transfer length in bits, bus width in lanes, and the
   DQ output-enable mask. mask=1 drives DQ0 only (single-lane send); mask=0
   releases every DQ so the flash can drive them (receive). */
static inline void flash_phy_set(unsigned int len, unsigned int width,
                                 unsigned int mask) {{
    FLASH_PHY = (len & 0x3f) | ((width & 0xf) << 6) | ((mask & 0xff) << 10);
}}

/* Queue one transfer. Does NOT wait for its result.

   SPI is full duplex: every transfer clocks a word out and a word in at the
   same time, so a send and a receive are one operation with different masks,
   and every queued transfer eventually produces an RX word that must be
   consumed or the RX FIFO fills and the controller stalls.

   The wait is on tx_ready -- room in the 16-deep TX FIFO -- not on time. If the
   PHY never drains the FIFO this spins forever, which is the right behaviour
   for bring-up: a silent timeout would turn "the flash is not responding" into
   "the flash returned zero", and those have different causes. */
static inline void flash_push(unsigned int data) {{
    while (!(FLASH_STATUS & FLASH_STATUS_TX_READY)) {{ }}
    FLASH_TX = data;
}}

/* Take one completed transfer's data out of the RX FIFO. */
static inline unsigned int flash_pop(void) {{
    while (!(FLASH_STATUS & FLASH_STATUS_RX_READY)) {{ }}
    return FLASH_RX;
}}

/* Read the JEDEC ID: command 0x9f, then three bytes back. Expect ef4016.

   THE WHOLE SEQUENCE IS QUEUED BEFORE ANY OF IT IS READ BACK, and that is a
   correctness requirement rather than an optimisation.

   luna_soc's SPIController derives chip select from the TX FIFO, not from the
   `cs` register:

       with m.State("RISE"):  m.d.comb += cs.eq(tx_fifo.r_rdy)
       with m.State("FALL"):  m.d.comb += cs.eq(self._cs.f.select.w_data
                                                | tx_fifo.r_rdy)

   `self._cs.f.select` is a csr.action.W field, so `w_data` is live only during
   the cycle of the write strobe -- it is a pulse, not a latch. The `cs` CSR
   therefore cannot hold chip select down across transfers, and CS is
   effectively `tx_fifo.r_rdy` alone.

   So a loop that pushes one transfer and immediately waits for its result
   empties the TX FIFO between every byte, dropping CS and restarting the flash
   each time. The command is accepted and then discarded three times over, and
   the ID reads back as zeros -- which is exactly what the first hardware run
   printed. Queueing all four transfers keeps r_rdy high throughout, so CS stays
   asserted for the whole command as the flash requires.

   THE DATA IS PASSED RIGHT-ALIGNED, NOT PRE-SHIFTED, and getting this wrong is
   what made the ID read zeros for most of this investigation.

   The PHY left-justifies for you:

       sr_out.eq(source.data << (len(source.data) - source.len))

   so an 8-bit transfer of 0x9f becomes 0x9f000000 in the shift register and
   goes out MSB first. Passing 0x9f << 24 means the PHY shifts it a FURTHER 24
   bits, the register becomes zero, and the flash is clocked 8 zero bits instead
   of an opcode. It answers nothing, because it was never asked anything.

   The ILA caught this directly: dq_o0 was high in 0 of the 465 samples in the
   controller's chip-select window, while the memory-mapped control asserted it
   8 times in the same capture -- so the PHY does left-justify, whatever a
   comment might assert.

   32-bit transfers are unaffected -- (32 - 32) is a shift of zero -- which is
   why the address-bearing commands below already pack the opcode into bits
   31..24 and are correct as written.

   Four transfers is well inside the 16-deep FIFOs. */
static inline unsigned int flash_jedec_id(void) {{
    unsigned int id = 0;

    /* Hold chip select down for the WHOLE command. Without this the FIFO
       empties between transfers and CS drops, which is the fault the ILA
       found. Queueing everything up front does not help -- the CPU cannot
       refill faster than the PHY drains. */
    FLASH_HOLD = 1;

    flash_phy_set(8, 1, 0x1);        /* 8 bits out on DQ0 */
    flash_push(0x9fu);

    flash_phy_set(8, 1, 0x0);        /* 8 bits in, DQ released */
    flash_push(0);
    flash_push(0);
    flash_push(0);

    /* Discard the word clocked in while the command was going out -- the flash
       was not driving yet. */
    (void)flash_pop();

    for (int i = 0; i < 3; i++) {{
        id = (id << 8) | (flash_pop() & 0xff);
    }}

    FLASH_HOLD = 0;
    return id;
}}

/* The memory map, as plain loads. This is what the issue is about: the CPU
   addressing flash the same way it addresses RAM. */
static inline unsigned int flash_read32(unsigned int offset) {{
    return *(volatile unsigned int *)(FLASH_BASE + offset);
}}

/* ------------------------------------------------------------------------
   Write and erase.

   These modify the chip. Everything below stays inside FLASH_SCRATCH, a 4 KiB
   sector 2 MiB into the part, chosen to clear both the bitstream at offset 0
   and the 4 MiB address wrap that would alias back onto it. Nothing here
   computes an address from anything but FLASH_SCRATCH.

   The memory map cannot express any of this -- it issues one read opcode -- so
   all of it goes through the controller's arbitrary-command path, with the same
   CS discipline as the JEDEC read: queue the whole command before draining any
   of it, because chip select follows the TX FIFO's occupancy.
   ------------------------------------------------------------------------ */

#define FLASH_CMD_WRITE_ENABLE  0x06u
#define FLASH_CMD_PAGE_PROGRAM  0x02u
#define FLASH_CMD_SECTOR_ERASE  0x20u   /* 4 KiB  */
#define FLASH_CMD_READ_STATUS1  0x05u

#define FLASH_SR1_BUSY 0x01u   /* WIP: an erase or program is in progress */
#define FLASH_SR1_WEL  0x02u   /* Write Enable Latch */

#define FLASH_SECTOR_SIZE 4096u
#define FLASH_PAGE_SIZE    256u

/* Discard `count` words from the RX FIFO. Every queued transfer produces one,
   whether or not the data mattered, and leaving them there fills the FIFO and
   stalls the controller on the next command. */
static inline void flash_drain(unsigned int count) {{
    for (unsigned int i = 0; i < count; i++) {{
        (void)flash_pop();
    }}
}}

/* A single-byte command with no address and no data: 0x06, mostly.

   Write Enable must precede every program and every erase, and the flash clears
   the latch itself when the operation completes -- so it is one enable per
   operation, never one at the start of a batch. */
static inline void flash_command(unsigned int opcode) {{
    flash_phy_set(8, 1, 0x1);
    flash_push(opcode);
    flash_drain(1);
}}

/* Read status register 1. Bit 0 is BUSY, bit 1 is the write-enable latch. */
static inline unsigned int flash_status1(void) {{
    flash_phy_set(8, 1, 0x1);
    flash_push(FLASH_CMD_READ_STATUS1);
    flash_phy_set(8, 1, 0x0);
    flash_push(0);
    flash_drain(1);                 /* the byte clocked in during the command */
    return flash_pop() & 0xff;
}}

/* Spin until the flash reports it is no longer busy, returning the cycles it
   took.

   Polling BUSY, not waiting a fixed time. The datasheet quotes 0.7 ms typical
   and 3 ms maximum for a page program, and 45 ms typical against 400 ms maximum
   for a sector erase -- a spread of nearly ten to one. A delay long enough to be
   safe would be mostly idle, and one tuned to the typical case would corrupt
   data on a slow part or a worn sector. The chip knows when it is done and says
   so; this asks it.

   Returns elapsed cycles so the caller can compare against those datasheet
   figures, which is the point of measuring from the CPU rather than the host.

   No timeout, deliberately. If BUSY never clears the flash is wedged, and that
   is worth hanging visibly for -- a timeout here would report a program as
   complete when it was not, and the readback check would then blame the wrong
   thing. */
/* Wait for BUSY to clear, but NOT forever.

   The bound is what makes this diagnosable. An unbounded `while (BUSY) {{}}` cannot
   distinguish "the erase is still running" from "the status read is broken and BUSY
   will never clear", and the second is exactly what happened while the command path
   was faulty -- the CPU hung with no output and looked dead.

   FLASH_WAIT_LIMIT COUNTS TRANSACTIONS, NOT CYCLES, and that distinction is the whole
   sizing. Each spin is a full 16-bit status read: 16 SCK at 30 MHz (sync 60 / 2*(1+0))
   plus four uncached CSR accesses, each a Wishbone round trip -- order 2 us, not one
   cycle. An earlier value of 0x20000000 was derived as though spins were cycles, which
   made the bound about eighteen minutes rather than the few seconds intended. A timeout
   that long is not a bound; nobody waits for it, so the hang it exists to diagnose looks
   exactly like the hang it was meant to distinguish.

   The slowest operation this firmware issues is a 4 KiB SECTOR erase -- there is no
   block or chip erase opcode here -- so tSE, 400 ms maximum, is what binds. Ten times
   that is 4 s, and at ~2 us a spin that is ~2M transactions.

   Too small is worse than too large: it reports a timeout on an erase that was going to
   finish. 10x the datasheet maximum is the headroom that makes a expiry mean a broken
   read rather than a slow flash. On expiry this returns with the top bit set so the
   caller can report a timeout rather than silently believing the operation finished. */
#define FLASH_WAIT_LIMIT 0x200000u
#define FLASH_WAIT_TIMEOUT 0x80000000u

static inline unsigned int flash_wait_ready(void) {{
    unsigned int start = flash_cycles();
    unsigned int spins = 0;
    while (flash_status1() & FLASH_SR1_BUSY) {{
        if (++spins > FLASH_WAIT_LIMIT) {{
            return FLASH_WAIT_TIMEOUT | (flash_cycles() - start);
        }}
    }}
    return flash_cycles() - start;
}}

/* Erase one 4 KiB sector. `offset` is anywhere inside it. Returns the cycles
   the erase took, measured from the end of the command to BUSY clearing. */
static inline unsigned int flash_sector_erase(unsigned int offset) {{
    flash_command(FLASH_CMD_WRITE_ENABLE);

    /* Opcode and 24-bit address in one CS assertion. The controller sends the
       top `len` bits of the word, so a 32-bit transfer of
       (opcode << 24 | address) is exactly the four bytes the flash wants, in
       order, and keeps the whole command inside a single FIFO burst. */
    flash_phy_set(32, 1, 0x1);
    flash_push((FLASH_CMD_SECTOR_ERASE << 24) | (offset & 0x00ffffffu));
    flash_drain(1);

    return flash_wait_ready();
}}

/* Program up to one 256-byte page, from `words` 32-bit words.

   A page program cannot cross a page boundary -- the address wraps within the
   page rather than continuing, so bytes written past the end land back at its
   start and silently overwrite what was just written. The caller keeps to
   pages; this does not check, because the only caller is below and it is
   aligned by construction.

   Returns the cycles the program took. */
static inline unsigned int flash_page_program(unsigned int offset,
                                              const unsigned int *data,
                                              unsigned int words) {{
    flash_command(FLASH_CMD_WRITE_ENABLE);

    flash_phy_set(32, 1, 0x1);
    flash_push((FLASH_CMD_PAGE_PROGRAM << 24) | (offset & 0x00ffffffu));

    /* The RX side is drained as we go, and the ORDER matters for chip select.

       CS follows TX FIFO occupancy, so it drops the moment the FIFO runs dry --
       which mid-page would abort the program and leave part of a page written.
       Both FIFOs are 16 deep, so neither a queue-everything-then-drain nor a
       drain-before-push arrangement works: the first overflows RX, the second
       lets TX empty while the CPU is blocked waiting on rx_ready.

       Pushing before popping on every iteration keeps at least one entry in the
       TX FIFO whenever the CPU is blocked, so CS is held by the pending
       transfer while its predecessor's result is collected. The CPU at 80 MHz
       also queues a word far faster than the PHY shifts one out (a 32-bit
       transfer at 40 MHz SCK is 800 ns), so in practice TX stays well ahead. */
    for (unsigned int i = 0; i < words; i++) {{
        /* Byte-reversed on the way out to match the byte-reversal the memory
           map applies on the way in (SPIFlashMemoryMap.reverse_bytes). Without
           this a word written as 0x11223344 reads back as 0x44332211 and the
           verify fails on data that is actually intact -- a mismatch that looks
           like a flash fault and is a byte-order bug. */
        unsigned int w = data[i];
        flash_push(((w & 0x000000ffu) << 24) |
                   ((w & 0x0000ff00u) <<  8) |
                   ((w & 0x00ff0000u) >>  8) |
                   ((w & 0xff000000u) >> 24));
        flash_drain(1);
    }}
    flash_drain(1);                 /* the address transfer's own RX word */

    return flash_wait_ready();
}}

/* ------------------------------------------------------------------------
   The pin probe: what actually happened on the SPI pins.

   Counters in the gateware, sampled at the pads rather than anywhere in the
   fabric, so a non-zero reading means the signal genuinely left the design.
   See FlashPinProbe in gateware/soc/vexii_flash.py.

   All sticky or cumulative, never live. An SCK edge is one cycle wide and the
   CPU reads these thousands of cycles later, so a live sample would read zero
   essentially always -- the same trap that nearly had the CPU declared dead
   when the sideband reported raw Wishbone strobes.
   ------------------------------------------------------------------------ */

#define FLASH_PROBE_BASE {flash_probe_base:#x}u

#define PROBE_CS_FELL   (*(volatile unsigned char *)(FLASH_PROBE_BASE + 0x0))
#define PROBE_SCK_EDGES (*(volatile unsigned short *)(FLASH_PROBE_BASE + 0x2))
#define PROBE_DQ_DRIVEN (*(volatile unsigned char *)(FLASH_PROBE_BASE + 0x4))
#define PROBE_GRANTS    (*(volatile unsigned short *)(FLASH_PROBE_BASE + 0x6))
/* SCK edges that happened while the DQ output driver was still enabled. During
   the receive phase of a read this must be ZERO: the controller has to release
   DQ so the flash can drive it, and if the FPGA is still driving, the two fight
   and the sampled value is the FPGA's own output rather than the flash's
   answer. A non-zero count here explains a read of all zeros exactly. */
#define PROBE_OE_EDGES  (*(volatile unsigned short *)(FLASH_PROBE_BASE + 0x8))
#define PROBE_CLEAR     (*(volatile unsigned char *)(FLASH_PROBE_BASE + 0xa))

/* Zero every counter, so what follows measures ONE operation rather than a
   total since reset. A total cannot separate "this transaction did nothing"
   from "this transaction did nothing but an earlier one did". */
static inline void probe_clear(void) {{
    PROBE_CLEAR = 1;
}}

static inline void probe_report(const char *label) {{
    print(label);
    print(" cs ");
    print_hex(PROBE_CS_FELL);
    print("  sck ");
    print_hex(PROBE_SCK_EDGES);
    print("  dq_oe ");
    print_hex(PROBE_DQ_DRIVEN);
    print("  grants ");
    print_hex(PROBE_GRANTS);
    print("  oe_clk ");
    print_hex(PROBE_OE_EDGES);
    print("\\r\\n");
}}

/* ------------------------------------------------------------------------
   The logic analyser.

   Captures eight signals per sync cycle into gateware memory, dumped afterwards
   over this console. The pin probe established that the controller reaches the
   pins correctly; what it cannot show is WHEN the input bit is latched relative
   to the clock, which is what is left.

   Trace format, one byte per sample, bit 0 first:

     0 sck          the SPI clock as handed to USRMCLK
     1 dq_i1        the flash's output pin, as the PHY sees it
     2 cs           chip select (high = selected)
     3 sr_in_shift  THE CAPTURE STROBE -- when an input bit is shifted in
     4 sample       the clock generator's sample strobe
     5 update       the clock generator's update strobe
     6 in_xfer      PHY FSM is in XFER
     7 dq_o0        what the PHY DRIVES on DQ0 -- the outgoing command bit

   The relationship between bit 3 and bit 0 is the measurement. Bits 6 and 7
   show the transitions between the four transfers of a JEDEC read, which is
   where a strobe that fires only on the first transfer would show itself.
   ------------------------------------------------------------------------ */

#define FLASH_ILA_BASE {flash_ila_base:#x}u
#define ILA_DEPTH      {ila_depth}u

#define ILA_STATUS (*(volatile unsigned char  *)(FLASH_ILA_BASE + 0x0))
#define ILA_ARM    (*(volatile unsigned char  *)(FLASH_ILA_BASE + 0x1))
#define ILA_INDEX  (*(volatile unsigned short *)(FLASH_ILA_BASE + 0x2))
#define ILA_SAMPLE (*(volatile unsigned char  *)(FLASH_ILA_BASE + 0x4))

#define ILA_STATUS_COMPLETE 0x1u
#define ILA_STATUS_SAMPLING 0x2u

/* Arm the capture. The very next thing the caller does is the transaction
   under test, so the window starts at the transaction rather than at some
   symptom of it. */
static inline void ila_arm(void) {{
    ILA_ARM = 1;
}}

/* Dump the trace as hex, one line per 32 samples.

   Printed raw rather than decoded on-target. A decoder here would be one more
   thing that can be wrong between the signal and the reader, and the whole
   point of this instrument is to see what the hardware did rather than what
   something believes about it. The host script decodes.

   Waits for `complete` first. The capture is 1024 sync cycles, a few
   microseconds, and the CPU takes far longer than that to reach this line --
   but polling the flag is what makes that a fact rather than an assumption. If
   the ILA never completes this spins, which is the correct failure: a partial
   trace read as though it were whole would be worse. */
static inline void ila_dump(const char *label) {{
    while (!(ILA_STATUS & ILA_STATUS_COMPLETE)) {{ }}

    print("ila ");
    print(label);
    print("\\r\\n");
    for (unsigned int i = 0; i < ILA_DEPTH; i++) {{
        if ((i & 31) == 0) {{
            print("  ");
            print_hex(i);
            print(" ");
        }}
        ILA_INDEX = i;
        /* One dead read: ILA_INDEX feeds a synchronous memory read port, so
           the sample for index i is only valid the cycle after the address
           lands. Reading once and discarding costs nothing and removes an
           off-by-one that would shift the entire trace by one sample. */
        (void)ILA_SAMPLE;
        unsigned char s = ILA_SAMPLE;
        putch("0123456789abcdef"[(s >> 4) & 0xf]);
        putch("0123456789abcdef"[s & 0xf]);
        if ((i & 31) == 31) print("\\r\\n");
    }}
}}

/* Displace every D-cache line, so a following memory-mapped read reaches the
   flash rather than returning a line cached before the flash changed.

   VexiiRiscv is built here with --lsu-l1-sets 64 --lsu-l1-ways 1 and a 64-byte
   line, so the data cache is 64 * 1 * 64 = 4096 bytes, direct-mapped. Reading
   4 KiB of DISTINCT addresses at line stride therefore touches every set once
   and evicts whatever was in it.

   This is a workaround, and the proper fix is named so nobody has to rediscover
   it: VexiiRiscv can be generated with Zicbom (--with-rvZcbm) for `cbo.inval`,
   which invalidates a line directly. This SoC is not built with it, and turning
   on an untested CPU extension to support a test is the wrong order of work.

   The reads come from a region far from FLASH_SCRATCH so the displacement
   itself does not populate the lines being tested. */
static inline void flash_cache_flush(void) {{
    for (unsigned int i = 0; i < 4096; i += 64) {{
        (void)*(volatile unsigned int *)(FLASH_BASE + FLASH_FLUSH_REGION + i);
    }}
}}

/* Read one word through the CONTROLLER rather than the memory map.

   This exists because of cache coherency, and the problem it solves is easy to
   miss. The memory map is in a main=1 PMA region, so the D-cache backs it --
   which is the entire point of mapping it that way, and is exactly wrong for
   verifying a write. The cache has no idea the flash changed underneath it: a
   line read before an erase is still in the cache after it, and
   `flash_read32` returns the OLD contents. A verify built on that reports
   success when nothing was written, and reports the previous pass's data as
   though it were this pass's.

   VexiiRiscv can be built with Zicbom (`--with-rvZcbm`) for `cbo.inval`, which
   would be the proper fix; this SoC is not, and enabling an untested CPU
   feature to support a test is the wrong order. So the verify reads through the
   uncached path instead, which is also the more useful check: the write goes
   through the controller and the readback through a different route, so the two
   paths cross-check rather than confirming each other.

   Byte order matches the memory map's: SPIFlashMemoryMap reverses bytes on the
   way in, so this does too and the two agree word for word. */
static inline unsigned int flash_read32_uncached(unsigned int offset) {{
    flash_phy_set(32, 1, 0x1);
    flash_push((0x03u << 24) | (offset & 0x00ffffffu));   /* Read Data */
    flash_phy_set(32, 1, 0x0);
    flash_push(0);
    flash_drain(1);                 /* RX word from the command phase */
    unsigned int w = flash_pop();
    return ((w & 0x000000ffu) << 24) |
           ((w & 0x0000ff00u) <<  8) |
           ((w & 0x00ff0000u) >>  8) |
           ((w & 0xff000000u) >> 24);
}}

#endif
"""

HELLO_C = """
/* Generated by scripts/riscv_firmware.py -- do not edit. */
#include "console.h"
#include "flash.h"

/* Words read per throughput measurement. 256 words is 1 KiB: long enough that
   the command/address/dummy preamble is amortised and the burst-continuation
   path dominates, short enough to fit inside a 32-bit cycle counter at 80 MHz
   with several orders of magnitude to spare. */
#define BENCH_WORDS 256

/* Whether this image erases and programs, set by scripts/riscv_firmware.py.

   Defaults to OFF. The read-only build is what validates the controller path --
   if the JEDEC ID does not come back as ef4016 through that path, then the same
   path has not been shown to issue a READ correctly, and sending it erase and
   program opcodes would be the wrong order of operations. Writes are enabled
   only after that check passes. */
#ifndef WRITE_TESTS
#define WRITE_TESTS 0
#endif

/* How many erase/program/verify passes to run. TWO, not a soak.

   One pass establishes that erase, program and readback all work and yields the
   timing figures; repeating it with the SAME pattern tests nothing further, it
   only spends the sector's rated 100,000-cycle endurance.

   The second pass uses a DIFFERENT pattern, and that is the only reason it is
   worth running at all: with one pattern, a readback that matches cannot
   distinguish "the reprogram worked" from "the data was already there and the
   program did nothing". Changing the pattern makes those two outcomes look
   different. */
#ifndef WRITE_PASSES
#define WRITE_PASSES 2
#endif

/* Report every value the flash tests produce. Raw numbers, never a bare "OK":
   a check that prints only its verdict hides what it saw, and the difference
   between 0xef4016, 0x000000 and 0xffffff is the difference between a working
   chip, a chip that is not being clocked, and a chip that is not being
   selected.

   THIS RUNS ONCE PER LOOP ITERATION RATHER THAN ONCE AT STARTUP, and that is
   not cosmetic. The firmware begins printing the moment the FPGA is
   configured, which is well before the host has enumerated the USB device and
   opened the tty -- so a reader always misses part of a prologue printed only
   at reset. Three capture attempts caught `ame`, `00  same` and `nsole.` as
   their first line, losing exactly the values under test, and no amount of
   opening the port faster fixes it. Reprinting means a reader attaching at any
   moment sees a complete report within one period. */
/* Returns 1 if the flash answered and the rest of the pass is worth running,
   0 if the command path is dead.

   FAIL FAST. If the controller cannot issue a command and get an answer, then
   nothing after this point can be interpreted: a read cannot be trusted, and a
   write cannot be verified at all -- you would be programming a sector whose
   state you cannot observe. Printing the failure and stopping the pass is the
   only useful thing left to do. */
/* The last JEDEC value read, so the held banner can report what was actually
   seen rather than a generic failure. */
static unsigned int last_jedec = 0;

static int flash_report(unsigned int pass) {
    print("--- flash pass ");
    print_hex(pass);
    print("\\r\\n");

    /* 1. The controller's arbitrary-command path, exercised with 0x9f.

          What this proves is that the controller can issue a command and get a
          sane answer back. It is NOT an assertion that a particular chip is
          fitted, and must not become one: this board reads ef4016 -- Winbond
          (ef), SPI NOR (40), capacity 2^0x16 = 4 MiB -- but ef4017 is the 8 MiB
          W25Q64, c22016 a Macronix equivalent and 9d6016 an ISSI one, all of
          them healthy parts that a hardcoded comparison would call a failure.
          So the raw value is printed and the judgement left to structure.

          The one thing worth deriving is the capacity byte, because the SoC
          depends on it: the memory map is sized at 4 MiB and everything above
          that aliases back to offset 0. A part reporting a different capacity
          would make the address map wrong, which is a real finding rather than
          a chip-identity quibble.

          This cannot come through the memory map: 0x9f is a register read, and
          the memory map issues one opcode, a read of a 24-bit address. */
    /* --- THE ILA CAPTURES. Only on pass 0: the trace is 1024 samples printed
       as hex and there is no reason to send it more than once, and a reader
       attaching late still gets the pin-probe counters below every pass.

       Two captures with an IDENTICAL configuration -- a memory-mapped read,
       which is known to work, and a controller read, which is not. Diffing them
       is the entire reason this instrument exists: both sample the same
       dq_i[1] through the same PHY, so if the capture strobe behaves
       differently between them, that difference IS the fault.

       The memory-mapped capture is the positive control and is taken first. If
       it shows no clock and no strobe on a path proven correct against
       `apollo flash-read`, the instrument is wrong and nothing it says about
       the controller means anything. */
    if (pass == 0) {
        ila_arm();
        (void)flash_read32(FLASH_TEST_OFFSET + 0x100);
        ila_dump("mmap (positive control)");

        ila_arm();
        (void)flash_jedec_id();
        ila_dump("ctrl (jedec)");
    }

    /* --- THE POSITIVE CONTROL, and it runs first on purpose.

       A memory-mapped read is the one flash operation known to work here: its
       results match `apollo flash-read` byte for byte at two offsets, through
       entirely separate hardware. So the probe MUST show chip select asserting
       and SCK toggling during it.

       If it does not, the instrument is broken and nothing measured after it
       means anything -- the same discipline as the fabric test's 0xdeadbeef
       control, which has caught bad measurements in this project repeatedly.
       Read this line before reading the controller line below it. */
    probe_clear();
    (void)flash_read32(FLASH_TEST_OFFSET + 0x80);   /* uncached by construction:
                                                       a fresh line, not one the
                                                       benchmark already pulled in */
    probe_report("probe mmap  ");

    /* --- THE MEASUREMENT. Same counters, same pins, around a controller
       transaction instead. The difference between this line and the one above
       is the entire experiment. */
    probe_clear();
    (void)flash_jedec_id();
    probe_report("probe ctrl  ");

    /* The controller's status register before anything is queued. tx_ready
       (bit 1) must be set or no transfer can ever be pushed; rx_ready (bit 0)
       should be clear on an idle controller. Printing it separates "the
       command was never sent" from "the command was sent and the flash did not
       answer", which the ID value alone cannot distinguish -- both give
       000000. */
    print("spi status   ");
    print_hex(FLASH_STATUS);
    print("\\r\\n");

    unsigned int id = flash_jedec_id();
    last_jedec = id;
    print("jedec        ");
    print_hex(id);
    /* A failed ID is reported and the pass CONTINUES.

       The reads and the benchmark below go through the memory map, which is
       independently verified against `apollo flash-read` and works whether or
       not the controller can read a response back. Skipping them on a JEDEC
       failure threw away good measurements from a working path because a
       different path was broken. The return value tells the caller what
       happened; it does not mean "nothing else is valid". */
    int answered = 1;
    if (id == 0x000000u) {
        print("  NO RESPONSE (all zeros)\\r\\n");
        answered = 0;
    } else if ((id & 0xffffffu) == 0xffffffu) {
        print("  NO RESPONSE (floating high)\\r\\n");
        answered = 0;
    } else {
        print("  capacity ");
        print_hex(1u << (id & 0xffu));
        print(" bytes\\r\\n");
    }

    /* 2. The memory map itself: an ordinary load from FLASH_BASE. Offset 0 is
          the start of the FPGA bitstream, so this is a value with a known
          shape rather than an opaque one. Read only. */
    print("read @0      ");
    print_hex(flash_read32(0));
    print("\\r\\n");

    /* 3. Repeatability, and the cost of a single word.
          Two reads of one offset, printed separately so a mismatch shows both
          values rather than just failing. The second is timed: it is the small
          read that has never been measured, because a host cannot time a ~1 us
          operation through a ~35 ms JTAG register read. */
    unsigned int first  = flash_read32(FLASH_TEST_OFFSET);
    unsigned int t0 = flash_cycles();
    unsigned int second = flash_read32(FLASH_TEST_OFFSET);
    unsigned int t1 = flash_cycles();
    print("read @test   ");
    print_hex(first);
    print(" ");
    print_hex(second);
    print(first == second ? "  same" : "  DIFFER");
    print("  2nd read ");
    print_hex(t1 - t0);
    print(" cycles\\r\\n");

    /* 4. Sequential throughput, as cycles for BENCH_WORDS words.

          What this measures is the rate the CPU SEES, not the rate the flash
          sustains. The flash is in a main=1 PMA region, so the D-cache is in
          the path: one miss per line fetches the whole line and the rest of
          that line are hits. The two numbers differ by the line size, and that
          difference is the point of caching it.

          `sum` is a plain local: the loads inside flash_read32 are already
          volatile so none can be elided, and a volatile accumulator would add
          a store per iteration and measure that instead. Printing it keeps the
          loop alive and distinguishes a run that read zeros from one that read
          data. */
    unsigned int sum = 0;
    unsigned int b0 = flash_cycles();
    for (unsigned int i = 0; i < BENCH_WORDS; i++) {
        sum += flash_read32(FLASH_TEST_OFFSET + i * 4);
    }
    unsigned int b1 = flash_cycles();
    print("read bench   ");
    print_hex(b1 - b0);
    print(" cycles / ");
    print_hex(BENCH_WORDS);
    print(" words, sum ");
    print_hex(sum);
    print("\\r\\n");
    return answered;
}

/* Erase a sector, program a page into it, and read it back through the memory
   map -- reporting the timings and every mismatch.

   Everything here stays inside FLASH_SCRATCH. The pattern includes the pass
   number so a stale readback from a previous cycle is visible as wrong data
   rather than passing as correct: a verify against a constant pattern cannot
   tell a successful reprogram from a program that did nothing.

   The readback goes through the MEMORY MAP while the write goes through the
   controller, so agreement between them also cross-checks the two paths
   against each other. */
static void flash_write_test(unsigned int pass) {
    unsigned int page[FLASH_PAGE_SIZE / 4];
    for (unsigned int i = 0; i < FLASH_PAGE_SIZE / 4; i++) {
        page[i] = (pass << 16) ^ (i * 0x01010101u) ^ 0xa5a5a5a5u;
    }

    unsigned int erase_cycles = flash_sector_erase(FLASH_SCRATCH);

    /* Erased flash is all ones. Checking this before programming separates "the
       erase did nothing" from "the program did nothing" -- without it, a
       readback matching the previous pass's data would be ambiguous between
       the two.

       READ THROUGH THE MEMORY MAP, not through the controller. The controller's
       response path is the one that is broken -- the JEDEC ID comes back as
       zeros through it -- so a verify built on it could not distinguish a
       failed write from a failed read. The memory map is independently
       cross-checked against `apollo flash-read` at two offsets, so it is the
       only trustworthy observer here.

       The flush is required because that mapping is cached: a line read before
       the erase would otherwise survive it and report pre-erase contents. */
    flash_cache_flush();
    unsigned int after_erase = flash_read32(FLASH_SCRATCH);

    print("erase 4K     ");
    print_hex(erase_cycles & ~FLASH_WAIT_TIMEOUT);
    if (erase_cycles & FLASH_WAIT_TIMEOUT) {{
        print(" *** TIMEOUT -- BUSY never cleared, status read is broken\\r\\n");
        return;
    }}
    print(" cycles, after ");
    print_hex(after_erase);
    print(after_erase == 0xffffffffu ? "  erased\\r\\n" : "  NOT ERASED\\r\\n");

    /* Stop BEFORE programming rather than after. If the erase did not take, a
       verify afterwards cannot distinguish "the program worked" from "the old
       contents happened to match", and programming over un-erased flash can
       only clear bits -- so the result would be neither a clean write nor a
       clean failure. */
    if (after_erase != 0xffffffffu) {
        print("stopping     sector did not erase; not programming into an "
              "unknown state\\r\\n");
        return;
    }

    unsigned int program_cycles =
        flash_page_program(FLASH_SCRATCH, page, FLASH_PAGE_SIZE / 4);

    /* Verify through the MEMORY MAP -- the path that is proven correct against
       an independent reader. Flushed first so every word comes from the flash
       rather than from a line cached before the program.

       Counts mismatches and keeps the first: a count says how bad it is, the
       first bad word says what went wrong. */
    flash_cache_flush();
    unsigned int bad = 0;
    unsigned int bad_index = 0, bad_want = 0, bad_got = 0;
    for (unsigned int i = 0; i < FLASH_PAGE_SIZE / 4; i++) {
        unsigned int got = flash_read32(FLASH_SCRATCH + i * 4);
        if (got != page[i]) {
            if (!bad) {
                bad_index = i;
                bad_want = page[i];
                bad_got = got;
            }
            bad++;
        }
    }

    /* The same word through the CONTROLLER, reported alongside rather than
       checked. This is the diagnostic the whole write test doubles as.

       The controller's response path is broken -- the JEDEC ID reads zeros
       through it. If the memory map above shows the programmed data, then the
       controller successfully ISSUED the erase and program commands and only
       fails to READ RESPONSES back. If the memory map shows unchanged data,
       the controller is not issuing commands at all. Those are different
       faults with different fixes, and no simulation has been able to tell
       them apart because every stage passes in simulation. */
    unsigned int via_ctrl = flash_read32_uncached(FLASH_SCRATCH);

    print("program 256B ");
    print_hex(program_cycles & ~FLASH_WAIT_TIMEOUT);
    if (program_cycles & FLASH_WAIT_TIMEOUT) {{
        print(" *** TIMEOUT");
    }}
    print(" cycles\\r\\n");

    print("verify       ");
    print_hex(bad);
    print(" of ");
    print_hex(FLASH_PAGE_SIZE / 4);
    print(" words differ");
    if (bad) {
        print(", first @");
        print_hex(bad_index);
        print(" want ");
        print_hex(bad_want);
        print(" got ");
        print_hex(bad_got);
    }
    print("\\r\\n");

    print("via ctrl     ");
    print_hex(via_ctrl);
    print(" want ");
    print_hex(page[0]);
    print(via_ctrl == page[0] ? "  controller reads OK\\r\\n"
                              : "  controller read path broken\\r\\n");
}

int main(void) {
    print("\\r\\n");
    print("RISC-V on Cynthion: block RAM, USB console.\\r\\n");

    /* Prove arithmetic and memory work, not just the store to a peripheral --
       a CPU that can only write a constant is not obviously executing. */
    volatile unsigned int a = 0x12345678;
    volatile unsigned int b = 0x9abcdef0;
    print("sum  ");
    print_hex(a + b);
    print("\\r\\nprod ");
    print_hex(a * 3);
    print("\\r\\n");

    /* The write tests run WRITE_PASSES times and then never again -- not a
       soak. Each pass uses a different pattern so that a matching readback
       proves the reprogram happened rather than merely agreeing with what was
       already in the sector.

       Every pass reports its own numbers rather than a running summary: a
       summary hides which pass went wrong and what it saw. */
    unsigned int pass = 0;
    /* Set once the detailed report has nothing left to say; from then on each pass
       emits one status line instead of the full block. */
    unsigned int quiet = 0;
    /* Latched once the command path is found dead, so the failure is reported
       once in full and then held rather than re-run every second. */
    int dead = 0;
    unsigned int dead_id = 0;
    for (;;) {
        if (dead) {
            /* HELD STATE, not fresh work.

               Once the command path is known dead there is nothing left worth
               running, but the report cannot simply stop either: the firmware
               starts printing before the host has enumerated the device and
               opened the tty, so a reader attaching at an arbitrary moment
               would see silence and be unable to tell a dead board from a
               finished one.

               So the failure block is printed ONCE and then held as a single
               terse line. A late reader still learns the state within a
               second; the log stops filling with identical multi-line blocks;
               and nothing after the failure produces numbers that cannot be
               trusted. 184 repetitions of the full report was the behaviour
               this replaces. */
            print("held         flash command path dead (jedec ");
            print_hex(dead_id);
            print("); nothing further will run\\r\\n");
        } else {
            print("\\r\\n");
            int answered = flash_report(pass);

            /* THE WRITE TEST RUNS EVEN WHEN THE JEDEC READ FAILED, and that is
               the point of running it.

               The earlier version gated this on `answered`, on the reasoning
               that a write verified through a broken read path is not
               verified. That reasoning applied when the verify read back
               through the controller. It no longer does: the verify now uses
               the MEMORY MAP, which is independently cross-checked against
               `apollo flash-read` at two offsets and is the one path here known
               to be correct.

               So this is a diagnostic rather than a dependant. Erase and
               program are commands the controller ISSUES; their effect is
               observable through a reader that works. JEDEC is a command whose
               RESPONSE comes back through the controller. If the programmed
               data appears, the controller issues commands correctly and the
               fault is confined to reading responses. If it does not, the
               controller is not issuing commands at all. Those are different
               faults, and no simulation has separated them -- every stage
               passes in simulation. */
            if (WRITE_TESTS && pass < WRITE_PASSES) {
                flash_write_test(pass);
            } else if (!answered) {
                /* Nothing further to learn: the command path is dead and there
                   is no write test to interrogate it with. */
                dead = 1;
                dead_id = last_jedec;
            }

            /* Latch after the write passes are spent, so the held banner
               reports the final state rather than pre-empting the experiment. */
            if (!answered && WRITE_TESTS && pass + 1 >= WRITE_PASSES) {
                dead = 1;
                dead_id = last_jedec;
            }

            print("tick ");
            print_hex(pass);
            print("\\r\\n");

            /* Full report ONCE, then a status line.

               With the inter-pass delay gone this loop runs flat out, and
               reprinting eleven lines per pass buries the one thing a reader
               wants to know. The detail above is printed while there is still
               something to learn from it -- the write passes -- and after that
               this collapses to a single line.

               It still reprints rather than going silent, because the firmware
               starts before the host has enumerated and a reader attaching later
               would otherwise see nothing at all. */
            if (WRITE_TESTS && pass + 1 >= WRITE_PASSES) {
                quiet = 1;
            } else if (!WRITE_TESTS) {
                quiet = 1;
            }
        }
        pass++;

        /* No delay between passes.

           There was a ~1 second busy-wait here, from when this loop was the only
           evidence the CPU was alive. It is not any more: the LEDs carry liveness
           (green heartbeat) and the console service reattaches after a reconfigure,
           so a late reader gets the next pass regardless.

           It also has to go before any flash speed work. These are cycle counts and
           they should be taken back to back; pacing a benchmark against wall clock
           measures the pacing.

           NOTE for that work: this firmware links ENTIRELY INTO BLOCK RAM -- one
           MEMORY region, .text included. Keep it that way while benchmarking flash.
           Executing from flash while timing flash measures instruction fetch
           contending with the reads under test, not the flash. */
    }
    return 0;
}
"""


def write_common(work):
    """Write the files every target shares."""
    (work / "link.ld").write_text(
        LINKER_SCRIPT.format(ram_base=RAM_BASE, ram_size=RAM_SIZE))
    (work / "start.S").write_text(START_S)
    (work / "console.h").write_text(
        CONSOLE_H.format(console_base=CONSOLE_BASE))
    (work / "flash.h").write_text(
        FLASH_H.format(flash_base=FLASH_BASE,
                       flash_csr_base=FLASH_CSR_BASE,
                       flash_test_offset=FLASH_TEST_OFFSET,
                       flash_scratch=FLASH_SCRATCH,
                       flash_probe_base=FLASH_PROBE_BASE,
                       flash_ila_base=FLASH_ILA_BASE,
                       ila_depth=ILA_DEPTH))


def build(target, work, write_tests=False):
    """Compile, link, and emit a raw binary."""
    write_common(work)

    extra_flags = []
    if target == "hello":
        (work / "main.c").write_text(HELLO_C)
        sources = [str(work / "start.S"), str(work / "main.c")]
        # Read-only unless asked. The erase/program tests are gated at compile
        # time rather than by a runtime flag so that a read-only image contains
        # no erase or program opcode at all -- there is then nothing for a wild
        # branch or a decode fault to reach.
        extra_flags = [f"-DWRITE_TESTS={1 if write_tests else 0}"]
    elif target == "softfloat":
        # Measures what floating point costs when libgcc emulates it. Uses the
        # same console.h as hello, so it needs no CoreMark machinery.
        sources = [str(work / "start.S"),
                   str(PORT / "softfloat_bench.c")]
        extra_flags = [f"-I{work}"]
    elif target == "coremark":
        if not COREMARK.exists():
            emit(f"CoreMark sources not found at {COREMARK}")
            emit("  They are vendored in the VexiiRiscv submodule; run")
            emit("  git submodule update --init --recursive")
            return None
        sources = [str(work / "start.S"),
                   str(PORT / "core_portme.c")]
        sources += [str(COREMARK / f) for f in
                    ("core_main.c", "core_list_join.c", "core_matrix.c",
                     "core_state.c", "core_util.c")]
        # ITERATIONS is fixed rather than auto-scaled: CoreMark normally grows
        # the count until a run takes 10 s, which needs a wall clock. Ticks
        # here are CPU cycles, and a fixed count makes runs directly
        # comparable between configurations.
        extra_flags = [f"-I{COREMARK}", f"-I{PORT}",
                       "-DPERFORMANCE_RUN=1", f"-DITERATIONS={ITERATIONS}",
                       "-DMAIN_HAS_NOARGC=1", '-DCOMPILER_FLAGS="-O2"',
                       "-DSEED_METHOD=SEED_VOLATILE"]
    else:
        emit(f"{target}: not vendored.")
        emit("  Dhrystone needs its sources fetched. CoreMark is "
             "available as")
        emit("  --target coremark.")
        return None

    elf = work / f"{target}.elf"
    # -O2 for benchmarks: CoreMark measures the compiler as much as the core,
    # and -Os would understate every configuration equally but pointlessly.
    optimisation = "-O2" if target in ("coremark", "softfloat") else "-Os"
    command = [CC, *ARCH, optimisation, "-g",
               "-nostdlib", "-nostartfiles", "-ffreestanding",
               "-fno-builtin", "-Wall",
               f"-I{work}", *extra_flags,
               "-T", str(work / "link.ld"),
               "-o", str(elf),
               *sources,
               # libgcc, not a libc: it supplies the soft-float helpers
               # (__divdf3, __floatunsidf) that CoreMark's own reporting code
               # needs on a core with no FPU. -nostdlib excludes it, and
               # patching upstream source to avoid double arithmetic would be
               # worse than linking the runtime that exists for this.
               "-lgcc"]

    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        emit("compile failed:")
        for line in (result.stderr or "").strip().splitlines()[:15]:
            emit(f"    {line}")
        return None
    for line in (result.stderr or "").strip().splitlines():
        emit(f"    warning: {line}")

    binary = work / f"{target}.bin"
    # Spawned rather than `check=True`: objcopy is silent on success, and on
    # failure the complaint reaches the log instead of only the terminal.
    rc = spawn([OBJCOPY, "-O", "binary", str(elf), str(binary)])
    if rc != 0:
        raise subprocess.CalledProcessError(rc, OBJCOPY)

    listing = work / f"{target}.lst"
    disassembly = subprocess.run([OBJDUMP, "-d", str(elf)],
                                 capture_output=True, text=True)
    listing.write_text(disassembly.stdout)

    return binary, elf


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--target", default="hello",
                        choices=["hello", "dhrystone", "coremark",
                                 "softfloat"])
    parser.add_argument("--write-tests", action="store_true",
                        help="enable the flash erase/program tests (they write "
                             "to FLASH_SCRATCH; read-only without this)")
    args = parser.parse_args()

    for tool in (CC, OBJCOPY, OBJDUMP):
        if shutil.which(tool) is None:
            print(f"{tool} not found")
            return 1

    OUT.mkdir(parents=True, exist_ok=True)

    emit(f"building {args.target} for rv32im/ilp32")
    emit()

    if args.write_tests:
        emit("  WRITE TESTS ENABLED -- this image erases and "
             f"programs the sector at {FLASH_SCRATCH:#x}")
        emit()
    built = build(args.target, OUT, write_tests=args.write_tests)
    if built is None:
        return 1
    binary, elf = built

    size = binary.stat().st_size
    emit(f"  {binary.name}: {size} bytes "
         f"({100 * size / RAM_SIZE:.1f}% of {RAM_SIZE // 1024} KiB "
         f"block RAM)")

    # The reset vector is the base of RAM, so whatever is at offset 0 runs
    # first. Checking it is _start catches a linker script that reordered
    # sections -- which fails silently and looks like a dead CPU.
    entry = subprocess.run([OBJDUMP, "-d", "--start-address=0",
                            "--stop-address=8", str(elf)],
                           capture_output=True, text=True).stdout
    emit()
    emit("  first instructions at the reset vector:")
    for line in entry.splitlines():
        if ":\t" in line:
            emit(f"    {line.strip()}")

    emit()
    emit(f"  disassembly: {elf.with_suffix('.lst')}")
    emit()
    emit("next: ./gateware/soc/vexii_hello_soc.py --build --program")

    return 0


if __name__ == "__main__":
    sys.exit(main())
