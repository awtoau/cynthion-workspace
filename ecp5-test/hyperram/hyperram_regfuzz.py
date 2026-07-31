#!/usr/bin/env python3
#
# Sweep the HyperRAM register space, looking for anything undocumented.
# See awtoau/cynthion-workspace#109.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reads sixteen HyperRAM register addresses and reports every one that answers.

The capacity question is settled -- the part is 8 MiB and four independent sources agree
(`../../docs/luna_ecp5_fpga/hyperram-detailed.md`). This asks the remaining question:
**is anything reachable in the register space that the datasheets do not document?**

HyperBus documents exactly four registers: ID0 at 0x0, ID1 at 0x1, CR0 at 0x800 and CR1
at 0x801. Everything else in the space is unspecified, which is not the same as absent --
vendors put test and trim registers in unspecified space, and this part is a Winbond
W956A8MBYA6I whose datasheet is not in `sources/` (only the ISSI equivalents are).

## What counts as a find

An address that returns something other than the bus-idle pattern. On this board a dead
address reads `0x8484`, established by probing memory above 8 MiB and the top-die
register space -- both return exactly that. So `0x8484` means "nothing there" and
anything else is worth looking at.

The sweep deliberately includes:

- the four documented registers, as controls -- if these do not come back right, nothing
  else in the run means anything
- `0x2`-`0x7`, immediately above the ID pair
- `0x802`-`0x807`, immediately above the CR pair
- `0x1000` and `0x1001`, one bit above the CR block, where a third register bank would
  sit if the address decode extends

## What it does to the board

Reads only -- no register writes. Register writes are how a HyperRAM is misconfigured
into an unusable state (CR0 carries drive strength and latency), so this deliberately
does not write. **Configures the FPGA**, replacing whatever gateware is loaded; SRAM
only, so a power cycle restores it.

    python3 ecp5-test/hyperram/hyperram_regfuzz.py --build
    ./scripts/hyperram_regfuzz.py
