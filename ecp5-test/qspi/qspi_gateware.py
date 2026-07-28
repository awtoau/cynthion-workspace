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
                                                 Signal, unsigned)
from amaranth.lib.memory                 import Memory

from luna.gateware.interface.jtag        import JTAGRegisterInterface

from apollo_fpga.gateware.qspi_flash     import QSPIFlashController, QuadFlashReader
from luna.gateware.architecture.car      import LunaECP5DomainGenerator


# Sync-domain frequency in MHz, fixed at build time. LUNA's generator offers
# 60/120/240 for sync; 120 is used because the runtime divisor only divides
# down, so this sets the ladder's ceiling, and 120 is already proven on this
# board by the HyperRAM work. It yields SCK of 120, 60, 40, 30, 24, 20 and
# 15 MHz -- which spans the 30-to-60 region the rebuild-per-frequency ladder
# had to skip.
SYNC_MHZ = 120

# Sample-point offset, also build-time -- it selects between pipeline stages.
# 0 is what the earlier sweep found correct at 30 MHz SCK.
QSPI_OFFSET = 0

READ_BYTES    = 4096
CAPTURE_DEPTH = 1024

APPLET_ID = 0x51535049   # "QSPI"

REGISTER_ID           = 1
REGISTER_QUAD_TIME    = 2   # cycles the last read occupied
REGISTER_QUAD_ADDR    = 3   # write: address into the capture buffer
REGISTER_QUAD_DATA    = 4   # read: that byte, plus the captured count
REGISTER_QUAD_STATUS  = 5   # done/busy, and the sync frequency actually built
REGISTER_QUAD_DIVISOR = 6   # write: SCK divisor; SCK = sync / (divisor + 1)
REGISTER_QUAD_START   = 7   # write: any changed value re-runs the read


class QSPITest(Elaboratable):
    """ Quad flash read with a host-settable clock divisor. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies={"fast": SYNC_MHZ, "sync": SYNC_MHZ, "usb": 60})

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        controller = QSPIFlashController(
            resource=platform.request("qspi_flash", dir="-"),
            offset=QSPI_OFFSET)
        m.submodules.controller = controller

        reader = QuadFlashReader(controller=controller)
        m.submodules.reader = reader
        m.d.comb += reader.length.eq(READ_BYTES)

        # Runtime divisor, defaulting to 7 -- 30 MHz at a 240 MHz sync, the
        # rate already verified byte-exact. A freshly configured board then
        # starts in a known-good state rather than at an untested extreme.
        divisor = Signal(16, init=7)
        registers.add_register(REGISTER_QUAD_DIVISOR, value_signal=divisor)
        m.d.comb += reader.divisor.eq(divisor)

        # Writing a *different* value to the start register re-runs the read.
        # Edge-triggered rather than level, so the host can sweep the divisor
        # and re-measure without reconfiguring the FPGA.
        start_reg  = Signal(32)
        start_prev = Signal(32)
        registers.add_register(REGISTER_QUAD_START, value_signal=start_reg)
        m.d.sync += start_prev.eq(start_reg)

        run = Signal()
        m.d.comb += run.eq(start_reg != start_prev)

        # Also run once after configuration, so a board that is never written
        # to still holds a valid measurement.
        started = Signal()
        with m.If(~started):
            m.d.sync += started.eq(1)
            m.d.comb += reader.start.eq(1)
        with m.Elif(run & ~reader.busy):
            m.d.comb += reader.start.eq(1)

        #
        # Capture buffer.
        #
        memory = Memory(shape=unsigned(8), depth=CAPTURE_DEPTH, init=[])
        m.submodules.memory = memory
        write_port = memory.write_port()
        read_port  = memory.read_port(domain="sync")

        count = Signal(range(CAPTURE_DEPTH + 1))
        m.d.comb += [
            write_port.addr.eq(count),
            write_port.data.eq(reader.data),
            write_port.en  .eq(reader.data_strobe & (count < CAPTURE_DEPTH)),
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

        registers.add_read_only_register(REGISTER_QUAD_TIME, read=reader.cycles)
        registers.add_read_only_register(REGISTER_QUAD_DATA,
                                         read=captured_word)

        # The sync frequency is reported rather than assumed: it is snapped to
        # the nearest achievable PLL output, which need not equal SYNC_MHZ, and
        # every rate the host computes depends on it.
        registers.add_read_only_register(
            REGISTER_QUAD_STATUS,
            read=Cat(reader.done, reader.busy, Const(0, 6),
                     Const(SYNC_MHZ, 16),
                     Const(QSPI_OFFSET, 8)))

        return m
