#!/usr/bin/env python3
#
# HyperRAM FIFO-pattern throughput: alternating writes and reads in chunks.
# SPDX-License-Identifier: BSD-3-Clause

"""
Measures HyperRAM throughput when the access pattern turns around frequently.

The streaming test amortises the command and latency phases over a 2048-word
burst, so it reports what the bus can do at its best: 237 MB/s. A capture
buffer does not get that pattern. Data arrives from USB, sits in RAM, and
leaves for storage -- writes and reads alternate, and every turnaround pays the
command and latency phase again.

That fixed cost was measured at ~23 cycles in the streaming test (2071 cycles
for 2048 words). Against a 4096-word chunk it is noise; against an 8-word chunk
it is three quarters of the transaction. Somewhere between is the granularity a
FIFO should use, and this sweep finds it by measurement rather than arithmetic.

Every chunk size runs as its own phase: write N words, read N words back and
verify them, repeat until a fixed total number of words has moved, then record
the cycle count. Fixing the *total* rather than the repetition count keeps each
phase comparable -- the same volume of data moves at every chunk size, so the
cycle counts are directly a measure of overhead and nothing else.

Two counters run per phase, one for the write half and one for the read half,
because they need not be symmetric: a write ends when the controller latches
the last word, but a read ends when the RAM returns it, and those have
different relationships to the latency phase.

Verification is not optional here. The pattern is derived from the full word
address, so a chunk served from the wrong place is detectably wrong -- and with
the address advancing chunk by chunk, a controller that failed to update the
address between transactions would return the first chunk forever and still
look fast. A constant fill would hide exactly that failure. Mismatches are
counted per phase in gateware.

Timing is in gateware for the same reason as the streaming test: a JTAG
register read takes ~35 ms and the transactions being measured take
microseconds, so anything timed from the host measures the instrument.
"""

import sys
from pathlib                             import Path

from amaranth                            import (Cat, Const, Elaboratable, Module,
                                                 Signal, unsigned)
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
# branch made a parameter. The PHY beneath it is still upstream's. Turnaround is
# what this harness measures, and tCSHI is part of a turnaround -- so the numbers
# it reports are only comparable with the DQS path's once both keep it.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "soc"))
from peripherals.hyperram_controller     import HyperRAMController


# The verified operating point from the streaming test. Not raised here: this
# measures the shape of the access pattern, and changing the clock at the same
# time would confound the two.
CLOCK_MHZ = 120

# Chunk sizes to sweep, in 16-bit words. Powers of two from 8 to 4096, which
# spans the region where the ~23-cycle turnaround goes from dominating the
# transaction to being invisible. 256 words is 512 bytes -- one USB high-speed
# bulk packet, the size a USB capture buffer would actually see -- so it is on
# the ladder as a measured point rather than an interpolation.
CHUNK_SIZES = [8, 16, 32, 64, 128, 256, 512, 1024, 2048, 4096]

# Words moved per phase, in each direction. Fixed across chunk sizes so the
# phases are comparable: the same 16384 words are written and read back at
# every chunk size, and only the number of turnarounds differs. It is a
# multiple of the largest chunk (4096) so every size divides it exactly and no
# phase runs a partial chunk.
WORDS_PER_PHASE = 16384

# The address space the sweep walks. Each chunk goes to a fresh address so the
# pattern check catches an address that fails to advance; wrapping at 64Ki
# words (128 KiB) keeps every access inside a region the retention test has
# already shown to be good, while still crossing HyperRAM's 1-2 KiB row
# boundaries hundreds of times per phase.
ADDRESS_WRAP = 0xFFFF

APPLET_ID = 0x48524649   # "HRFI"

REGISTER_ID            = 1
REGISTER_STATUS        = 2   # phase index, done flag, clock, phase count
REGISTER_RESULT_ADDR   = 3   # write: which phase's result to read
REGISTER_WRITE_CYCLES  = 4   # that phase's write-half cycle count
REGISTER_READ_CYCLES   = 5   # that phase's read-half cycle count
REGISTER_ERRORS        = 6   # that phase's mismatch count
REGISTER_TOTAL_CYCLES  = 7   # whole sweep, as a cross-check on the parts


def chunk_words(index):
    """The chunk size used by phase `index`, for the host to recompute."""
    return CHUNK_SIZES[index]


