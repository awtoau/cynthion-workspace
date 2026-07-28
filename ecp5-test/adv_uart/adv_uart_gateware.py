#!/usr/bin/env python3
#
# End-to-end test bitstream for the UART advertisement mode.
# See awtoau/cynthion-workspace#68.
#
# SPDX-License-Identifier: BSD-3-Clause

"""
Drives FPGA_ADV with the UART heartbeat so the receive side in Apollo can be
tested against a real transmitter rather than only in simulation.

The pin is push-pull from the FPGA (`Resource("int", 0, Pins("T6", dir="o"))`)
and input-with-pull-up on Apollo (PA09), so only the FPGA ever drives it. The
pull-up holds the line high when the FPGA is unconfigured, which is also the
UART idle state -- an absent FPGA therefore looks like "no heartbeat" rather
than a break condition, and does not get the port.

Also exposes a JTAG register interface so the host can confirm which bitstream
is loaded and toggle transmission on and off. Toggling matters: proving Apollo
sees the heartbeat is only half the test, and proving it *stops* seeing one is
what verifies the port is handed back.
"""

from amaranth                            import Signal, Elaboratable, Module
from luna.gateware.architecture.car      import LunaECP5DomainGenerator
from luna.gateware.interface.jtag        import JTAGRegisterInterface

from apollo_fpga.gateware.advertiser     import ApolloUARTAdvertiser


CLOCK_FREQUENCIES = {
    "fast": 60,
    "sync": 60,
    "usb":  60,
}

# Register 0 is reserved by JTAGRegisterInterface for size auto-negotiation.
REGISTER_ID   = 1
REGISTER_STOP = 2  # write 1 to silence the advertiser, 0 to resume

APPLET_ID = 0x41445556  # "ADUV"


class AdvUARTTest(Elaboratable):
    """ Transmits the UART heartbeat on FPGA_ADV, with host control over stop. """

    def elaborate(self, platform):
        m = Module()

        m.submodules.clocking = LunaECP5DomainGenerator(
            clock_frequencies=CLOCK_FREQUENCIES)

        registers = JTAGRegisterInterface(default_read_value=0xDEADBEEF)
        m.submodules.registers = registers

        registers.add_read_only_register(REGISTER_ID, read=APPLET_ID)

        stop = Signal()
        registers.add_register(REGISTER_STOP, value_signal=stop,
                               name="stop", init=0)

        advertiser = ApolloUARTAdvertiser(pad=platform.request("int"))
        m.submodules.advertiser = advertiser
        m.d.comb += advertiser.stop.eq(stop)

        return m
