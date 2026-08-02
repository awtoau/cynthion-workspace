#!/usr/bin/env python3
#
# CPU control of the FPGA_ADV sideband payload.
# SPDX-License-Identifier: BSD-3-Clause

"""
One register, so the CPU decides what the sideband link reports.

`sideband_debug.py` puts Apollo's single-wire debug link (pin T6) in the design
and this SoC has always driven it from hardwired status bits -- whether the
instruction bus had ever moved, whether a byte had ever reached the USB
endpoint, whether any master had ever reported an error. Those are the right
things to report from a bitstream that has no CPU, or whose CPU is the thing
under suspicion, and they are the reason the link exists.

They are the wrong things to report from a bitstream whose firmware is running
and knows more than the fabric does. This register lets the firmware say so.

    +0  ctrl   RW   bit 0-1  state       what the responder reports as `state`
                    bit 2    events
                    bit 3    error
                    bit 4    reconfigured
                    bit 7    own -- take the link from the hardwired bits

**`own` resets to 0 and the hardwired bits win until it is set.** That ordering
is the point: a design that never reaches its firmware, or reaches it and hangs
before this register is written, still answers the sideband with the fabric's
own account of itself. The register adds a voice; it does not replace the one
that was there for the case where the CPU is the problem.

## Register discipline

One address, read/write, plain storage. Reading it returns what was written and
changes nothing, so it is outside the class of hazards `uart16550.py` describes
entirely. There is no status here to keep clear of a side-effecting register
because there is no side-effecting register.

## What is not here

The responder also accepts a 128-bit `power_data` payload -- four VBUS and four
VSENSE readings -- which this design leaves at zero. Filling it from the PAC1954
on the I2C bus would let Apollo read board power over a wire that needs neither
USB nor the CPU's console, which is a genuinely good use of the link. It is not
here because it is 128 more flip-flops in service of a path that cannot be
tested from this side: verifying it means driving the sideband protocol from
Apollo, and nothing in this workspace does that yet.
"""

from amaranth               import Module, Signal
from amaranth.lib           import wiring
from amaranth.lib.wiring    import In, Out

from amaranth_soc           import csr


__all__ = ["SidebandControl"]


# Bit positions in the one register.
CTRL_STATE  = 0     # two bits
CTRL_EVENTS = 2
CTRL_ERROR  = 3
CTRL_RECONF = 4
CTRL_OWN    = 7


class SidebandControl(wiring.Component):
    """One byte of CSR that can take the sideband payload from the fabric.

    Attributes
    ----------
    bus : csr.Interface(addr_width=1, data_width=8)
        The single register.
    fabric_state, fabric_events, fabric_error, fabric_reconfigured : in
        What the design reports when the CPU has not taken the link.
    state, events, error, reconfigured : out
        What to feed to `SidebandDebug`.
    own : Signal(), out
        Which of the two is being reported. Brought out so a design can show it
        somewhere -- an operator looking at a board wants to know whether they
        are reading the fabric or the firmware.
    """

    def __init__(self):
        self._ctrl = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                  access="rw")

        # addr_width=1 -- exactly one register, so the window is one byte and
        # nothing can alias a second address onto it.
        builder = csr.Builder(addr_width=1, data_width=8)
        builder.add("ctrl", self._ctrl)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":                 In(csr.Signature(addr_width=1, data_width=8)),

            "fabric_state":        In(2),
            "fabric_events":       In(1),
            "fabric_error":        In(1),
            "fabric_reconfigured": In(1),

            "state":               Out(2),
            "events":              Out(1),
            "error":               Out(1),
            "reconfigured":        Out(1),
            "own":                 Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        ctrl = self._ctrl.f.data.data
        own  = ctrl[CTRL_OWN]

        m.d.comb += self.own.eq(own)

        with m.If(own):
            m.d.comb += [
                self.state       .eq(ctrl[CTRL_STATE:CTRL_STATE + 2]),
                self.events      .eq(ctrl[CTRL_EVENTS]),
                self.error       .eq(ctrl[CTRL_ERROR]),
                self.reconfigured.eq(ctrl[CTRL_RECONF]),
            ]
        with m.Else():
            m.d.comb += [
                self.state       .eq(self.fabric_state),
                self.events      .eq(self.fabric_events),
                self.error       .eq(self.fabric_error),
                self.reconfigured.eq(self.fabric_reconfigured),
            ]

        return m
