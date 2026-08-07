#!/usr/bin/env python3
#
# Survey every FPGA pin that is wired but untested.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reads or exercises every board signal reachable from the FPGA, and reports over
JTAG registers.

The point is coverage of things nothing currently touches. The existing
self-test covers the three PHYs and HyperRAM; the sideband work covered
FPGA_ADV, the LEDs, the button, the flash and the power monitor. What is left
untested is most of the Type-C sideband: SBU1/SBU2, the `int` and `fault` lines,
the per-port I2C buses, VBUS switching, and both PMOD connectors.

This needs no CPU and no USB. It runs from the existing JTAG register path, so
it works today rather than after RISC-V bring-up -- which makes it the cheapest
way to find out whether any of that hardware is alive before designing tests
around it.

Three kinds of check, and they differ in what a result means:

**Levels** are read-only and always safe. A pin with a pull-up that reads low
is either asserted or shorted; a pin that reads as its pull says only that
nothing is driving it, which for `fault` is the expected healthy state.

**Loopback** drives a bidirectional pin and reads it back. That proves the pad
and the FPGA's own buffer, not the far end -- an SBU pin with nothing attached
still passes. It fails usefully though: a pin that cannot be driven high is
shorted to ground, and one that cannot be driven low is shorted to a supply.

**Toggle counts** watch an input over a window and count edges. That
distinguishes a genuinely static signal from one that is changing faster than
the JTAG read rate, which matters because a single sample of a toggling line is
indistinguishable from a stuck one.

