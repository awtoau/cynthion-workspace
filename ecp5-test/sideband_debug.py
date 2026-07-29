#!/usr/bin/env python3
#
# The FPGA_ADV sideband link, as a drop-in block for any test design.
# SPDX-License-Identifier: BSD-3-Clause

"""
Adds Apollo's single-wire debug link to a design, so it can be asked questions
when USB does not come up.

Every test bitstream this project has built reported over USB and nothing else.
That works until it does not: a design that enumerates but produces no output
gives no way to tell a dead CPU from a wedged peripheral from a working design
whose output never reaches the host. Several hours went into exactly that
ambiguity.

FPGA_ADV is a separate channel to the Apollo microcontroller on one wire
(pin T6). It does not touch the USB stack, the ULPI PHYs or the CPU, so it
still answers when those are broken -- which is precisely when a debug channel
earns its area.

Cost is small: the responder is a UART and a CRC, and it needs no PHY.

## Use

    from sideband_debug import SidebandDebug

    m.submodules.sideband = sideband = SidebandDebug()
    m.d.comb += [
        sideband.state.eq(my_state),        # 2 bits, whatever is diagnostic
        sideband.events.eq(my_event_flag),
        sideband.error.eq(my_error_flag),
    ]

Query it from the host with the sideband client in the apollo tests.

## The clock

The responder's bit period is a cycle count fixed at build time, so it is only
correct in a domain that really runs at `clk_freq_hz`. A design that clocks
`sync` faster and leaves the default alone gets a dead link, not a slow one --
at 120 MHz the effective baud doubles and a UART tolerates about +/-2%.

This block therefore pins the responder to a domain of the stated frequency and
says so, rather than inheriting whatever the design happens to use.
"""

import sys
from pathlib import Path

from amaranth               import Elaboratable, Module, Signal, Cat
from amaranth.lib.cdc       import FFSynchronizer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "repos"
                       / "apollo"))
from apollo_fpga.gateware.sideband import SidebandResponder


class SidebandDebug(Elaboratable):
    """ FPGA_ADV responder, wired to the pin, ready to drop into a design.

    Attributes
    ----------
    state : Signal(2), input
        Whatever the design wants to report. Read back over the link.
    events : Signal(), input
        Something happened worth noticing.
    error : Signal(), input
        Something went wrong.
    reconfigured : Signal(), input
        Set after reconfiguration, cleared when polled.
    power_data : Signal(128), input
        VBUS[0..3] then VSENSE[0..3], little-endian 16 bits each. Left at zero
        when the design has no power monitor.
    """

    def __init__(self, *, clk_freq_hz=60e6, baud=115200, domain="sync"):
        self.clk_freq_hz = clk_freq_hz
        self.baud = baud
        self.domain = domain

        self.state = Signal(2)
        self.events = Signal()
        self.error = Signal()
        self.reconfigured = Signal()
        self.power_data = Signal(128)

    def elaborate(self, platform):
        m = Module()

        responder = SidebandResponder(clk_freq_hz=self.clk_freq_hz,
                                      baud=self.baud)
        m.submodules.responder = responder

        # FPGA_ADV is bidirectional: the FPGA answers Apollo on the same wire,
        # so it must release the line when idle rather than driving it forever.
        # The platform resource declares PULLMODE=UP for that reason -- the
        # ECP5 defaults to pull-down while Apollo pulls up, and opposing pulls
        # leave the line mid-rail, which a UART reads as a permanent break.
        pin = platform.request("int", 0)

        # The incoming edge is asynchronous to this domain. Without
        # synchronisation a start bit sampled mid-transition can be seen at
        # different times by different flops, and the link fails
        # intermittently -- the worst kind of failure for a debug channel.
        rx_sync = Signal()
        m.submodules.rx_cdc = FFSynchronizer(pin.i, rx_sync, reset=1)

        m.d.comb += [
            responder.rx.eq(rx_sync),
            pin.o.eq(responder.tx),
            pin.oe.eq(responder.tx_active),

            responder.state.eq(self.state),
            responder.events.eq(self.events),
            responder.error.eq(self.error),
            responder.reconfigured.eq(self.reconfigured),
            responder.power_data.eq(self.power_data),
        ]

        return m
