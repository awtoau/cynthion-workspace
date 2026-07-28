#!/usr/bin/env python3
#
# Standalone PAC1954 power-monitor bring-up gateware for Cynthion r1.4.
# See awtoau/cynthion-workspace#82.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Reads the on-board PAC195X power monitor over I2C and exposes the result as
JTAG registers, so it can be driven from the host over the Apollo debug link
exactly like the LED and PHY selftests.

Deliberately standalone: the moondancer SoC has no I2C peripheral, and adding
one plus a RISC-V driver is disproportionate for a single device that only
needs periodic polling.

The device's I2C address is set by a resistor to ground on ADDRSEL and latched
at power-up (DS20006539B Table 6-1: 0R/GND -> 0x10, 499R -> 0x11, ...
226k -> 0x1E). Which resistor is fitted on r1.4 could not be confirmed from
the schematic, so the device address is a host-writable register here and the
host sweeps it. Reading the manufacturer ID back as 0x54 is what identifies
the right one.
"""

from amaranth                            import Signal, Elaboratable, Module, Cat
from luna.gateware.architecture.car      import LunaECP5DomainGenerator
from luna.gateware.interface.jtag        import JTAGRegisterInterface
from luna.gateware.interface.i2c         import I2CRegisterInterface

from .registers import *


CLOCK_FREQUENCIES = {
    "fast": 60,
    "sync": 60,
    "usb":  60,
}

# 60 MHz sync domain. The PAC195X supports up to 400 kHz (DS20006539B 6.1);
# 600 gives 100 kHz, chosen for bring-up margin on a bus whose pull-ups we have
# not characterised. Raise once the link is known good.
I2C_PERIOD_CYC = 600


class PowerMonitorTest(Elaboratable):
    """ Bring-up gateware for the PAC195X power monitor.

    Exposes over JTAG:
      - an applet ID, so the host can confirm which bitstream is loaded
      - a writable I2C device address, so the host can sweep Table 6-1
      - a register-level read against that address
      - status: busy / done / last-ACK, so a NAK is distinguishable from a hang
    """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers

        # Applet ID: proves the host is talking to this bitstream rather than a
        # stale one still resident in SRAM.
        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        pmon = platform.request("power_monitor")

        # PWRDN is declared PinsN in the platform, so driving .o low here
        # de-asserts power-down and enables the device. Getting this wrong is
        # indistinguishable from a wrong address: NAK on everything.
        m.d.comb += pmon.pwrdn.o.eq(0)

        # SLOW low selects the 1024 SPS default rather than 8 SPS
        # (DS20006539B 3.8). Driven deliberately rather than left floating.
        m.d.comb += [
            pmon.slow.o  .eq(0),
            pmon.slow.oe .eq(1),
        ]

        #
        # I2C register interface.
        #
        # dev_address is only ever used inside two data_i assignments in
        # I2CRegisterInterface, so handing it a Signal instead of an int makes
        # the address runtime-writable with no change to LUNA. That keeps this
        # to a single I2C FSM: instantiating one per candidate address would
        # burn 15 copies of the same logic and have them all contend for the
        # same pins.
        #
        dev_address = Signal(7, init=CANDIDATE_ADDRESSES[0])
        registers.add_register(REGISTER_DEV_ADDRESS, value_signal=dev_address,
                               name="dev_address", init=CANDIDATE_ADDRESSES[0])

        m.submodules.i2c = i2c = I2CRegisterInterface(
            pads=pmon,
            period_cyc=I2C_PERIOD_CYC,
            address=dev_address,
            clk_stretch=True,
        )

        reg_address = Signal(8, init=REG_MANUFACTURER_ID)
        registers.add_register(REGISTER_REG_ADDRESS, value_signal=reg_address,
                               name="reg_address", init=REG_MANUFACTURER_ID)

        # A read is a write-strobe to the trigger register; the host then polls
        # DONE and reads DATA. Latch the result so it survives past the strobe.
        read_strobe = Signal()
        registers.add_sfr(REGISTER_READ_TRIGGER, read=0, write_strobe=read_strobe)

        read_data = Signal(8)
        done_flag = Signal()

        with m.If(read_strobe):
            m.d.sync += done_flag.eq(0)
        with m.Elif(i2c.done):
            m.d.sync += [
                read_data .eq(i2c.read_data),
                done_flag .eq(1),
            ]

        m.d.comb += [
            i2c.address      .eq(reg_address),
            i2c.size         .eq(1),
            i2c.read_request .eq(read_strobe),
        ]

        registers.add_read_only_register(REGISTER_READ_DATA, read=read_data)
        registers.add_read_only_register(REGISTER_STATUS,
                                         read=Cat(done_flag, i2c.busy))

        return m
