#!/usr/bin/env python3
#
# Quad-SPI flash read test bitstream with a runtime-settable clock.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reads the configuration flash in quad mode, mirrors the result into block RAM,
and lets the host change the SCK rate and re-measure without rebuilding.

Two things set SCK, and only one is fixed at build time:

  - the PLL frequency is a bitstream constant. ECP5 output dividers are
    programmed during configuration and cannot be written afterwards, so
    changing it means a rebuild and a reconfigure.
  - the QSPI controller's divisor is an ordinary signal, so
    SCK = sync / (divisor + 1) can be written over JTAG at any time.

Building at 120 MHz sync and sweeping the divisor therefore covers the range
from one bitstream: divisors 0..7 give 120, 60, 40, 30, 24, 20, 17 and 15 MHz.
That matters because the previous ladder rebuilt per frequency and could only
reach 30 and 60 MHz -- it jumped straight over the region where the limit sits.

A custom PLL was tried first, to get arbitrary 480/N frequencies. It did not
work: CLKOP is the feedback path so CLKFB_DIV and CLKOP_DIV are coupled, and
the other dividers did not behave as documented either -- CLKOS_DIV=2 measured
480 MHz on the sync domain rather than 240. Since the runtime divisor supplies
finer coverage than the PLL would have, LUNA's proven divider set is used
unchanged.

Separate from the sideband bitstream because both want the same four flash data
pins -- `spi_flash` and `qspi_flash` are the same wires viewed two ways, so
requesting both in one design is a resource conflict rather than a merge.

