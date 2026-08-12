#!/usr/bin/env python3
#
# The FPGA_ADV sideband link, as a drop-in block for a shipping design.
# SPDX-License-Identifier: BSD-3-Clause

"""
Apollo's single-wire link on pin T6, wired to the pad, ready to drop in.

Three things, and no more:

  * **liveness** -- `SidebandLink`, answering PING and STATUS with a heartbeat
    that toggles per reply
  * **a byte each way** -- PING carries one out, a `0x80`-`0xFF` opcode carries
    seven bits in; the CPU's end is `gateware/soc/peripherals/sideband_csr.py`
  * **the CONTROL port request** -- `SidebandAdvertiser`, the frame Apollo
    matches to hand the port over

It answers when the CPU is wedged, when USB has not enumerated and when JTAG is
occupied, which is when a debug channel earns its area.

## What is not here, and where it went

The hardware readout -- flash identity, HyperRAM presence, the PAC1954 power
payload -- is **not implemented**, not stubbed. Those opcodes are gone from the
shipping link, so a query for them gets the unknown-command reply rather than a
well-formed frame full of zeros. Zeros would read as a working measurement of
nothing, and every reader of the protocol would have to know which fields were
real.

`gateware/probes/sideband/sideband_gateware.py` is the test bitstream. It uses
`apollo_fpga.gateware.sideband.SidebandResponder`, sources every field, and is
the right place to ask a board that will not boot far enough to have a console.

## The clock

The bit period is a cycle count fixed at build time, so it is only correct in a
domain that really runs at `clk_freq_hz`. A design that clocks `sync` faster and
leaves the default alone gets a **dead** link, not a slow one -- at 120 MHz the
effective baud doubles and a UART tolerates about +/-2%.

Both submodules take that frequency from one argument, so they cannot disagree
about the bit period and a frame at the wrong rate is not an advertisement.

## Use

    from sideband_debug import SidebandDebug

    m.submodules.sideband = sideband = SidebandDebug(clk_freq_hz=SYNC_MHZ * 1e6)
    m.d.comb += [
        sideband.state.eq(my_state),        # 2 bits, whatever is diagnostic
        sideband.events.eq(my_event_flag),
        sideband.error.eq(my_error_flag),
        sideband.message.eq(my_byte),
        sideband.advertise.eq(want_control_port),
    ]
"""

import sys
from pathlib import Path

from amaranth               import Elaboratable, Module, Mux, Signal
from amaranth.lib.cdc       import FFSynchronizer

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sideband_advertise import SidebandAdvertiser
from sideband_link      import SidebandLink


class SidebandDebug(Elaboratable):
    """ The FPGA_ADV link and the port request, wired to the pin.

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
    message : Signal(8), input
        The byte a PING returns.
    received : Signal(7), output
    received_strobe : Signal(), output
        A byte Apollo sent, and one cycle of notice.
    advertise : Signal(), input
        Ask Apollo for the CONTROL port. Off unless something drives it, because
        a bitstream that seized CONTROL on configuration would take the port from
        the debug interface used to recover the board.
    """

    # 230400 by default, matching ADV_UART_BAUD in the Apollo firmware.
    #
    # The two numbers must change together; no build step sees both. A mismatch
    # is a DEAD link, not a slow one -- 2x against a UART's ~2% tolerance, with
    # no error anywhere, because neither end ever frames a byte.
    def __init__(self, *, clk_freq_hz=60e6, baud=230400, domain="sync"):
        self.clk_freq_hz = clk_freq_hz
        self.baud = baud
        self.domain = domain

        self.state = Signal(2)
        self.events = Signal()
        self.error = Signal()
        self.reconfigured = Signal()
        self.message = Signal(8)
        self.received = Signal(7)
        self.received_strobe = Signal()
        self.advertise = Signal()

    def elaborate(self, platform):
        m = Module()

        link = SidebandLink(clk_freq_hz=self.clk_freq_hz, baud=self.baud)
        m.submodules.link = link

        advertiser = SidebandAdvertiser(clk_freq_hz=self.clk_freq_hz,
                                        baud=self.baud)
        m.submodules.advertiser = advertiser

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
            # Idle-high while the advertiser transmits, for the same reason the
            # link does it for its own replies: the pad reads back what it
            # drives, so otherwise the link frames the advertisement as incoming
            # commands and answers each byte of it.
            link.rx.eq(Mux(advertiser.tx_active, 1, rx_sync)),

            advertiser.rx.eq(rx_sync),
            advertiser.enable.eq(self.advertise),
            # The link wins the wire. It only ever transmits when asked, so
            # deferring to it costs the advertisement one interval at most, where
            # the reverse would corrupt a reply Apollo is already waiting for.
            advertiser.hold.eq(link.tx_active),

            # Both are open-drain, so `o` is never 1 and the two enables simply
            # OR: two pull-downs on one wire is a pull-down. That is what makes
            # sharing the pad free rather than an arbitration problem.
            #
            # Not `tx`/`tx_active`: wiring oe = tx_active gives push-pull, which
            # can short against Apollo's driver on any timing slip -- and does,
            # for the 0.75 bit times tx_active leads the first bit onto the wire.
            pin.o.eq(link.pad_o | advertiser.pad_o),
            pin.oe.eq(link.pad_oe | advertiser.pad_oe),

            link.state.eq(self.state),
            link.events.eq(self.events),
            link.error.eq(self.error),
            link.reconfigured.eq(self.reconfigured),
            link.message.eq(self.message),

            self.received.eq(link.received),
            self.received_strobe.eq(link.received_strobe),
        ]

        return m
