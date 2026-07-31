#!/usr/bin/env python3
#
# Read the HyperRAM's identification registers, and probe for address aliasing.
# See awtoau/cynthion-workspace#109.
# SPDX-License-Identifier: BSD-3-Clause

"""
Identifies the HyperRAM and asks whether it holds more storage than it is marked for.

Two questions, and the first is overdue: **nothing in this workspace records what the
HyperRAM part actually is.** `docs/luna_ecp5_fpga/hyperram-speed.md` characterises it at
220.2 MB/s, the platform file declares only pins, and no document names a manufacturer or
a density. ID0 answers that directly -- it encodes manufacturer, die generation and row
address bit count, from which capacity follows.

The second question follows the ECP5 result (`ecp5-test/fabric/FABRIC_TEST.md`), where a
part marked 12F placed 20,143 of 24,288 LUT4s. HyperRAM is a family where the same
question is worth asking: densities share a package and a pinout, so a smaller die in a
larger address map is a real possibility rather than a fanciful one.

## The registers

HyperBus identification and configuration live in a separate address space, selected by
the controller's `register_space` input, which `hyperram_stress.py` ties to 0. Reading
them is otherwise an ordinary read.

| address | register | what it carries |
|---|---|---|
| 0x000000 | ID0 | manufacturer, die generation, **row address bit count** |
| 0x000001 | ID1 | die revision |
| 0x000800 | CR0 | latency, drive strength, burst behaviour |
| 0x000801 | CR1 | refresh interval on parts that expose it |

Capacity comes out of ID0: rows x columns x 2 bytes. A part reporting fewer row bits than
its marking implies is the small-die signature.

## `final_word` must be held, not pulsed

The controller leaves `READ_DATA` only when `final_word` is asserted **on the same cycle
`read_ready` fires** (`psram.py:253-267`). Pulsing it alongside `start_transfer`, which
is the obvious reading of the interface, is too early: the transfer has not begun, so the
controller never terminates the burst, never returns to `idle`, and the FSM stalls with
exactly one word captured.

That presents identically to a device that answers once and then stops, which is what it
was first mistaken for -- reading ID0 twice stalls the same way, because the stall is in
the first transaction's termination rather than in the second's start.

So `final_word` is driven for the whole of each wait state here, not just at issue.

## The aliasing probe

Writes a distinct value to the first word of each candidate bank, then reads them all
back. If a 32 Mbit die is mapped into a 64 Mbit space, writing bank 1 overwrites bank 0
and the readback shows it -- which a single-bank test cannot detect.

**This writes to HyperRAM, which is volatile and holds nothing at power-on.** No flash,
no bitstream, nothing persistent is touched.

Results are read over JTAG registers, so no CPU is needed -- which matters because the
RISC-V is not up (#91) and the HyperRAM has no other master.

    python3 ecp5-test/hyperram/hyperram_identify.py --build
    ./scripts/hyperram_identify.py
"""

import sys
from pathlib import Path

from amaranth import Elaboratable, Module, Signal, Cat, C

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "ecp5-test"))

from luna.gateware.interface.psram import HyperRAMInterface, HyperRAMPHY
from luna.gateware.architecture.car import LunaECP5DomainGenerator
from luna.gateware.interface.jtag import JTAGRegisterInterface

# Conservative: identification is a handful of transactions, so there is nothing to gain
# from pushing the clock, and 60 MHz is the rate hyperram-speed.md verified as clean.
CLOCK_MHZ = 60

APPLET_ID = 0x48594944  # "HYID"

# Numbering starts at 1: address 0 is claimed by the JTAG register interface itself,
# and adding a second register there fails the build rather than silently colliding.
REGISTER_ID        = 1
REGISTER_ID0       = 2
REGISTER_ID1       = 3
REGISTER_CR0       = 4
REGISTER_CR1       = 5
REGISTER_ALIAS     = 6   # one bit per bank: set if it aliases bank 0
REGISTER_BANK_DATA = 7   # readback of bank 0 after all banks were written
REGISTER_STATE     = 8   # FSM progress, so a hang is diagnosable
REGISTER_BANK1     = 9   # what bank 1 actually holds, not merely whether it differs
REGISTER_BANK2     = 10
REGISTER_BANK3     = 11

# Candidate bank boundaries, in 16-bit words. A 32 Mbit part is 2 Mi words, so these step
# through the densities the package could plausibly hold.
# 2 MiB per bank, so the four banks span 0-6 MiB. Chosen because the probe established
# the real edge at 8 MiB: every bank here is inside real storage, which makes the default
# run demonstrate the finding rather than re-derive it.
#
# The boundary was bracketed by varying this constant: 7.94 MiB holds its marker, 8 MiB
# does not, and 9/10.5/12 MiB do not. The declared capacity is 4 MiB, so the part is
# exactly 2x its marking.
BANK_WORDS = 2 * 1024 * 1024
BANKS = 4

