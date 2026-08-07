#!/usr/bin/env python3
#
# Read DEVICE_ID from both Type-C controllers, as a liveness check.
# SPDX-License-Identifier: BSD-3-Clause

"""
Reads register 0x01 from the FUSB302B on each Type-C bus, on a loop.

This is deliberately the smallest useful thing, and it is modelled on how the
sideband bitstream checks the PAC1954: one register, read repeatedly, reported
over JTAG. No state machine, no protocol, no branching.

The dividing line matters more than the code. Anything that needs a timer, a
retry, or a decision based on a previous message is **software** and belongs in
firmware on a CPU: configuration sequences, interrupt handling, PD message
parsing, negotiation. Anything that is "put this byte on the bus and tell me
what came back" is **gateware**, and that is all this does.

Building it the other way round -- a full driver as an FSM -- would produce a
large, hard-to-change state machine for a protocol that is specified in terms of
message types and timeouts. The MIT reference implementation is 323 lines of
C++ with 45 functions; that shape belongs on a processor.

Separate from the bus scanner because two I2C masters cannot share a bus: LUNA's
initiator and register interface both drive SDA, and instantiating both against
the same pads is a driver conflict rather than a design choice.
"""

from amaranth                       import (Cat, Const, Elaboratable, Module, Mux,
                                            Signal)

from luna.gateware.architecture.car import LunaECP5DomainGenerator
from luna.gateware.interface.i2c    import I2CRegisterInterface
# `jtag_registers` sits beside `bist` one directory up; a build script may
# only have put this applet's own directory on the path.
import sys as _probe_sys
from pathlib import Path as _probe_Path
_probe_sys.path.insert(0, str(_probe_Path(__file__).resolve().parent.parent))

from jtag_registers import JTAGRegisterInterface


CLOCK_FREQUENCIES = {"fast": 60, "sync": 60, "usb": 60}

# 100 kHz on a 60 MHz domain, matching the scanner.
I2C_PERIOD_CYC = 600

# Confirmed empirically by the bus scan rather than taken from a document:
# both controllers acknowledge at this address.
FUSB302B_ADDRESS   = 0x22
FUSB_REG_DEVICE_ID = 0x01
FUSB_REG_STATUS0   = 0x40

# STATUS0 bits worth naming. VBUSOK is the useful one here: it reports whether
# the port is powered above roughly 4 V, and it needs no configuration at all --
# unlike a voltage reading, which requires writing MEASURE (0x04) to set a
# comparator threshold and then binary-searching COMP over six iterations. The
# FUSB302B has a comparator, not an ADC.
STATUS0_VBUSOK   = 7
STATUS0_ACTIVITY = 6
STATUS0_COMP     = 5

# The PAC1954, as a control. If the Type-C reads fail but this one works, the
# fault is with those chips rather than with the I2C logic here.
PAC1954_ADDRESS    = 0x10
PAC1954_REG_ID     = 0xFD

APPLET_ID = 0x46555342   # "FUSB"

REGISTER_ID        = 1
REGISTER_DEVICE_ID = 2   # target and aux DEVICE_ID, plus read counts
REGISTER_CONTROL   = 3   # PAC1954 control read, and its count
REGISTER_STATUS0   = 4   # target and aux STATUS0 -- VBUSOK lives here


class FUSB302IDReader(Elaboratable):
    """ Polls DEVICE_ID on both Type-C controllers and a control device. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        values = []
        counts = []
        status = []

        for name, address, register in (
                ("target_type_c", FUSB302B_ADDRESS, FUSB_REG_DEVICE_ID),
                ("aux_type_c",    FUSB302B_ADDRESS, FUSB_REG_DEVICE_ID),
                ("power_monitor", PAC1954_ADDRESS,  PAC1954_REG_ID)):

            reader = I2CRegisterInterface(
                pads=platform.request(name, 0), period_cyc=I2C_PERIOD_CYC,
                address=address)
            m.submodules[f"i2c_{name}"] = reader

            # Alternate between the identity register and STATUS0 on the
            # Type-C buses, so one interface covers both without a second
            # master fighting for the same wires. The PAC1954 has no STATUS0,
            # so it just re-reads its own register.
            second = (register if name == "power_monitor"
                      else FUSB_REG_STATUS0)
            phase = Signal()
            status_value = Signal(8)

            value = Signal(8)
            # Counts completed reads. A value that never changes could be a
            # stuck bus or a device that always returns the same byte; a count
            # that climbs proves transactions are actually completing.
            count = Signal(8)

            m.d.comb += [
                reader.address.eq(Mux(phase, second, register)),
                reader.size.eq(1),
                # Re-issue whenever idle, so this free-runs without the host
                # having to poke it.
                reader.read_request.eq(~reader.busy),
            ]

            with m.If(reader.done):
                m.d.sync += [
                    phase.eq(~phase),
                    count.eq(count + 1),
                ]
                with m.If(phase):
                    m.d.sync += status_value.eq(reader.read_data)
                with m.Else():
                    m.d.sync += value.eq(reader.read_data)

            values.append(value)
            counts.append(count)
            status.append(status_value)

        registers.add_read_only_register(
            REGISTER_DEVICE_ID,
            read=Cat(values[0], values[1], counts[0], counts[1]))

        registers.add_read_only_register(
            REGISTER_CONTROL,
            read=Cat(values[2], counts[2], Const(0, 16)))

        registers.add_read_only_register(
            REGISTER_STATUS0,
            read=Cat(status[0], status[1], Const(0, 16)))

        return m
