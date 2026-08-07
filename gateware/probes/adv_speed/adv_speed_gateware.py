#!/usr/bin/env python3
#
# FPGA_ADV speed-ceiling test transmitter.
# See awtoau/cynthion-workspace#85.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Streams a counting byte sequence over FPGA_ADV so the link's error rate can be
measured against baud rate.

Sends an incrementing counter rather than a fixed pattern: with a counter, a
dropped byte shows up as a gap in the sequence and a corrupted byte shows up as
a wrong value at a known position. A repeating fixed pattern cannot distinguish
"lost three bytes" from "lost seven" -- it resynchronises and hides the loss,
which is exactly the measurement we need.

Baud is a build-time parameter. The receiver has to agree, and Apollo's SERCOM1
is configured in firmware, so a rate change means rebuilding both sides; making
it runtime-selectable here would imply a flexibility the other end does not
have.

Drive mode is selectable:
  - push-pull  -- actively drives both edges, so no RC limit. Failures here are
                  clocking or sampling, not signal integrity.
  - open-drain -- only pulls low; the rise is governed by the pull-up current.
                  Failures should appear as the rise approaches a bit period.

The crossover between the two is the point of the exercise: it says what an
external pull-up would buy, and whether a board revision should carry one.
"""

from amaranth                            import Cat, Signal, Elaboratable, Module, Mux
from luna.gateware.architecture.car      import LunaECP5DomainGenerator
# `jtag_registers` sits beside `bist` one directory up; a build script may
# only have put this applet's own directory on the path.
import sys as _probe_sys
from pathlib import Path as _probe_Path
_probe_sys.path.insert(0, str(_probe_Path(__file__).resolve().parent.parent))

from jtag_registers import JTAGRegisterInterface


CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# Register 0 is reserved by JTAGRegisterInterface for size auto-negotiation.
REGISTER_ID        = 1
REGISTER_RUN       = 2  # write 1 to transmit, 0 to stop
REGISTER_SENT_LOW  = 3  # bytes transmitted, low 32 bits
REGISTER_BAUD      = 4  # read back the built-in baud, so the host can check

APPLET_ID = 0x53504545  # "SPEE"


class AdvSpeedTest(Elaboratable):
    """ Transmits a counting byte stream on FPGA_ADV at a fixed baud. """

    # 115200 is the lowest rung of the sweep, NOT the operating rate. The link
    # runs at 230400 and that is determined -- see `gateware/probes/sideband/sideband_link.py`.
    # This module exists to challenge that with evidence, which is why it takes
    # a baud at all.
    def __init__(self, baud=115200, open_drain=False, clk_freq_hz=None):
        self.baud        = baud
        self.open_drain  = open_drain
        self.clk_freq_hz = clk_freq_hz

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers

        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)
        registers.add_read_only_register(REGISTER_BAUD, read=self.baud)

        run = Signal()
        registers.add_register(REGISTER_RUN, value_signal=run, name="run", init=0)

        sent = Signal(32)
        registers.add_read_only_register(REGISTER_SENT_LOW, read=sent)

        if self.clk_freq_hz is None:
            self.clk_freq_hz = platform.DEFAULT_CLOCK_FREQUENCIES_MHZ["sync"] * 1e6

        divisor    = int(self.clk_freq_hz // self.baud)
        bit_timer  = Signal(range(divisor))
        bit_strobe = Signal()
        m.d.comb += bit_strobe.eq(bit_timer == 0)
        m.d.sync += bit_timer.eq(Mux(bit_strobe, divisor - 1, bit_timer - 1))

        payload   = Signal(8)
        shifter   = Signal(10, init=0x3FF)   # idle high
        bit_index = Signal(range(10))

        pad = platform.request("int")

        if self.open_drain:
            # Only ever pull low: drive 0 when the bit is low, release
            # otherwise and let the pull-ups bring the line high. This is what
            # makes the rise time -- and therefore the speed ceiling -- a
            # property of the pull-up current rather than of a driver.
            m.d.comb += [
                pad.o  .eq(0),
                pad.oe .eq(~shifter[0]),
            ]
        else:
            m.d.comb += pad.o.eq(shifter[0])
            if hasattr(pad, "oe"):
                m.d.comb += pad.oe.eq(1)

        with m.FSM(domain="sync"):

            with m.State("IDLE"):
                m.d.sync += shifter.eq(0x3FF)
                with m.If(run):
                    m.next = "LOAD"

            # Load on a bit boundary: entering mid-bit would overwrite the
            # shifter before the previous stop bit had been held for its full
            # period, truncating a byte and corrupting the next.
            with m.State("LOAD"):
                with m.If(bit_strobe):
                    m.d.sync += [
                        shifter   .eq((1 << 9) | (payload << 1)),
                        bit_index .eq(0),
                    ]
                    m.next = "SHIFT"

            with m.State("SHIFT"):
                with m.If(bit_strobe):
                    m.d.sync += [
                        shifter   .eq(Cat(shifter[1:], 1)),
                        bit_index .eq(bit_index + 1),
                    ]
                    with m.If(bit_index == 9):
                        m.d.sync += [
                            payload .eq(payload + 1),
                            sent    .eq(sent + 1),
                        ]
                        with m.If(run):
                            m.next = "LOAD"
                        with m.Else():
                            m.next = "IDLE"

        return m
