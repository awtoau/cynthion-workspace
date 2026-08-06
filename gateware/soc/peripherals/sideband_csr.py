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
                    bit 5    advertise -- ask Apollo for the CONTROL port
                    bit 7    own -- take the link from the hardwired bits
    +1  tx     RW   the byte a PING returns
    +2  rx     R    the low seven bits of the last byte Apollo sent
    +3  rxcnt  R    how many such bytes have arrived, wrapping at 256

## The byte channel

`tx` out, `rx` in, and `rxcnt` to tell a repeat from a silence.

**`rxcnt` rather than a ready flag, and rather than read-to-clear.** A flag
cleared by reading `rx` makes reading a side-effecting operation, which is the
hazard `peripherals/uart16550.py` spends a page on. A count has no such hazard -- the CPU
keeps its own copy and compares -- and it distinguishes "Apollo sent the same
byte again" from "Apollo has sent nothing", which a flag cannot. It wraps rather
than saturating: the CPU is comparing against its own last value, so wrapping is
correct and saturation would look like silence.

**`own` resets to 0 and the hardwired bits win until it is set.** That ordering
is the point: a design that never reaches its firmware, or reaches it and hangs
before this register is written, still answers the sideband with the fabric's
own account of itself. The register adds a voice; it does not replace the one
that was there for the case where the CPU is the problem.

## The port request is not part of the payload

`advertise` is outside `own`: it is not something the link *reports*, it is
something the FPGA *does* -- emit the frame Apollo matches to grant the CONTROL
port (`gateware/sideband_advertise.py`). The fabric has no opinion to override,
so there is no `fabric_advertise`.

**It resets to 0, which is the opposite of upstream.** `ApolloAdvertiser`
advertises from configuration and `ApolloAdvertiserRequestHandler` provides a
`stop` request to end it. Inverting that here is deliberate: a bitstream that
seized CONTROL the moment it configured would take the port away from Apollo's
own debug interface, which is the path used to recover a board that will not
boot. Asking for the port is a decision firmware makes after it is running;
clearing the bit hands the port back one Apollo timeout later.

## Register discipline

Four addresses, plain storage, and **no register whose read changes anything**.
`ctrl` and `tx` read back what was written; `rx` and `rxcnt` are windows onto
state the link updates. That keeps the whole peripheral outside the class of
hazards `peripherals/uart16550.py` describes, and it is why the receive side is a count
rather than a flag.

## What is not here

No flash identity, no HyperRAM presence, no power payload, and no registers for
them -- the shipping link does not implement those commands at all. The SoC reads
all of it better: `power` over I2C from the PAC1954, `board` for the memories,
`info` and `selftest` for the image. A design that answered a POWER query with
zeros would look like a working measurement of nothing, which is worse than a
command that is not there. `gateware/probes/sideband/sideband_gateware.py` is the test
bitstream and carries the full set.
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
CTRL_ADV    = 5
CTRL_OWN    = 7


class SidebandControl(wiring.Component):
    """Four bytes of CSR: what the link reports, and a byte each way.

    Attributes
    ----------
    bus : csr.Interface(addr_width=2, data_width=8)
        ctrl, tx, rx, rxcnt.
    fabric_state, fabric_events, fabric_error, fabric_reconfigured : in
        What the design reports when the CPU has not taken the link.
    state, events, error, reconfigured : out
        What to feed to `SidebandDebug`.
    message : Signal(8), out
        The `tx` register, straight through. What a PING returns.
    received : Signal(7), in
    received_strobe : Signal(), in
        A byte from Apollo, valid for the one cycle the strobe is high. Latched
        into `rx`, counted in `rxcnt`.
    advertise : Signal(), out
        Ask Apollo for the CONTROL port. Straight from bit 5, outside `own`,
        because it is an action rather than a reported value.
    own : Signal(), out
        Which of the two is being reported. Brought out so a design can show it
        somewhere -- an operator looking at a board wants to know whether they
        are reading the fabric or the firmware.
    """

    def __init__(self):
        self._ctrl = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                  access="rw")
        self._tx = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                access="rw")
        # R, not RW: the link writes these, and a CPU write would be a value the
        # next byte from Apollo silently discards.
        self._rx = csr.Register({"data": csr.Field(csr.action.R, 8)},
                                access="r")
        self._rxcnt = csr.Register({"data": csr.Field(csr.action.R, 8)},
                                   access="r")

        # addr_width=2 -- exactly four registers, so the window is four bytes and
        # nothing can alias a fifth address onto it.
        builder = csr.Builder(addr_width=2, data_width=8)
        builder.add("ctrl", self._ctrl)
        builder.add("tx", self._tx)
        builder.add("rx", self._rx)
        builder.add("rxcnt", self._rxcnt)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":                 In(csr.Signature(addr_width=2, data_width=8)),

            "fabric_state":        In(2),
            "fabric_events":       In(1),
            "fabric_error":        In(1),
            "fabric_reconfigured": In(1),
            "received":            In(7),
            "received_strobe":     In(1),

            "state":               Out(2),
            "events":              Out(1),
            "error":               Out(1),
            "reconfigured":        Out(1),
            "message":             Out(8),
            "advertise":           Out(1),
            "own":                 Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        ctrl = self._ctrl.f.data.data
        own  = ctrl[CTRL_OWN]

        m.d.comb += [
            self.own.eq(own),
            # Not gated on `own`: the fabric has no advertisement to be overridden.
            self.advertise.eq(ctrl[CTRL_ADV]),
            # Nor is the outgoing byte: there is no fabric value it could shadow.
            # A design whose firmware never runs sends zero, which is what it has
            # to say.
            self.message.eq(self._tx.f.data.data),
        ]

        # The receive side. Latched, and counted separately so the CPU can tell a
        # repeat from a silence without a read that clears anything.
        rx_data  = Signal(8)
        rx_count = Signal(8)
        with m.If(self.received_strobe):
            m.d.sync += [rx_data.eq(self.received), rx_count.eq(rx_count + 1)]
        m.d.comb += [
            self._rx.f.data.r_data.eq(rx_data),
            self._rxcnt.f.data.r_data.eq(rx_count),
        ]

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
