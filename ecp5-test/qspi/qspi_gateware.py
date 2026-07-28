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

from amaranth                            import (Cat, Const, Elaboratable, Module,
                                                 Mux, Signal, unsigned)
from amaranth.lib.memory                 import Memory

from luna.gateware.interface.jtag        import JTAGRegisterInterface

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
SYNC_MHZ = 96

# Sample-point offset, also build-time -- it selects between pipeline stages.
# 0 is what the earlier sweep found correct at 30 MHz SCK.
QSPI_OFFSET = 0

# Read length. 32 KiB exercises the flash across many pages rather than one,
# while the capture window below stays 4 KiB for timing reasons.
READ_BYTES    = 32768
CAPTURE_DEPTH = 1024

APPLET_ID = 0x51535049   # "QSPI"

REGISTER_ID           = 1
REGISTER_QUAD_TIME    = 2   # cycles the last read occupied
REGISTER_QUAD_ADDR    = 3   # write: address into the capture buffer
REGISTER_QUAD_DATA    = 4   # read: that byte, plus the captured count
REGISTER_QUAD_STATUS  = 5   # done/busy, and the sync frequency actually built
REGISTER_QUAD_DIVISOR = 6   # write: SCK divisor; SCK = sync / (divisor + 1)
REGISTER_QUAD_START   = 7   # write: any changed value re-runs the read
REGISTER_QUAD_MODE    = 8   # write: bit 0 selects 0xEB quad I/O over 0x6B
REGISTER_PASS_ERRORS  = 9   # mismatches vs the first pass, and a pass count
REGISTER_BURST_LEN    = 10  # write: bytes per read in burst mode (0 = off)
REGISTER_BURST_COUNT  = 11  # write: how many reads to perform
REGISTER_BURST_CYCLES = 12  # read: total cycles for the whole burst


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

        # Opcode select: 0x6B quad output by default, 0xEB quad I/O when set.
        # Both return data on four lanes; 0xEB also sends the address on four,
        # halving per-transaction overhead from 40 clocks to 20. That is worth
        # 0.2% on a 4 KiB streaming read and 19% on a 32-byte one, so it
        # matters for RISC-V executing from flash rather than for bulk copies.
        mode = Signal(32)
        registers.add_register(REGISTER_QUAD_MODE, value_signal=mode)
        m.d.comb += reader.quad_io.eq(mode[0])

        # Writing a *different* value to the start register re-runs the read.
        # Edge-triggered rather than level, so the host can sweep the divisor
        # and re-measure without reconfiguring the FPGA.
        start_reg  = Signal(32)
        start_prev = Signal(32)
        registers.add_register(REGISTER_QUAD_START, value_signal=start_reg)
        m.d.sync += start_prev.eq(start_reg)

        run = Signal()
        m.d.comb += run.eq(start_reg != start_prev)

        #
        # Burst mode: many short reads at scattered addresses.
        #
        # The single 32 KiB read measures streaming, which is the case where
        # per-transaction overhead disappears -- and therefore the one case
        # where quad I/O cannot help. RISC-V executing from flash does the
        # opposite: many small independent reads, each paying full overhead.
        # This measures that, and exercises address decoding at the same time,
        # since a run of reads at one address would not.
        burst_len   = Signal(16)
        burst_count = Signal(16)
        burst_done  = Signal(16)
        # 24 bits, not 32. The longest burst here -- 256 reads of 64 bytes --
        # is about 35k cycles, so 24 bits is ample, and the shorter carry chain
        # matters: the 32-bit version cost enough timing to fail the build at
        # 120 MHz.
        burst_cycles = Signal(24)
        bursting    = Signal()

        registers.add_register(REGISTER_BURST_LEN, value_signal=burst_len)
        registers.add_register(REGISTER_BURST_COUNT, value_signal=burst_count)
        registers.add_read_only_register(REGISTER_BURST_CYCLES,
                                         read=burst_cycles)

        # KNOWN BROKEN: the burst sequencer wedges the reader.
        #
        # `start` is asserted again on the completion edge, but the reader is
        # not yet back in IDLE, so the strobe is missed and `busy` stays high
        # for ever -- observed as read cycles climbing past 2 billion with
        # done=0.
        #
        # It is left in place rather than removed because the register map and
        # the address plumbing below are correct and reused. The measurement it
        # was written for should not be done this way regardless: a JTAG
        # register read takes ~35 ms against ~1 us for a 4-byte flash read, so
        # the host cannot observe short transfers at all, whatever the gateware
        # does. That belongs in a soft CPU inside the FPGA -- luna_soc ships
        # VexRiscv, which is what moondancer already uses.
        #
        # Addresses stride by a large odd number so successive reads land in
        # different pages and rarely repeat, without needing an LFSR: a
        # sequential walk would stay inside one page and measure the wrong
        # thing.
        burst_addr = Signal(24)

        m.d.comb += reader.length.eq(Mux(bursting, burst_len, READ_BYTES))
        m.d.comb += reader.address.eq(Mux(bursting, burst_addr, 0))

        # Also run once after configuration, so a board that is never written
        # to still holds a valid measurement.
        started = Signal()
        with m.If(~started):
            m.d.sync += started.eq(1)
            m.d.comb += reader.start.eq(1)
        with m.Elif(run & ~reader.busy):
            m.d.sync += [
                bursting.eq(burst_len != 0),
                burst_done.eq(0),
                burst_cycles.eq(0),
                burst_addr.eq(0),
            ]
            m.d.comb += reader.start.eq(1)

        # `done` is a level, not a strobe, so a burst driven off the level
        # re-triggers continuously and the cycle counter never stops -- it
        # reported 3.9 billion cycles for 256 four-byte reads, which would be
        # 32 seconds. Advance on the rising edge instead.
        done_prev = Signal()
        done_edge = Signal()
        m.d.sync += done_prev.eq(reader.done)
        m.d.comb += done_edge.eq(reader.done & ~done_prev)

        with m.If(bursting):
            m.d.sync += burst_cycles.eq(burst_cycles + 1)
            with m.If(done_edge & (burst_done < burst_count)):
                m.d.sync += [
                    burst_done.eq(burst_done + 1),
                    # 4099 is prime, so the stride visits many distinct pages
                    # before repeating rather than cycling through a few.
                    burst_addr.eq(burst_addr + 4099),
                ]
                with m.If(burst_done + 1 < burst_count):
                    m.d.comb += reader.start.eq(1)
                with m.Else():
                    m.d.sync += bursting.eq(0)

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
        m.d.comb += [
            write_port.addr.eq(count),
            write_port.data.eq(reader.data),
            write_port.en  .eq(reader.data_strobe & first_pass
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

        # Registered, so block RAM output into a comparator into a counter is
        # not one combinational path.
        cmp_valid = Signal()
        cmp_data  = Signal(8)
        m.d.sync += [
            cmp_valid.eq(reader.data_strobe & ~first_pass
                         & (count < CAPTURE_DEPTH)),
            cmp_data.eq(reader.data),
        ]

        with m.If(reader.start):
            m.d.sync += [pass_errors.eq(0), pass_count.eq(pass_count + 1)]
        with m.If(cmp_valid & (check_port.data != cmp_data)):
            m.d.sync += pass_errors.eq(pass_errors + 1)

        with m.If(reader.done & first_pass):
            m.d.sync += first_pass.eq(0)

        registers.add_read_only_register(
            REGISTER_PASS_ERRORS, read=Cat(pass_errors, pass_count))

        registers.add_read_only_register(REGISTER_QUAD_TIME, read=reader.cycles)
        registers.add_read_only_register(REGISTER_QUAD_DATA,
                                         read=captured_word)

        # The sync frequency is reported rather than assumed: it is snapped to
        # the nearest achievable PLL output, which need not equal SYNC_MHZ, and
        # every rate the host computes depends on it.
        registers.add_read_only_register(
            REGISTER_QUAD_STATUS,
            read=Cat(reader.done, reader.busy, Const(0, 6),
                     Const(int(clocking.actual_sync_mhz), 16),
                     Const(QSPI_OFFSET, 8)))

        return m