The capture buffer remains the point of the design. A throughput figure cannot
distinguish a working quad read from a fast stream of nonsense, and this work
produced two wrong conclusions from summary statistics before byte-level
capture settled them.
"""

import os

from amaranth                            import (Cat, Const, Elaboratable, Module,
                                                 Mux, Signal, unsigned)
from amaranth.lib.memory                 import Memory

# `jtag_registers` sits beside `bist` one directory up; a build script may
# only have put this applet's own directory on the path.
import sys as _probe_sys
from pathlib import Path as _probe_Path
_probe_sys.path.insert(0, str(_probe_Path(__file__).resolve().parent.parent))

from jtag_registers import JTAGRegisterInterface

from apollo_fpga.gateware.qspi_flash     import QSPIFlashController, QuadFlashReader
from apollo_fpga.gateware.variable_clock import VariableClockDomainGenerator


# Sync-domain frequency in MHz, fixed at build time. LUNA's generator offers
# 60/120/240 for sync; 120 is used because the runtime divisor only divides
# down, so this sets the ladder's ceiling, and 120 is already proven on this
# board by the HyperRAM work. It yields SCK of 120, 60, 40, 30, 24, 20 and
# 15 MHz -- which spans the 30-to-60 region the rebuild-per-frequency ladder
# had to skip.
# 96 MHz, giving 48 MHz SCK at divisor 1.
#
# Down from 120. The burst-mode logic pushed the design from 135 MHz to about
# 119 -- under the 120 it needed, so the build failed outright. Rather than
# strip out the measurement to buy back 1%, the clock drops to where there is
# real margin: at 96 MHz the design closes comfortably and stays buildable as
# more is added.
#
# 48 MHz SCK is well inside Lattice's 62 MHz limit for MCLK and costs 20%
# throughput against 60 MHz. The divisor is still writable, so a board can be
# characterised faster; this is the rate the bitstream ships at.
#
# Overridable by environment variable so that `scripts/flash_ceiling.py` can
# build a ladder of sync frequencies in parallel from one source file.
#
# This is the ONE parameter that genuinely cannot be a register: the ECP5's PLL
# output dividers are programmed during configuration and are not writable
# afterwards. Everything else this sweep varies -- SCK divisor, opcode, the
# 0xEB mode byte, burst length and count -- is a JTAG register, so one
# bitstream covers every rung below its own sync rate without a rebuild.
#
# Divisor 0 is legal and gives SCK = sync, measured byte-exact to 144 MHz, so
# the sync rate a bitstream is built at IS its top SCK. Not every value is
# reachable: `usb` divides the same VCO and the ULPI PHY needs exactly 60 MHz,
# so the generator refuses the rest.
SYNC_MHZ = float(os.environ.get("QSPI_SYNC_MHZ", 96))

# Sample-point offset, also build-time -- it selects between pipeline stages.
# 0 is what the earlier sweep found correct at 30 MHz SCK.
QSPI_OFFSET = 0

# Read length.
#
# Down from 32 KiB. A clock ceiling announces itself in the first bytes -- the
# HyperRAM sweep next door found its ceiling where 88% of words were wrong from
# the first word -- so a long read buys nothing a short one does not already
# have, and this one only has to fill the 1 KiB capture buffer and give the
# cycle counter something to count. 2 KiB is 28 us at the slowest rate here.
READ_BYTES    = 2048
CAPTURE_DEPTH = 1024

APPLET_ID = 0x51535049   # "QSPI"

REGISTER_ID           = 1
REGISTER_QUAD_TIME    = 2   # cycles the last read occupied
REGISTER_QUAD_ADDR    = 3   # write: address into the capture buffer
REGISTER_QUAD_DATA    = 4   # read: that byte, plus the captured count
REGISTER_QUAD_STATUS  = 5   # done/busy, and the sync frequency actually built
REGISTER_QUAD_DIVISOR = 6   # write: SCK divisor; SCK = sync / (divisor + 1)
REGISTER_QUAD_START   = 7   # write: any changed value re-runs the read
# Read mode and Continuous Read, in one word:
#
#   1:0    read mode -- 0 = 0x6B, 1 = 0xEB, 2 = 0x03, 3 = 0x0B
#   2      force the next read to omit its opcode (Continuous Read recovery)
#   15:8   the mode byte 0xEB sends after the address. 0xA0 enters Continuous
#          Read, 0xFF leaves it, and the part remembers across a reconfigure.
REGISTER_QUAD_MODE    = 8
REGISTER_PASS_ERRORS  = 9   # mismatches vs the first pass, and a pass count
REGISTER_BURST_LEN    = 10  # write: bytes per read in burst mode (0 = off)
REGISTER_BURST_COUNT  = 11  # write: how many reads to perform
REGISTER_BURST_CYCLES = 12  # read: total cycles for the whole burst
REGISTER_BURST_DONE   = 13  # read: reads completed in the last run

# Stride between successive burst addresses. Prime, so the walk visits many
# distinct pages before repeating; a sequential walk would stay inside one page
# and measure the flash's sequential path rather than its random-access one.
BURST_STRIDE = 4099


class BurstSequencer(Elaboratable):
    """ Drives a `QuadFlashReader` for one long read or a run of short ones.

    - One trigger runs `count` reads of `length` bytes at strided addresses,
      counting sync cycles across the whole run in hardware. The host reads the
      total once, so its ~35 ms per JTAG register access is paid per run rather
      than per read, and reads far too short to time from the host become
      measurable.
    - `length` and `address` are held in registers that change only while the
      reader is in IDLE. `QuadFlashReader` counts requests from a copy of
      `length` latched at `start` but decides it has finished by comparing
      against the live signal, so a `length` that moves after the strobe makes
      it request more bytes than it consumes; the surplus stays in the
      controller's pipeline, backpressures `i_stream` and wedges the next read.
    - A trigger is latched, not sampled. The write that arrives while a run is
      in flight starts the next run instead of being dropped.
    - `pending` starts CLEAR: every read is one the host asked for.

    ============  ===  =========================================================
    Signal        Dir  Meaning
    ============  ===  =========================================================
    trigger       in   Strobe; one run. Latched, so it need not be seen.
    burst_len     in   Bytes per read. 0 selects one read of `single_length`.
    burst_count   in   Reads per run. 0 is treated as 1.
    reader_done   in   The reader's level-held completion flag.
    reader_busy   in   The reader's busy flag; gates the first arm of a run.
    start         out  Strobe to the reader, one cycle, reader in IDLE.
    length        out  Bytes for the read being armed. Stable start to done.
    address       out  Address for that read. Stable start to done.
    busy          out  A run is in flight. Superset of `reader_busy`.
    cycles        out  Sync cycles for the whole run, including arming.
    reads         out  Reads completed in the run.
    ============  ===  =========================================================

    `cycles` includes one arming cycle per read -- the cycle in which `start` is
    asserted -- so a per-reader figure is `cycles - reads`. One cycle against
    the ~350 a 64-byte read takes at divisor 1 is below the measurement's
    resolution, and an explicit dead cycle is what makes `length` and `address`
    provably stable across the strobe.
    """

    def __init__(self, *, single_length, stride=BURST_STRIDE):
        self.single_length = single_length
        self.stride        = stride

        self.trigger     = Signal()
        self.burst_len   = Signal(16)
        self.burst_count = Signal(16)
        self.reader_done = Signal()
        self.reader_busy = Signal()

        self.start    = Signal()
        self.length   = Signal(16)
        self.address  = Signal(24)
        self.busy     = Signal()
        # 24 bits, not 32. The longest run here -- 256 reads of 64 bytes -- is
        # about 90k cycles, and the 32-bit version cost enough on the carry
        # chain to fail the build at 120 MHz.
        self.cycles   = Signal(24)
        self.reads    = Signal(16)
        # High for a run of short reads rather than the single long one, so the
        # capture buffer and the pass comparator can stand down: a burst is a
        # timing measurement and its bytes come from scattered addresses.
        self.bursting = Signal()

    def elaborate(self, platform):
        m = Module()

        # Starts CLEAR, so the host triggers the reference read explicitly at a
        # rate it has verified. Starting set makes a freshly configured board
        # read once unasked, at whatever divisor and opcode the registers reset
        # to, and THAT becomes the reference -- identical mismatch counts at
        # 120 and 144 MHz is the tell, since a timing fault cannot produce them.
        pending = Signal(init=0)
        index   = Signal(16)
        count   = Signal(16)
        addr    = Signal(24)
        size    = Signal(16)

        # `done` is a level, not a strobe, so a run driven off the level
        # re-triggers continuously and the cycle counter never stops -- it
        # reported 3.9 billion cycles for 256 four-byte reads, which would be
        # 32 seconds. Advance on the rising edge instead.
        done_prev = Signal()
        done_edge = Signal()
        m.d.sync += done_prev.eq(self.reader_done)
        m.d.comb += done_edge.eq(self.reader_done & ~done_prev)

        m.d.comb += [
            self.length .eq(size),
            self.address.eq(addr),
            self.reads  .eq(index),
        ]

        with m.FSM():
            with m.State("IDLE"):
                # Busy covers a latched trigger as well as a run in flight.
                # Without it there is a cycle between the two in which the host
                # would read done=1, busy=0 and take the previous run's cycle
                # count for the answer to the run about to start.
                m.d.comb += self.busy.eq(pending)
                with m.If(pending & ~self.reader_busy):
                    m.d.sync += [
                        pending .eq(0),
                        index   .eq(0),
                        addr    .eq(0),
                        self.cycles.eq(0),
                        self.bursting.eq(self.burst_len != 0),
                        size    .eq(Mux(self.burst_len == 0,
                                        self.single_length, self.burst_len)),
                        count   .eq(Mux((self.burst_len == 0)
                                        | (self.burst_count == 0),
                                        1, self.burst_count)),
                    ]
                    m.next = "ARM"

            with m.State("ARM"):
                # The reader is in IDLE here: either nothing has run yet, or the
                # cycle before this one carried the rising edge of `done`, which
                # the reader asserts as it leaves DESELECT for IDLE.
                m.d.comb += [self.busy.eq(1), self.start.eq(1)]
                m.d.sync += self.cycles.eq(self.cycles + 1)
                m.next = "WAIT"

            with m.State("WAIT"):
                m.d.comb += self.busy.eq(1)
                m.d.sync += self.cycles.eq(self.cycles + 1)
                with m.If(done_edge):
                    m.d.sync += index.eq(index + 1)
                    with m.If(index + 1 < count):
                        m.d.sync += addr.eq(addr + self.stride)
                        m.next = "ARM"
                    with m.Else():
                        m.next = "IDLE"

        # After the FSM, so that a trigger landing on the cycle IDLE consumes
        # `pending` sets it again rather than being cleared by the same edge.
        # Later assignments in a domain take priority, which is what makes "a
        # trigger is never lost" true rather than nearly true.
        with m.If(self.trigger):
            m.d.sync += pending.eq(1)

        return m


class QSPITest(Elaboratable):
    """ Quad flash read with a host-settable clock divisor. """

    def elaborate(self, platform):
        m = Module()

        clocking = VariableClockDomainGenerator(sync_mhz=SYNC_MHZ)
        m.submodules.clocking = clocking

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        controller = QSPIFlashController(
            resource=platform.request("qspi_flash", dir="-"),
            offset=QSPI_OFFSET)
        m.submodules.controller = controller

        reader = QuadFlashReader(controller=controller)
        m.submodules.reader = reader

        # Runtime divisor, defaulting to 7 -- 30 MHz at a 240 MHz sync, the
        # rate already verified byte-exact. A freshly configured board then
        # starts in a known-good state rather than at an untested extreme.
        divisor = Signal(16, init=7)
        registers.add_register(REGISTER_QUAD_DIVISOR, value_signal=divisor)
        m.d.comb += reader.divisor.eq(divisor)

        # Opcode select, Continuous Read override and the 0xEB mode byte. See
        # the field list at REGISTER_QUAD_MODE.
        mode = Signal(32)
        registers.add_register(REGISTER_QUAD_MODE, value_signal=mode)
        m.d.comb += [
            reader.read_mode.eq(mode[0:2]),
            reader.xip_force.eq(mode[2]),
            reader.mode_byte.eq(mode[8:16]),
        ]


        # Writing a *different* value to the start register re-runs the read.
        # Edge-triggered rather than level, so the host can sweep the divisor
        # and re-measure without reconfiguring the FPGA.
        start_reg  = Signal(32)
        start_prev = Signal(32)
        registers.add_register(REGISTER_QUAD_START, value_signal=start_reg)
        m.d.sync += start_prev.eq(start_reg)

        # Registered, not combinational. `start_reg` is a JTAG register, so a
        # comb path from it runs the whole width of the register decode and
        # then into `sequencer.pending` -- and with the pass comparator
        # pipelined, that decode became this design's critical path and
        # therefore its SCK ceiling. The sequencer latches its trigger, so a
        # cycle of delay costs nothing it can observe.
        run = Signal()
        m.d.sync += run.eq(start_reg != start_prev)

        #
        # Burst mode: many short reads at scattered addresses.
        #
        # The single 32 KiB read measures streaming, which is the case where
        # per-transaction overhead disappears -- and therefore the one case
        # where quad I/O cannot help. RISC-V executing from flash does the
        # opposite: many small independent reads, each paying full overhead.
        # This measures that, and exercises address decoding at the same time,
        # since a run of reads at one address would not.
        sequencer = BurstSequencer(single_length=READ_BYTES)
        m.submodules.sequencer = sequencer

        registers.add_register(REGISTER_BURST_LEN,
                               value_signal=sequencer.burst_len)
        registers.add_register(REGISTER_BURST_COUNT,
                               value_signal=sequencer.burst_count)
        registers.add_read_only_register(REGISTER_BURST_CYCLES,
                                         read=sequencer.cycles)
        registers.add_read_only_register(REGISTER_BURST_DONE,
                                         read=sequencer.reads)

        bursting = sequencer.bursting
        m.d.comb += [
            sequencer.trigger    .eq(run),
            sequencer.reader_done.eq(reader.done),
            sequencer.reader_busy.eq(reader.busy),

            reader.start  .eq(sequencer.start),
            reader.length .eq(sequencer.length),
            reader.address.eq(sequencer.address),
        ]

        #
        # Capture buffer.
        #
        memory = Memory(shape=unsigned(8), depth=CAPTURE_DEPTH, init=[])
        m.submodules.memory = memory
        write_port = memory.write_port()
        read_port  = memory.read_port(domain="sync")

        count = Signal(range(CAPTURE_DEPTH + 1))
        # Declared here because the write port's enable depends on it: only the
        # first pass fills the buffer, later passes compare against it.
        first_pass = Signal(init=1)
        # Bursts are excluded throughout this section. Their bytes come from a
        # different address on every read, so storing them would overwrite the
        # reference the pass comparator is built on and comparing them would
        # count every byte as an error -- a mismatch report from a flash that is
        # working, which is worse than no report.
        capturing = Signal()
        m.d.comb += capturing.eq(~bursting)
        m.d.comb += [
            write_port.addr.eq(count),
            write_port.data.eq(reader.data),
            write_port.en  .eq(reader.data_strobe & first_pass & capturing
                              & (count < CAPTURE_DEPTH)),
        ]

        # Reset the write pointer on each run. Without this a re-measurement
        # finds the buffer already full and captures nothing, so the host would
        # read stale bytes from the previous divisor and call it a pass.
        with m.If(reader.start):
            m.d.sync += count.eq(0)
        with m.Elif(reader.data_strobe & (count < CAPTURE_DEPTH)):
            m.d.sync += count.eq(count + 1)

        capture_addr = Signal(range(CAPTURE_DEPTH))
        registers.add_register(REGISTER_QUAD_ADDR, value_signal=capture_addr)
        m.d.comb += read_port.addr.eq(capture_addr)

        # Register the capture readback before it reaches the JTAG mux.
        #
        # Without this the critical path runs block RAM -> LUTs -> JTAG
        # instruction register and closes at only ~137 MHz, which caps the
        # whole design and makes a 240 MHz sync domain unbuildable. That is
        # purely test scaffolding limiting the measurement: the readback is
        # asynchronous to the transfer, so an extra cycle of latency costs
        # nothing.
        captured_word = Signal(32)
        m.d.sync += captured_word.eq(Cat(read_port.data, Const(0, 8), count))

        #
        # Repeat-pass checking, in hardware.
        #
        # The host verifies the first pass against apollo flash-read, which is
        # slow: 4 KiB read back one JTAG register at a time. Later passes are
        # compared here instead, against what the first pass stored, so
        # repeated runs cost no host time.
        #
        # This is what catches intermittency. A rate that works nine times in
        # ten is indistinguishable from one that always works, if it is only
        # ever tried once.
        pass_errors = Signal(16)
        pass_count  = Signal(16)

        check_port = memory.read_port(domain="sync")
        m.d.comb += check_port.addr.eq(count)

        # Two stages, not one, and the second one is what sets this design's
        # fmax -- which is what sets the top SCK, because SCK is sync/2 at
        # best.
        #
        # With one stage the critical path was the block RAM's own output:
        # 4.26 ns of clock-to-Q from `memory.1.0.DOB1`, then three LUT levels
        # of comparison, into `pass_errors`' clock enable -- 7.61 ns, so
        # 131 MHz and no higher however the rest of the design is arranged.
        # Registering the RAM output alongside the byte it is compared against
        # cuts that path at the RAM's own pins and moves the limit elsewhere.
        # The comparison is still of the same two bytes; both sides are simply
        # one cycle later.
        cmp_valid = Signal()
        cmp_data  = Signal(8)
        chk_valid = Signal()
        chk_data  = Signal(8)
        ram_data  = Signal(8)
        m.d.sync += [
            cmp_valid.eq(reader.data_strobe & ~first_pass & capturing
                         & (count < CAPTURE_DEPTH)),
            cmp_data.eq(reader.data),

            chk_valid.eq(cmp_valid),
            chk_data .eq(cmp_data),
            ram_data .eq(check_port.data),
        ]

        with m.If(reader.start & capturing):
            m.d.sync += [pass_errors.eq(0), pass_count.eq(pass_count + 1)]
        with m.If(chk_valid & (ram_data != chk_data)):
            m.d.sync += pass_errors.eq(pass_errors + 1)

        # The rising edge of `done`, not the level.
        #
        # `reader.done` stays high until the next read starts, so it is still
        # set from the *previous* read while this one is being armed -- and
        # `capturing` is updated one cycle before `start`. A burst followed by
        # an ordinary read therefore cleared `first_pass` during the arming
        # cycle, before a single byte had been captured, and the comparator
        # spent the rest of the session checking every pass against a buffer
        # nothing had ever written. It reported the same three mismatch counts
        # at 120 and at 144 MHz, which is what gave it away: a timing fault
        # does not produce identical numbers at two clock rates.
        done_prev = Signal()
        m.d.sync += done_prev.eq(reader.done)
        with m.If(reader.done & ~done_prev & first_pass & capturing):
            m.d.sync += first_pass.eq(0)

        registers.add_read_only_register(
            REGISTER_PASS_ERRORS, read=Cat(pass_errors, pass_count))

        registers.add_read_only_register(REGISTER_QUAD_TIME, read=reader.cycles)
        registers.add_read_only_register(REGISTER_QUAD_DATA,
                                         read=captured_word)

        # Status, in one read-only word. Every field is a report; nothing here
        # changes state when read, and no side-effecting register shares the
        # word.
        #
        #   bit    meaning
        #   0      done   -- the run has finished and its results are settled
        #   1      busy   -- a run is in flight
        #   2      xip    -- the part is believed to be in Continuous Read
        #   8:24   sync frequency actually built, MHz
        #   24:32  sample-point offset the bitstream was built with
        #
        # The busy bit is the sequencer's, not the reader's. Between two reads
        # of a burst the reader is momentarily idle with `done` still set from
        # the previous one, so a poll landing there would read a mid-run cycle
        # count and call it the total.
        #
        # The sync frequency is reported rather than assumed: it is snapped to
        # the nearest achievable PLL output, which need not equal SYNC_MHZ, and
        # every rate the host computes depends on it.
        run_busy = Signal()
        run_done = Signal()
        m.d.comb += [
            run_busy.eq(sequencer.busy | reader.busy),
            run_done.eq(reader.done & ~run_busy),
        ]
        registers.add_read_only_register(
            REGISTER_QUAD_STATUS,
            read=Cat(run_done, run_busy, reader.xip, Const(0, 5),
                     Const(int(clocking.actual_sync_mhz), 16),
                     Const(QSPI_OFFSET, 8)))

        return m