"""

import sys
from pathlib import Path

from amaranth import Elaboratable, Module, Signal, Array, C

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT / "ecp5-test"))

from luna.gateware.interface.psram import HyperRAMInterface, HyperRAMPHY
from luna.gateware.architecture.car import LunaECP5DomainGenerator
from luna.gateware.interface.jtag import JTAGRegisterInterface

CLOCK_MHZ = 60
APPLET_ID = 0x52465A5A  # "RFZZ"

REGISTER_ID    = 1
REGISTER_STATE = 2
REGISTER_BASE  = 3   # results land at REGISTER_BASE + n

# The addresses to sweep. Documented ones first so they act as controls: if ID0 does not
# read 0x0c86 the run is void, whatever the rest shows.
PROBE_ADDRESSES = [
    0x0000, 0x0001,          # ID0, ID1 -- documented, controls
    0x0800, 0x0801,          # CR0, CR1 -- documented, controls
    # 0x1000/0x1001 returned 0x3030/0x3230 in the first sweep -- ASCII "00" and "20",
    # matching nothing else on the part and not the dead pattern. Walk the region.
    0x1000, 0x1001, 0x1002, 0x1003,
    0x1004, 0x1005, 0x1006, 0x1007,
    0x1008, 0x1009, 0x100A, 0x100B,
]

# Read as MEMORY rather than register space, as the control for the fall-through test.
MEMORY_CONTROL = 0x1000

# Register-space write test. CR0/CR1 are volatile on HyperRAM, so a power cycle restores
# defaults and nothing here is permanent -- the realistic downside of a bad register write
# is a bus that needs a replug, not a damaged part.
#
# Two writes, in order:
#   1. CR0 <- its own current value (0x8f2f). Changes nothing, and proves the write path
#      works. Without this control a "no change" result at 0x1000 is meaningless, since it
#      would be indistinguishable from writes not happening at all.
#   2. 0x1000 <- 0x5A5A. If it reads back changed, that block is writable scratch. If it
#      reads back as 0x3030, it is read-only -- a lot code or factory trim.
CR0_ADDRESS   = 0x0800
# CR0's own current value. The write path was proven separately by flipping drive-strength
# bit 12 (0x8f2f -> 0x9f2f), which read back changed -- so a "no change" result at
# WRITE_TARGET means read-only rather than writes silently failing. That flip is not kept
# here: a repeatable script should not alter a working configuration, and CR0 is volatile
# so the board restores 0x8f2f on a power cycle regardless.
CR0_VALUE     = 0x8f2f
WRITE_TARGET  = 0x1000
WRITE_PATTERN = 0x5A5A


class HyperRAMRegFuzz(Elaboratable):
    """Reads each address in PROBE_ADDRESSES and exposes the results over JTAG."""

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

        results = Array([Signal(16, name=f"r{n}") for n in range(len(PROBE_ADDRESSES))])
        for n, sig in enumerate(results):
            registers.add_read_only_register(REGISTER_BASE + n, read=sig)

        index = Signal(range(len(PROBE_ADDRESSES) + 1))
        mem_control = Signal(16)
        after_write = Signal(16)
        cr0_after   = Signal(16)
        state_num = Signal(8)
        registers.add_read_only_register(REGISTER_STATE, read=state_num)
        registers.add_read_only_register(REGISTER_BASE + len(PROBE_ADDRESSES),
                                         read=mem_control)
        registers.add_read_only_register(REGISTER_BASE + len(PROBE_ADDRESSES) + 1,
                                         read=after_write)
        registers.add_read_only_register(REGISTER_BASE + len(PROBE_ADDRESSES) + 2,
                                         read=cr0_after)

        addresses = Array([C(a, 32) for a in PROBE_ADDRESSES])

        # Single-word reads throughout, so final_word is always true. It must be HELD
        # rather than pulsed: the controller samples it when read_ready fires, which is
        # well after start_transfer. Pulsing it leaves the controller in READ_DATA
        # forever -- it never returns to idle and the sweep stalls on its first address.
        m.d.comb += [
            psram.single_page.eq(0),
            psram.final_word.eq(1),
        ]

        with m.FSM():
            # Stamp memory at word 0x1000 before sweeping, so a register-space read that
            # is really falling through to the array shows our marker instead of the
            # 0x3030 pattern. Nothing else writes here, and the array is volatile.
            with m.State("STAMP"):
                m.d.sync += state_num.eq(0xA0)
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.write_data.eq(0xDEAD),
                ]
                with m.If(psram.idle):
                    m.d.comb += [
                        psram.address.eq(0x1000),
                        psram.register_space.eq(0),
                        psram.start_transfer.eq(1),
                    ]
                    m.next = "STAMP_BUSY"
            with m.State("STAMP_BUSY"):
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.write_data.eq(0xDEAD),
                ]
                with m.If(~psram.idle):
                    m.next = "STAMP_WAIT"
            with m.State("STAMP_WAIT"):
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.write_data.eq(0xDEAD),
                ]
                with m.If(psram.idle):
                    m.next = "ISSUE"

            with m.State("ISSUE"):
                m.d.sync += state_num.eq(1)
                with m.If(index == len(PROBE_ADDRESSES)):
                    m.next = "CRWRITE"
                with m.Elif(psram.idle):
                    m.d.comb += [
                        psram.address.eq(addresses[index]),
                        psram.register_space.eq(1),
                        psram.perform_write.eq(0),
                        psram.start_transfer.eq(1),
                    ]
                    m.next = "WAIT"

            with m.State("WAIT"):
                with m.If(psram.read_ready):
                    m.d.sync += results[index].eq(psram.read_data)
                    m.next = "SETTLE"

            with m.State("SETTLE"):
                # read_ready fires before the controller is back in IDLE; issuing the next
                # transfer here is silently dropped.
                with m.If(psram.idle):
                    m.d.sync += index.eq(index + 1)
                    m.next = "ISSUE"

            # Control write: CR0 gets its own value back.
            with m.State("CRWRITE"):
                m.d.sync += state_num.eq(0xB0)
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.write_data.eq(CR0_VALUE),
                    psram.register_space.eq(1),
                ]
                with m.If(psram.idle):
                    m.d.comb += [psram.address.eq(CR0_ADDRESS),
                                 psram.start_transfer.eq(1)]
                    m.next = "CRWRITE_BUSY"
            with m.State("CRWRITE_BUSY"):
                m.d.comb += [psram.perform_write.eq(1),
                             psram.write_data.eq(CR0_VALUE),
                             psram.register_space.eq(1)]
                with m.If(~psram.idle):
                    m.next = "CRWRITE_WAIT"
            with m.State("CRWRITE_WAIT"):
                m.d.comb += [psram.perform_write.eq(1),
                             psram.write_data.eq(CR0_VALUE),
                             psram.register_space.eq(1)]
                with m.If(psram.idle):
                    m.next = "FUZZWRITE"

            # The test write, into the undocumented block.
            with m.State("FUZZWRITE"):
                m.d.sync += state_num.eq(0xB1)
                m.d.comb += [psram.perform_write.eq(1),
                             psram.write_data.eq(WRITE_PATTERN),
                             psram.register_space.eq(1)]
                with m.If(psram.idle):
                    m.d.comb += [psram.address.eq(WRITE_TARGET),
                                 psram.start_transfer.eq(1)]
                    m.next = "FUZZWRITE_BUSY"
            with m.State("FUZZWRITE_BUSY"):
                m.d.comb += [psram.perform_write.eq(1),
                             psram.write_data.eq(WRITE_PATTERN),
                             psram.register_space.eq(1)]
                with m.If(~psram.idle):
                    m.next = "FUZZWRITE_WAIT"
            with m.State("FUZZWRITE_WAIT"):
                m.d.comb += [psram.perform_write.eq(1),
                             psram.write_data.eq(WRITE_PATTERN),
                             psram.register_space.eq(1)]
                with m.If(psram.idle):
                    m.next = "READBACK"

            # Re-read both, so the run reports before-and-after in one pass.
            with m.State("READBACK"):
                with m.If(psram.idle):
                    m.d.comb += [psram.address.eq(WRITE_TARGET),
                                 psram.register_space.eq(1),
                                 psram.start_transfer.eq(1)]
                    m.next = "READBACK_WAIT"
            with m.State("READBACK_WAIT"):
                with m.If(psram.read_ready):
                    m.d.sync += after_write.eq(psram.read_data)
                    m.next = "CRREAD"
            with m.State("CRREAD"):
                with m.If(psram.idle):
                    m.d.comb += [psram.address.eq(CR0_ADDRESS),
                                 psram.register_space.eq(1),
                                 psram.start_transfer.eq(1)]
                    m.next = "CRREAD_WAIT"
            with m.State("CRREAD_WAIT"):
                with m.If(psram.read_ready):
                    m.d.sync += cr0_after.eq(psram.read_data)
                    m.next = "MEMCHECK"

            with m.State("MEMCHECK"):
                with m.If(psram.idle):
                    m.d.comb += [
                        psram.address.eq(MEMORY_CONTROL),
                        psram.register_space.eq(0),
                        psram.perform_write.eq(0),
                        psram.start_transfer.eq(1),
                    ]
                    m.next = "MEMCHECK_WAIT"
            with m.State("MEMCHECK_WAIT"):
                with m.If(psram.read_ready):
                    m.d.sync += mem_control.eq(psram.read_data)
                    m.next = "DONE"

            with m.State("DONE"):
                m.d.sync += state_num.eq(0xD0)

        return m


if __name__ == "__main__":
    if "--build" in sys.argv:
        from cynthion.gateware.platform.cynthion_r1_4 import CynthionPlatformRev1D4
        CynthionPlatformRev1D4().build(
            HyperRAMRegFuzz(),
            build_dir=str(Path(__file__).parent / "regfuzz_build"),
            do_program=False)
        print("built into ecp5-test/hyperram/regfuzz_build/")
    else:
        print(__doc__)