# Retention wait between writing the banks and reading them back.
#
# 12 ms at 60 MHz. HyperRAM self-refreshes on a ~64 ms per-row budget and the
# controller refreshes only the region it believes exists, so if the space above the
# declared 4 MiB is unrefreshed it decays inside this window. Long enough to expose that,
# short enough that a JTAG poll still catches the result. On expiry the FSM reads the
# banks back and compares; decayed data shows up as a bank no longer holding its marker.
RETENTION_CYCLES = 720_000


class HyperRAMIdentify(Elaboratable):
    """Reads ID/CR registers, then tests whether the address space aliases."""

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies={"fast": CLOCK_MHZ * 2, "sync": CLOCK_MHZ, "usb": 60})

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        ram_bus = platform.request("ram")
        psram_phy = HyperRAMPHY(bus=ram_bus)
        m.submodules.psram_phy = psram_phy
        psram = HyperRAMInterface(phy=psram_phy.phy)
        m.submodules.psram = psram

        id0 = Signal(16)
        id1 = Signal(16)
        cr0 = Signal(16)
        cr1 = Signal(16)
        alias_bits = Signal(BANKS)
        bank0_data = Signal(16)
        bank1_data = Signal(16)
        bank2_data = Signal(16)
        bank3_data = Signal(16)
        wait_count = Signal(range(RETENTION_CYCLES + 1))
        state_num  = Signal(8)
        bank_index = Signal(range(BANKS + 1))

        registers.add_read_only_register(REGISTER_ID0, read=id0)
        registers.add_read_only_register(REGISTER_ID1, read=id1)
        registers.add_read_only_register(REGISTER_CR0, read=cr0)
        registers.add_read_only_register(REGISTER_CR1, read=cr1)
        registers.add_read_only_register(REGISTER_ALIAS, read=alias_bits)
        registers.add_read_only_register(REGISTER_BANK_DATA, read=bank0_data)
        registers.add_read_only_register(REGISTER_STATE, read=state_num)
        registers.add_read_only_register(REGISTER_BANK1, read=bank1_data)
        registers.add_read_only_register(REGISTER_BANK2, read=bank2_data)
        registers.add_read_only_register(REGISTER_BANK3, read=bank3_data)

        m.d.comb += psram.single_page.eq(0)

        def issue(address, *, register, write, data=0):
            """Start one single-word transaction."""
            return [
                psram.address.eq(address),
                psram.register_space.eq(register),
                psram.perform_write.eq(write),
                psram.write_data.eq(data),
                psram.start_transfer.eq(1),
            ]

        # Every transaction here is one word, so final_word is always true. It has to be
        # held rather than pulsed: the controller samples it when read_ready fires, which
        # is well after start_transfer. See the note above.
        m.d.comb += psram.final_word.eq(1)

        with m.FSM():
            # --- identification registers ---
            with m.State("ID0"):
                m.d.sync += state_num.eq(1)
                with m.If(psram.idle):
                    m.d.comb += issue(0x000000, register=1, write=0)
                    m.next = "ID0_WAIT"
            with m.State("ID0_WAIT"):
                with m.If(psram.read_ready):
                    m.d.sync += id0.eq(psram.read_data)
                    m.next = "ID0_SETTLE"
            with m.State("ID0_SETTLE"):
                with m.If(psram.idle):
                    m.next = "ID1"

            with m.State("ID1"):
                with m.If(psram.idle):
                    # ID0 of the TOP die: A22 set. On a real dual-die stack this returns
                    # a valid ID with bits 15:14 = 01. On a single die it returns junk or
                    # mirrors the bottom die's ID0.
                    m.d.comb += issue(0x000001, register=1, write=0)
                    m.next = "ID1_WAIT"
            with m.State("ID1_WAIT"):
                with m.If(psram.read_ready):
                    m.d.sync += id1.eq(psram.read_data)
                    m.next = "ID1_WAIT_SETTLE"
            with m.State("ID1_WAIT_SETTLE"):
                # read_ready fires before the controller returns to IDLE, so the next
                # start_transfer must wait for idle or it is silently dropped.
                with m.If(psram.idle):
                    m.next = "CR0"

            with m.State("CR0"):
                with m.If(psram.idle):
                    # CR0 of the top die, same reasoning.
                    m.d.comb += issue(0x000800, register=1, write=0)
                    m.next = "CR0_WAIT"
            with m.State("CR0_WAIT"):
                with m.If(psram.read_ready):
                    m.d.sync += cr0.eq(psram.read_data)
                    m.next = "CR0_WAIT_SETTLE"
            with m.State("CR0_WAIT_SETTLE"):
                # read_ready fires before the controller returns to IDLE, so the next
                # start_transfer must wait for idle or it is silently dropped.
                with m.If(psram.idle):
                    m.next = "CR1"

            with m.State("CR1"):
                with m.If(psram.idle):
                    # ID0 of the top die (address 0, A22 set) -- the decisive one.
                    m.d.comb += issue(0x000801, register=1, write=0)
                    m.next = "CR1_WAIT"
            with m.State("CR1_WAIT"):
                with m.If(psram.read_ready):
                    m.d.sync += cr1.eq(psram.read_data)
                    m.d.sync += bank_index.eq(0)
                    m.next = "CR1_SETTLE"
            with m.State("CR1_SETTLE"):
                with m.If(psram.idle):
                    m.next = "WRITE_BANK"

            # --- aliasing probe ---
            #
            # Write a value unique to each bank, then read bank 0 back. If a smaller die
            # is mapped across this space, a later bank's write lands on bank 0 and the
            # readback shows the last writer rather than 0xB000.
            with m.State("WRITE_BANK"):
                m.d.sync += state_num.eq(2)
                # perform_write and write_data must be held for the WHOLE transfer, not
                # pulsed at issue: the controller samples write_data when it reaches its
                # own WRITE_DATA state, several cycles later. Pulsing them wrote zeros --
                # which then read back as two banks "aliasing" when they were merely both
                # zero. hyperram_stress.py holds them the same way.
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.write_data.eq(0xB000 | bank_index),
                ]
                with m.If(psram.idle):
                    # 0xB0n0: a recognisable tag in the high byte and the bank index in
                    # the low nibble. Cat() here would place the index in the HIGH bits
                    # and the tag would then overwrite it, which silently produced a
                    # constant and made the readback meaningless.
                    m.d.comb += issue(bank_index * BANK_WORDS, register=0, write=1,
                                      data=0xB000 | bank_index)
                    m.next = "WRITE_BANK_BUSY"
            with m.State("WRITE_BANK_BUSY"):
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.write_data.eq(0xB000 | bank_index),
                ]
                # idle is still high on the cycle after issue -- the controller has not
                # started yet. Waiting for it to FALL first is what makes the write
                # actually happen; without this every write was dropped and the readback
                # was all zeros, which then looked like two banks "aliasing".
                with m.If(~psram.idle):
                    m.next = "WRITE_BANK_WAIT"
            with m.State("WRITE_BANK_WAIT"):
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.write_data.eq(0xB000 | bank_index),
                ]
                with m.If(psram.idle):
                    with m.If(bank_index == BANKS - 1):
                        m.d.sync += bank_index.eq(0)
                        m.d.sync += wait_count.eq(0)
                        m.next = "RETAIN_WAIT"
                    with m.Else():
                        m.d.sync += bank_index.eq(bank_index + 1)
                        m.next = "WRITE_BANK"

            with m.State("RETAIN_WAIT"):
                # Hold everything idle for the retention window. Nothing touches the bus,
                # so any loss here is the part failing to hold, not interference.
                m.d.sync += state_num.eq(5)
                m.d.sync += wait_count.eq(wait_count + 1)
                with m.If(wait_count == RETENTION_CYCLES):
                    m.next = "READ_BANK0"

            with m.State("READ_BANK0"):
                m.d.sync += state_num.eq(3)
                with m.If(psram.idle):
                    m.d.comb += issue(0, register=0, write=0)
                    m.next = "READ_BANK0_WAIT"
            with m.State("READ_BANK0_WAIT"):
                with m.If(psram.read_ready):
                    m.d.sync += bank0_data.eq(psram.read_data)
                    m.d.sync += bank_index.eq(1)
                    m.next = "READ_BANK0_SETTLE"
            with m.State("READ_BANK0_SETTLE"):
                with m.If(psram.idle):
                    m.next = "CHECK_BANK"

            # Each later bank is compared against what bank 0 now holds. Equality means
            # they are the same storage.
            with m.State("CHECK_BANK"):
                m.d.sync += state_num.eq(4)
                with m.If(psram.idle):
                    m.d.comb += issue(bank_index * BANK_WORDS, register=0, write=0)
                    m.next = "CHECK_BANK_WAIT"
            with m.State("CHECK_BANK_WAIT"):
                with m.If(psram.read_ready):
                    with m.If(bank_index == 1):
                        m.d.sync += bank1_data.eq(psram.read_data)
                    with m.If(bank_index == 2):
                        m.d.sync += bank2_data.eq(psram.read_data)
                    with m.If(bank_index == 3):
                        m.d.sync += bank3_data.eq(psram.read_data)
                    with m.If(psram.read_data == bank0_data):
                        m.d.sync += alias_bits.eq(alias_bits | (1 << bank_index))
                    with m.If(bank_index == BANKS - 1):
                        m.next = "DONE"
                    with m.Else():
                        m.d.sync += bank_index.eq(bank_index + 1)
                        m.next = "CHECK_BANK_SETTLE"
            with m.State("CHECK_BANK_SETTLE"):
                with m.If(psram.idle):
                    m.next = "CHECK_BANK"

            with m.State("DONE"):
                m.d.sync += state_num.eq(0xD0)

        return m


if __name__ == "__main__":
    if "--build" in sys.argv:
        from cynthion.gateware.platform import CynthionPlatformRev1D4
        CynthionPlatformRev1D4().build(
            HyperRAMIdentify(),
            build_dir=str(Path(__file__).parent / "identify_build"),
            do_program=False)
        print("built into ecp5-test/hyperram/identify_build/")
    else:
        print(__doc__)