class HyperRAMFIFOTest(Elaboratable):
    """ Sweeps chunk size for an alternating write/read access pattern. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies={"fast": min(CLOCK_MHZ * 2, 240),
                               "sync": CLOCK_MHZ,
                               "usb":  60})

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        # The non-DQS PHY, for the reason recorded in hyperram-speed.md: the
        # DQS variant assigns bus.clk as a single net and this board declares
        # the HyperRAM clock as a differential pair.
        ram_bus = platform.request("ram")
        psram_phy = HyperRAMPHY(bus=ram_bus)
        m.submodules.psram_phy = psram_phy
        psram = HyperRAMController(phy=psram_phy.phy, sync_mhz=CLOCK_MHZ)
        m.submodules.psram = psram

        phase_count = len(CHUNK_SIZES)

        #
        # Result storage. One entry per phase, read back over JTAG after the
        # sweep finishes -- reading during the sweep would be both useless
        # (35 ms per read against microsecond transactions) and disruptive.
        #
        write_cycles_mem = Memory(shape=unsigned(32), depth=phase_count, init=[])
        read_cycles_mem  = Memory(shape=unsigned(32), depth=phase_count, init=[])
        errors_mem       = Memory(shape=unsigned(32), depth=phase_count, init=[])
        m.submodules.write_cycles_mem = write_cycles_mem
        m.submodules.read_cycles_mem  = read_cycles_mem
        m.submodules.errors_mem       = errors_mem

        wc_write = write_cycles_mem.write_port()
        rc_write = read_cycles_mem.write_port()
        er_write = errors_mem.write_port()
        wc_read  = write_cycles_mem.read_port(domain="sync")
        rc_read  = read_cycles_mem.read_port(domain="sync")
        er_read  = errors_mem.read_port(domain="sync")

        result_addr = Signal(range(phase_count))
        registers.add_register(REGISTER_RESULT_ADDR, value_signal=result_addr)
        m.d.comb += [
            wc_read.addr.eq(result_addr),
            rc_read.addr.eq(result_addr),
            er_read.addr.eq(result_addr),
        ]

        #
        # Sweep state.
        #
        phase        = Signal(range(phase_count + 1))
        chunk_size   = Signal(range(max(CHUNK_SIZES) + 1))
        # Words remaining in the current chunk.
        chunk_left   = Signal(range(max(CHUNK_SIZES) + 1))
        # Words still to move in this phase, counted in one direction.
        phase_left   = Signal(range(WORDS_PER_PHASE + 1))
        # Base address of the current chunk, and the word offset within it.
        base_addr    = Signal(32)
        offset       = Signal(range(max(CHUNK_SIZES) + 1))

        write_cycles = Signal(32)
        read_cycles  = Signal(32)
        errors       = Signal(32)
        total_cycles = Signal(32)
        done         = Signal()

        # The value stored at a given word address: the low 8 bits of the
        # address and their complement. Derived from the address, so a word
        # returned from the wrong place fails the check -- which is what
        # catches an address that does not advance between chunks.
        word_addr = Signal(32)
        m.d.comb += word_addr.eq(base_addr + offset)

        expected = Signal(16)
        # Every address bit, not just the low eight -- see the note in
        # hyperram_stress.py. The docstring above says the pattern wraps at 64Ki;
        # it actually wrapped at 256 words, which is what this fixes.
        folded = word_addr[:8] ^ word_addr[8:16] ^ word_addr[16:24]
        m.d.comb += expected.eq(Cat(folded, ~folded))

        m.d.comb += [
            psram.single_page.eq(0),
            psram.register_space.eq(0),
            psram.write_data.eq(expected),
        ]

        # Total runtime, as a cross-check: it should exceed the sum of the
        # per-phase halves by the bookkeeping states between them, and a
        # wild disagreement would mean a counter is not counting what it
        # claims to.
        with m.If(~done):
            m.d.sync += total_cycles.eq(total_cycles + 1)

        with m.FSM():
            with m.State("INIT"):
                with m.If(psram.idle):
                    m.d.sync += phase.eq(0)
                    m.next = "PHASE_SETUP"

            # Load this phase's chunk size and reset its counters.
            with m.State("PHASE_SETUP"):
                with m.Switch(phase):
                    for i, size in enumerate(CHUNK_SIZES):
                        with m.Case(i):
                            m.d.sync += chunk_size.eq(size)
                m.d.sync += [
                    phase_left.eq(WORDS_PER_PHASE),
                    base_addr.eq(0),
                    write_cycles.eq(0),
                    read_cycles.eq(0),
                    errors.eq(0),
                ]
                m.next = "CHUNK_SETUP"

            with m.State("CHUNK_SETUP"):
                m.d.sync += [
                    chunk_left.eq(chunk_size),
                    offset.eq(0),
                ]
                with m.If(psram.idle):
                    m.next = "WRITE_START"

            #
            # Write half of one chunk.
            #
            with m.State("WRITE_START"):
                # The write-half counter starts here, at the command phase, so
                # the turnaround cost this test exists to measure is inside the
                # measurement rather than outside it.
                m.d.sync += write_cycles.eq(write_cycles + 1)
                m.d.comb += [
                    psram.address.eq(base_addr),
                    psram.perform_write.eq(1),
                    psram.start_transfer.eq(1),
                    psram.final_word.eq(chunk_size == 1),
                ]
                m.next = "WRITE"

            with m.State("WRITE"):
                m.d.sync += write_cycles.eq(write_cycles + 1)
                m.d.comb += [
                    psram.perform_write.eq(1),
                    psram.final_word.eq(chunk_left == 1),
                ]
                with m.If(psram.write_ready):
                    m.d.sync += offset.eq(offset + 1)
                    m.d.sync += chunk_left.eq(chunk_left - 1)
                    with m.If(chunk_left == 1):
                        m.next = "WRITE_DRAIN"

            # Wait for the controller to return to idle before turning the bus
            # around. This is the cost being measured, so it is counted.
            with m.State("WRITE_DRAIN"):
                m.d.sync += write_cycles.eq(write_cycles + 1)
                with m.If(psram.idle):
                    m.d.sync += [
                        chunk_left.eq(chunk_size),
                        offset.eq(0),
                    ]
                    m.next = "READ_START"

            #
            # Read half of the same chunk, verified word by word.
            #
            with m.State("READ_START"):
                m.d.sync += read_cycles.eq(read_cycles + 1)
                m.d.comb += [
                    psram.address.eq(base_addr),
                    psram.perform_write.eq(0),
                    psram.start_transfer.eq(1),
                    psram.final_word.eq(chunk_size == 1),
                ]
                m.next = "READ"

            with m.State("READ"):
                m.d.sync += read_cycles.eq(read_cycles + 1)
                m.d.comb += psram.final_word.eq(chunk_left == 1)
                with m.If(psram.read_ready):
                    with m.If(psram.read_data != expected):
                        m.d.sync += errors.eq(errors + 1)
                    m.d.sync += offset.eq(offset + 1)
                    m.d.sync += chunk_left.eq(chunk_left - 1)
                    with m.If(chunk_left == 1):
                        m.next = "READ_DRAIN"

            with m.State("READ_DRAIN"):
                m.d.sync += read_cycles.eq(read_cycles + 1)
                with m.If(psram.idle):
                    m.next = "CHUNK_NEXT"

            # Advance to the next chunk, or finish the phase.
            with m.State("CHUNK_NEXT"):
                m.d.sync += phase_left.eq(phase_left - chunk_size)
                # Walk forward so each chunk lands somewhere new, wrapping
                # inside a region already known good.
                m.d.sync += base_addr.eq((base_addr + chunk_size) & ADDRESS_WRAP)
                with m.If(phase_left <= chunk_size):
                    m.next = "PHASE_STORE"
                with m.Else():
                    m.next = "CHUNK_SETUP"

            # Commit this phase's results, then move to the next chunk size.
            with m.State("PHASE_STORE"):
                m.d.comb += [
                    wc_write.addr.eq(phase), wc_write.data.eq(write_cycles),
                    wc_write.en.eq(1),
                    rc_write.addr.eq(phase), rc_write.data.eq(read_cycles),
                    rc_write.en.eq(1),
                    er_write.addr.eq(phase), er_write.data.eq(errors),
                    er_write.en.eq(1),
                ]
                m.d.sync += phase.eq(phase + 1)
                with m.If(phase == phase_count - 1):
                    m.next = "DONE"
                with m.Else():
                    m.next = "PHASE_SETUP"

            with m.State("DONE"):
                m.d.sync += done.eq(1)

        registers.add_read_only_register(REGISTER_WRITE_CYCLES, read=wc_read.data)
        registers.add_read_only_register(REGISTER_READ_CYCLES,  read=rc_read.data)
        registers.add_read_only_register(REGISTER_ERRORS,       read=er_read.data)
        registers.add_read_only_register(REGISTER_TOTAL_CYCLES, read=total_cycles)
        registers.add_read_only_register(
            REGISTER_STATUS,
            read=Cat(phase, Const(0, 4), done, Const(0, 3),
                     Const(CLOCK_MHZ, 8), Const(phase_count, 8)))

        return m
