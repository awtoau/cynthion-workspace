#!/usr/bin/env python3
#
# Quad-SPI flash read test bitstream.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reads the configuration flash in quad mode and mirrors the result into block
RAM, readable over JTAG.

Separate from the sideband bitstream because both want the same four flash data
pins: the single-lane path requests `spi_flash` and this requests `qspi_flash`,
which are the same physical wires viewed two ways. Requesting both in one design
is a resource conflict, not a merge.

The capture buffer is the point. A throughput number alone cannot distinguish a
quad read that worked from one that returned plausible-looking nonsense four
times faster, and the single-lane work already produced two wrong conclusions
from a summary statistic. Here the bytes come back verbatim and are compared
against `apollo flash-read`, which uses a wholly independent path.
"""

from amaranth                            import Cat, Const, Elaboratable, Module, Signal
from amaranth.lib.memory                 import Memory
from amaranth                            import unsigned

from luna.gateware.architecture.car      import LunaECP5DomainGenerator
from luna.gateware.interface.jtag        import JTAGRegisterInterface

from apollo_fpga.gateware.qspi_flash     import QSPIFlashController, QuadFlashReader


CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# SCK = sync / (divisor + 1), so 1 gives 30 MHz on a 60 MHz domain -- the rate
# the single-lane path was verified at, which makes the comparison honest: any
# speedup here comes from lane count, not from clocking faster.
QSPI_DIVISOR = 1

# Sample-point offset. The knob the single-lane implementation lacked; 0 is the
# starting point and the ladder script sweeps it.
QSPI_OFFSET = 0

READ_BYTES    = 4096
CAPTURE_DEPTH = 1024

APPLET_ID = 0x51535049   # "QSPI"

REGISTER_ID           = 1
REGISTER_QUAD_TIME    = 2   # cycles the read occupied
REGISTER_QUAD_ADDR    = 3   # write: address into the capture buffer
REGISTER_QUAD_DATA    = 4   # read: that byte, plus captured count
REGISTER_QUAD_STATUS  = 5   # done flag and configuration, for sanity checking


class QSPITest(Elaboratable):
    """ Quad flash read, with the result mirrored into block RAM. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        controller = QSPIFlashController(
            resource=platform.request("qspi_flash", dir="-"),
            offset=QSPI_OFFSET)
        m.submodules.controller = controller

        reader = QuadFlashReader(controller=controller)
        m.submodules.reader = reader
        m.d.comb += [
            reader.length .eq(READ_BYTES),
            reader.divisor.eq(QSPI_DIVISOR),
        ]

        # Start once, shortly after configuration.
        started = Signal()
        with m.If(~started):
            m.d.sync += started.eq(1)
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
            # Stop at the end rather than wrapping: a wrapped buffer shows the
            # tail of a long read where the head is what was asked for.
            write_port.en  .eq(reader.data_strobe & (count < CAPTURE_DEPTH)),
        ]
        with m.If(reader.data_strobe & (count < CAPTURE_DEPTH)):
            m.d.sync += count.eq(count + 1)

        capture_addr = Signal(range(CAPTURE_DEPTH))
        registers.add_register(REGISTER_QUAD_ADDR, value_signal=capture_addr)
        m.d.comb += read_port.addr.eq(capture_addr)

        registers.add_read_only_register(REGISTER_QUAD_TIME,
                                         read=reader.cycles)
        registers.add_read_only_register(
            REGISTER_QUAD_DATA,
            read=Cat(read_port.data, Const(0, 8), count))
        registers.add_read_only_register(
            REGISTER_QUAD_STATUS,
            read=Cat(reader.done, reader.busy, Const(0, 6),
                     Const(QSPI_DIVISOR, 8), Const(QSPI_OFFSET, 8),
                     Const(0, 8)))

        return m
