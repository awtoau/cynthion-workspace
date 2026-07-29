#!/usr/bin/env python3
#
# Measure the area and timing cost of a RISC-V core, on its own.
# SPDX-License-Identifier: BSD-3-Clause

"""
Synthesises a CPU with nothing attached, to get comparable area and Fmax.

The existing RV32 report has a gap that makes its table unusable for choosing a
core: VexiiRiscv was measured with area, Fmax and CoreMark, while VexRiscv has
area and Fmax only -- and that area figure includes the whole USB fabric while
the VexiiRiscv rows do not. So the two cannot be compared, and the obvious
reading (12646 LUTs against 6876) overstates VexRiscv by roughly the size of a
USB stack.

This measures the core alone. Block RAM is attached because a CPU with no
memory optimises away entirely, but nothing else is: no USB, no peripherals, no
JTAG bridge beyond what the variant itself includes.

The result is not a benchmark. It answers "how much silicon does this core
cost, and how fast will it close", which is half of performance-per-LUT; the
CoreMark half needs firmware and is a separate exercise. But it is the half that
is currently missing, and it is one build rather than a toolchain.

    VARIANT = "cynthion+jtag"   # what moondancer ships
    VARIANT = "cynthion"        # same core without the debug module
    VARIANT = "imac"            # a plainer configuration
"""

from amaranth                       import Elaboratable, Module, Signal

from luna.gateware.architecture.car import LunaECP5DomainGenerator
from luna_soc.gateware.core         import blockram
from luna_soc.gateware.cpu          import VexRiscv

from amaranth_soc                   import wishbone
from amaranth_soc.wishbone          import Decoder


CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# Which VexRiscv configuration to measure. luna_soc ships pre-generated Verilog
# for each, so no Scala toolchain is involved -- the update that was blocked on
# Scala 2.11 against Java 25 is a different question from simply using the core.
VARIANT = "cynthion"

# 64 KiB, matching what moondancer allocates. Included because a CPU with no
# memory has nothing to fetch and synthesis removes most of it, which would
# make the area figure meaningless.
RAM_SIZE = 64 * 1024


class CPUArea(Elaboratable):
    """ A CPU and its memory, and deliberately nothing else. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        cpu = VexRiscv(variant=VARIANT, reset_addr=0x00000000)
        m.submodules.cpu = cpu

        ram = blockram.Peripheral(size=RAM_SIZE)
        m.submodules.ram = ram

        decoder = Decoder(addr_width=30, data_width=32, granularity=8,
                          features={"cti", "bte", "err"})
        m.submodules.decoder = decoder
        decoder.add(ram.bus, addr=0x00000000, name="ram")

        # Both CPU ports share the one decoder. An arbiter would be needed for
        # a real system; here the point is the area of the core, and adding one
        # would measure the arbiter too.
        m.d.comb += [
            decoder.bus.adr.eq(cpu.ibus.adr),
            decoder.bus.cyc.eq(cpu.ibus.cyc),
            decoder.bus.stb.eq(cpu.ibus.stb),
            decoder.bus.sel.eq(cpu.ibus.sel),
            decoder.bus.we.eq(cpu.ibus.we),
            decoder.bus.dat_w.eq(cpu.ibus.dat_w),
            cpu.ibus.dat_r.eq(decoder.bus.dat_r),
            cpu.ibus.ack.eq(decoder.bus.ack),
        ]

        return m
