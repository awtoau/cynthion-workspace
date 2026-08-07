#!/usr/bin/env python3
#
# HyperRAM stress test: bulk write/read-back and random access.
# SPDX-License-Identifier: BSD-3-Clause

"""
Exercises HyperRAM harder than the throughput test does.

The throughput test writes a counting pattern once and reads it straight back,
which measures speed but proves very little about correctness: a bus that
latches the wrong address consistently, or a refresh that quietly drops a row,
both pass it. Three phases here look for those.

**Bulk** writes a large span and reads it back, checking every word. This is
the phase that catches address decoding: the pattern is derived from the
address, so a word returned from the wrong place is wrong in a way a
constant fill would hide.

**Random** walks a pseudo-random sequence of addresses, writing and then
re-reading each. Sequential access hides bank and row-boundary problems
because it crosses each boundary once in the same direction; random access
crosses them constantly and in both.

**Retention** writes, waits, and re-reads. HyperRAM is DRAM behind a
self-refresh controller, so a refresh that is misconfigured or starved shows up
as data decaying over milliseconds -- invisible to any test that reads back
immediately.

Each phase reports its own error count, so a failure says which property broke
rather than just that something did.
"""

import sys
from pathlib                             import Path

from amaranth                            import (Cat, Const, Elaboratable, Module,
                                                 Signal, unsigned)
from amaranth.lib                        import io
from amaranth.lib.memory                 import Memory

from luna.gateware.architecture.car      import LunaECP5DomainGenerator
# `jtag_registers` sits beside `bist` one directory up; a build script may
# only have put this applet's own directory on the path.
import sys as _probe_sys
from pathlib import Path as _probe_Path
_probe_sys.path.insert(0, str(_probe_Path(__file__).resolve().parent.parent))

from jtag_registers import JTAGRegisterInterface
from luna.gateware.interface.psram       import HyperRAMPHY

# Ours: luna's `HyperRAMInterface` with tCSHI enforced and the dead low-latency
# branch made a parameter. The PHY beneath it is still upstream's. This harness
# is the validated known-good reference `docs/chips/hyperram/bist-plan.md` names,
# so it has to be running the same controller as everything it is compared with.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "soc"))
from peripherals.hyperram_controller     import HyperRAMController


CLOCK_MHZ = 120

# Words per phase. 16384 x 16 bits = 32 KiB, large enough to cross HyperRAM row
# boundaries (which are 1-2 KiB) many times, so row-crossing bugs cannot hide.
BULK_WORDS   = 16384
RANDOM_OPS   = 4096

# How long to wait before the retention re-read, in sync cycles. 6 ms at
# 120 MHz: the S27KS0641 self-refreshes on a ~64 ms cycle at room temperature,
# so this is well inside spec and any decay here means refresh is broken rather
# than merely marginal.
RETENTION_CYCLES = 720_000

APPLET_ID = 0x48525354   # "HRST"

REGISTER_ID           = 1
REGISTER_STATUS       = 2   # phase, done flags
REGISTER_BULK_ERRORS  = 3
REGISTER_RANDOM_ERRORS = 4
REGISTER_RETAIN_ERRORS = 5
REGISTER_BULK_CYCLES  = 6
REGISTER_FIRST_BAD    = 7   # address of the first mismatch, for diagnosis
REGISTER_FIRST_BAD_DATA = 8 # what was read there, and what was expected


class LFSR(Elaboratable):
    """ 16-bit maximal-length LFSR, for pseudo-random addresses.

    Deterministic on purpose: the host recomputes the same sequence to know
    which addresses were touched, and a failure is reproducible rather than
    a one-off that cannot be investigated.
    """

    def __init__(self, *, width=16, init=0xACE1):
        self.width = width
        self.value = Signal(width, init=init)
        self.step  = Signal()

    def elaborate(self, platform):
        m = Module()
        # x^16 + x^14 + x^13 + x^11 + 1
        feedback = (self.value[0] ^ self.value[2] ^ self.value[3]
                    ^ self.value[5])
        with m.If(self.step):
            m.d.sync += self.value.eq(Cat(self.value[1:], feedback))
        return m