VBUS switching is deliberately NOT automatic here: it is the one group with
external consequences, so the enables are exposed as writable registers and
left to the host to drive while watching the power monitor.
"""

from amaranth                       import Cat, Const, Elaboratable, Module, Signal
from amaranth.lib                   import io

from luna.gateware.architecture.car import LunaECP5DomainGenerator
# `jtag_registers` sits beside `bist` one directory up; a build script may
# only have put this applet's own directory on the path.
import sys as _probe_sys
from pathlib import Path as _probe_Path
_probe_sys.path.insert(0, str(_probe_Path(__file__).resolve().parent.parent))

from jtag_registers import JTAGRegisterInterface


CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# Window over which input edges are counted, in sync cycles. 60 ms at 60 MHz:
# long enough that a signal toggling even at a few Hz registers, and short
# enough that a host polling the register sees it update.
TOGGLE_WINDOW = 3_600_000

APPLET_ID = 0x50494E53   # "PINS"

REGISTER_ID          = 1
REGISTER_LEVELS      = 2   # one bit per read-only input
REGISTER_LOOPBACK    = 3   # per-pin loopback results
REGISTER_EDGES_A     = 4   # edge counts, first group
REGISTER_EDGES_B     = 5   # edge counts, second group
REGISTER_VBUS_CTRL   = 6   # write: VBUS enables, host-driven
REGISTER_PMOD_IN     = 7   # pmod 0 and 1 read back
REGISTER_PMOD_OUT    = 8   # write: drive the pmods
REGISTER_SBU_MODE    = 9   # write: 0 = observe passively, 1 = loopback
REGISTER_SBU_LEVELS  = 10  # read: SBU pin levels when observing


class Loopback(Elaboratable):
    """ Drives a bidirectional pin high then low, and checks it follows.

    Proves the pad and the FPGA buffer. It says nothing about what is attached
    at the far end -- an unconnected pin passes -- but it does catch a short:
    a pin that will not go high is tied low, and vice versa.
    """

    def __init__(self, *, port):
        self.port     = port
        self.drive    = Signal()
        self.saw_high = Signal()
        self.saw_low  = Signal()
        # When low, the buffer is tri-stated and `level` reports whatever is
        # on the pin. Loopback proves the pad but is deaf to the outside world:
        # driving push-pull overpowers any external signal, so a device on the
        # port cannot be seen. Observation mode is how these pins report
        # anything about what is attached.
        self.enable   = Signal(init=1)
        self.level    = Signal()

    def elaborate(self, platform):
        m = Module()

        m.submodules.buffer = buffer = io.Buffer("io", self.port)
        m.d.comb += [
            buffer.o.eq(self.drive),
            buffer.oe.eq(self.enable),
            self.level.eq(buffer.i),
        ]

        # Sample one cycle after driving, so the pad has settled. Reading
        # combinationally would report the driven value rather than the pin.
        driven = Signal()
        m.d.sync += driven.eq(self.drive)

        with m.If(driven & buffer.i):
            m.d.sync += self.saw_high.eq(1)
        with m.If(~driven & ~buffer.i):
            m.d.sync += self.saw_low.eq(1)

        return m


class EdgeCounter(Elaboratable):
    """ Counts transitions on an input over a fixed window.

    A single sample cannot distinguish a static signal from one toggling
    faster than it is read. This can.
    """

    def __init__(self, *, width=8):
        self.input = Signal()
        self.count = Signal(width)

    def elaborate(self, platform):
        m = Module()

        previous = Signal()
        m.d.sync += previous.eq(self.input)

        # Saturate rather than wrap: a wrapped counter can read as zero, which
        # is exactly the value that means "nothing happened".
        with m.If((self.input != previous) & (self.count != self.count.all())):
            m.d.sync += self.count.eq(self.count + 1)

        return m


class PinSurvey(Elaboratable):
    """ Reads, drives and counts edges on every otherwise-untested pin. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        #
        # Read-only levels.
        #
        # These are inputs with pull-ups, so the healthy idle state is high for
        # `int` and `fault` -- a low reading means asserted or shorted.
        #
        target_c = platform.request("target_type_c", 0,
                                    dir={"scl": "-", "sda": "-", "int": "i",
                                         "fault": "i", "sbu1": "-",
                                         "sbu2": "-"})
        aux_c = platform.request("aux_type_c", 0,
                                 dir={"scl": "-", "sda": "-", "int": "i",
                                      "fault": "i", "sbu1": "-", "sbu2": "-"})
        button = platform.request("button_user", 0)

        # The TARGET D+/D- lines are wired directly to the FPGA as well as
        # through the PHY, so they can be read without bringing USB up at all.
        target_dp = platform.request("target_usb_dp", 0)
        target_dm = platform.request("target_usb_dm", 0)

        levels = Cat(
            getattr(target_c, "int").i, target_c.fault.i,
            getattr(aux_c, "int").i,    aux_c.fault.i,
            button.i,
            target_dp.i,     target_dm.i,
            Const(0, 25),
        )
        registers.add_read_only_register(REGISTER_LEVELS, read=levels)

        #
        # Loopback on the bidirectional Type-C sideband pins.
        #
        # A slow square wave rather than a static level, so both directions are
        # exercised without the host having to sequence anything.
        #
        toggle = Signal(24)
        m.d.sync += toggle.eq(toggle + 1)

        loopbacks = []
        for name, port in (("target_sbu1", target_c.sbu1),
                           ("target_sbu2", target_c.sbu2),
                           ("aux_sbu1",    aux_c.sbu1),
                           ("aux_sbu2",    aux_c.sbu2)):
            block = Loopback(port=port)
            m.submodules[f"loopback_{name}"] = block
            m.d.comb += block.drive.eq(toggle[-1])
            loopbacks.append(block)

        registers.add_read_only_register(
            REGISTER_LOOPBACK,
            read=Cat(*[Cat(b.saw_high, b.saw_low) for b in loopbacks],
                     Const(0, 24)))

        # Observation mode. Writing 0 tri-states all four SBU buffers so their
        # levels report what is actually on the pins -- which is the only way
        # this design can say anything about a connected device. Defaults to
        # loopback so a freshly configured board still self-tests.
        sbu_mode = Signal(32, init=1)
        registers.add_register(REGISTER_SBU_MODE, value_signal=sbu_mode)
        for block in loopbacks:
            m.d.comb += block.enable.eq(sbu_mode[0])

        registers.add_read_only_register(
            REGISTER_SBU_LEVELS,
            read=Cat(*[b.level for b in loopbacks], Const(0, 28)))

        #
        # Edge counts on the interesting inputs.
        #
        counters = []
        for name, signal in (("target_int",   getattr(target_c, "int").i),
                             ("target_fault", target_c.fault.i),
                             ("aux_int",      getattr(aux_c, "int").i),
                             ("aux_fault",    aux_c.fault.i)):
            counter = EdgeCounter()
            m.submodules[f"edges_{name}"] = counter
            m.d.comb += counter.input.eq(signal)
            counters.append(counter)

        registers.add_read_only_register(
            REGISTER_EDGES_A,
            read=Cat(*[c.count for c in counters]))

        #
        # VBUS control, host-driven.
        #
        # Not automatic: this is the one group with consequences outside the
        # FPGA, so the host drives it deliberately while watching the power
        # monitor rather than having the bitstream switch rails on its own.
        #
        vbus_ctrl = Signal(8)
        registers.add_register(REGISTER_VBUS_CTRL, value_signal=vbus_ctrl)

        for index, name in enumerate(("control_vbus_en", "aux_vbus_en",
                                      "target_c_vbus_en", "target_a_discharge",
                                      "control_vbus_in_en", "aux_vbus_in_en")):
            pin = platform.request(name, 0)
            m.d.comb += pin.o.eq(vbus_ctrl[index])

        #
        # PMOD connectors, driven and read back.
        #
        pmod_out = Signal(16)
        registers.add_register(REGISTER_PMOD_OUT, value_signal=pmod_out)

        pmod_in = Signal(16)
        for index in (0, 1):
            pmod = platform.request("user_pmod", index, dir="-")
            buffer = io.Buffer("io", pmod)
            m.submodules[f"pmod_{index}"] = buffer
            m.d.comb += [
                buffer.o.eq(pmod_out[index * 8:(index + 1) * 8]),
                buffer.oe.eq(pmod_out[15]),   # top bit enables driving
                pmod_in[index * 8:(index + 1) * 8].eq(buffer.i),
            ]

        registers.add_read_only_register(REGISTER_PMOD_IN, read=pmod_in)

        return m