class HyperRAMStressTest(Elaboratable):
    """ Bulk, random-access and retention tests over HyperRAM. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies={"fast": CLOCK_MHZ * 2 if CLOCK_MHZ <= 120 else 240,
                               "sync": CLOCK_MHZ,
                               "usb":  60})

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        ram_bus = platform.request("ram")
        psram_phy = HyperRAMPHY(bus=ram_bus)
        m.submodules.psram_phy = psram_phy
        psram = HyperRAMController(phy=psram_phy.phy, sync_mhz=CLOCK_MHZ)
        m.submodules.psram = psram

        m.submodules.lfsr = lfsr = LFSR()

        index        = Signal(range(BULK_WORDS + 1))
        op_count     = Signal(range(RANDOM_OPS + 1))
        bulk_errors  = Signal(16)
        rand_errors  = Signal(16)
        retain_errors = Signal(16)
        bulk_cycles  = Signal(32)
        wait_count   = Signal(range(RETENTION_CYCLES + 1))
        phase        = Signal(4)
        first_bad    = Signal(32)
        first_bad_data = Signal(32)
        seen_bad     = Signal()

        # The value stored at a given word index: the index in the low byte and
        # its complement in the high byte. Derived from the address so that a
        # word served from the wrong location is detectably wrong -- a constant
        # fill would make address errors invisible.
        # EVERY address bit participates. This was `Cat(addr[:8], ~addr[:8])`,
        # which uses eight address bits and therefore repeats every 256 words
        # across a 4 Mi-word part: an addressing fault above bit 7 wrote and read
        # the same value and scored correct. A 16-bit word cannot encode a 22-bit
        # address, so the high bits are XOR-folded -- aliasing changes the value
        # and is detected, even though the source address is not recoverable.
        # `hyperram_ceiling_top.py` does the same, and at 32 bits can be
        # invertible. See #186.
        def pattern(addr):
            folded = addr[:8] ^ addr[8:16] ^ addr[16:24]
            return Cat(folded, ~folded)

        expected = Signal(16)

        m.d.comb += [
            psram.single_page.eq(0),
            psram.register_space.eq(0),
        ]

        def record_error(counter, addr, got):
            """Count a mismatch, and latch the first one for diagnosis."""
            result = [counter.eq(counter + 1)]
            return result

        with m.FSM():
            with m.State("INIT"):
                m.d.sync += phase.eq(0)
                with m.If(psram.idle):
                    m.d.sync += index.eq(0)
                    m.next = "BULK_WRITE_START"

            #
            # Phase 1: bulk write, then read back and verify.
            #
            with m.State("BULK_WRITE_START"):
                m.d.sync += phase.eq(1)
                m.d.comb += [
                    psram.address.eq(0),
                    psram.perform_write.eq(1),
                    psram.start_transfer.eq(1),
                    psram.final_word.eq(0),
                ]
                m.next = "BULK_WRITE"

            with m.State("BULK_WRITE"):
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.write_data.eq(pattern(index)),
                    psram.final_word.eq(index == BULK_WORDS - 1),
                ]
                m.d.sync += bulk_cycles.eq(bulk_cycles + 1)
                with m.If(psram.write_ready):
                    m.d.sync += index.eq(index + 1)
                    with m.If(index == BULK_WORDS - 1):
                        m.next = "BULK_DRAIN"

            with m.State("BULK_DRAIN"):
                m.d.sync += bulk_cycles.eq(bulk_cycles + 1)
                with m.If(psram.idle):
                    m.d.sync += index.eq(0)
                    m.next = "BULK_READ_START"

            with m.State("BULK_READ_START"):
                m.d.comb += [
                    psram.address.eq(0),
                    psram.perform_write.eq(0),
                    psram.start_transfer.eq(1),
                ]
                m.next = "BULK_READ"

            with m.State("BULK_READ"):
                m.d.comb += [
                    psram.final_word.eq(index == BULK_WORDS - 1),
                    expected.eq(pattern(index)),
                ]
                m.d.sync += bulk_cycles.eq(bulk_cycles + 1)
                with m.If(psram.read_ready):
                    with m.If(psram.read_data != expected):
                        m.d.sync += bulk_errors.eq(bulk_errors + 1)
                        with m.If(~seen_bad):
                            m.d.sync += [
                                seen_bad.eq(1),
                                first_bad.eq(index),
                                first_bad_data.eq(
                                    Cat(psram.read_data, expected)),
                            ]
                    m.d.sync += index.eq(index + 1)
                    with m.If(index == BULK_WORDS - 1):
                        m.d.sync += wait_count.eq(0)
                        m.next = "RETAIN_WAIT"

            #
            # Phase 2: retention. The bulk data is already written; wait, then
            # re-read it. Anything that decays here is a refresh problem.
            #
            with m.State("RETAIN_WAIT"):
                m.d.sync += phase.eq(2)
                m.d.sync += wait_count.eq(wait_count + 1)
                with m.If(wait_count == RETENTION_CYCLES):
                    m.d.sync += index.eq(0)
                    m.next = "RETAIN_READ_START"

            with m.State("RETAIN_READ_START"):
                m.d.comb += [
                    psram.address.eq(0),
                    psram.perform_write.eq(0),
                    psram.start_transfer.eq(1),
                ]
                m.next = "RETAIN_READ"

            with m.State("RETAIN_READ"):
                m.d.comb += [
                    psram.final_word.eq(index == BULK_WORDS - 1),
                    expected.eq(pattern(index)),
                ]
                with m.If(psram.read_ready):
                    with m.If(psram.read_data != expected):
                        m.d.sync += retain_errors.eq(retain_errors + 1)
                    m.d.sync += index.eq(index + 1)
                    with m.If(index == BULK_WORDS - 1):
                        m.d.sync += op_count.eq(0)
                        m.next = "RANDOM_WRITE_START"

            #
            # Phase 3: random access. Each iteration writes one word at a
            # pseudo-random address and immediately reads it back.
            #
            with m.State("RANDOM_WRITE_START"):
                m.d.sync += phase.eq(3)
                with m.If(psram.idle):
                    m.d.comb += [
                        psram.address.eq(lfsr.value),
                        psram.perform_write.eq(1),
                        psram.start_transfer.eq(1),
                        psram.final_word.eq(1),
                    ]
                    m.next = "RANDOM_WRITE"

            with m.State("RANDOM_WRITE"):
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.write_data.eq(pattern(lfsr.value)),
                    psram.final_word.eq(1),
                ]
                with m.If(psram.write_ready):
                    m.next = "RANDOM_READ_START"

            with m.State("RANDOM_READ_START"):
                with m.If(psram.idle):
                    m.d.comb += [
                        psram.address.eq(lfsr.value),
                        psram.perform_write.eq(0),
                        psram.start_transfer.eq(1),
                        psram.final_word.eq(1),
                    ]
                    m.next = "RANDOM_READ"

            with m.State("RANDOM_READ"):
                m.d.comb += [
                    psram.final_word.eq(1),
                    expected.eq(pattern(lfsr.value)),
                ]
                with m.If(psram.read_ready):
                    with m.If(psram.read_data != expected):
                        m.d.sync += rand_errors.eq(rand_errors + 1)
                    m.d.comb += lfsr.step.eq(1)
                    m.d.sync += op_count.eq(op_count + 1)
                    with m.If(op_count == RANDOM_OPS - 1):
                        m.next = "DONE"
                    with m.Else():
                        m.next = "RANDOM_WRITE_START"

            with m.State("DONE"):
                m.d.sync += phase.eq(15)

        registers.add_read_only_register(REGISTER_BULK_ERRORS, read=bulk_errors)
        registers.add_read_only_register(REGISTER_RANDOM_ERRORS,
                                         read=rand_errors)
        registers.add_read_only_register(REGISTER_RETAIN_ERRORS,
                                         read=retain_errors)
        registers.add_read_only_register(REGISTER_BULK_CYCLES, read=bulk_cycles)
        registers.add_read_only_register(REGISTER_FIRST_BAD, read=first_bad)
        registers.add_read_only_register(REGISTER_FIRST_BAD_DATA,
                                         read=first_bad_data)
        registers.add_read_only_register(
            REGISTER_STATUS,
            read=Cat(phase, Const(0, 4), Const(CLOCK_MHZ, 8),
                     Const(BULK_WORDS >> 8, 16)))

        return m
