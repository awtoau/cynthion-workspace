#!/usr/bin/env python3
#
# The FPGA_ADV port-request advertisement, as the sideband link's fifth frame.
# SPDX-License-Identifier: BSD-3-Clause

"""
Asks Apollo for the CONTROL port, on the wire the sideband already owns.

FPGA_ADV's primary purpose upstream is port takeover, not debug. `ApolloAdvertiser`
drives it as a 50 Hz square wave, and Apollo keeps the port switch on the FPGA only
while that continues. Every bitstream here is AUX-only, so the pin has been free to
carry the sideband instead -- and the SoC consequently has no way to ask for CONTROL.

A square wave and a UART cannot share one wire: the square wave is low for half of
every 20 ms and no byte survives that. The two are reconciled at the other end
instead. Apollo's `FPGA_ADV_MODE_UART` already defines the advertisement as a
**framed 4-byte pattern on the same UART**, and the receive handler already routes
bytes to the pattern matcher only when no command is in flight
(`repos/apollo/firmware/src/boards/cynthion_d11/fpga_adv.c`). Nothing in gateware
ever emitted it. This module does.

| | EIC mode | **UART mode** |
|---|---|---|
| Advertisement | >2 rising edges per 200 ms | the frame `C1 14 01 A5` per 300 ms |
| Sideband alongside | no -- command traffic counts as edges | yes -- responses bypass the matcher |

EIC mode is why the two cannot simply coexist as-is: it counts edges, and sideband
traffic is edges, so it would read a poll as a port request. UART mode is the only
mode in which one wire can do both, and the pattern is the whole of its contract.

## What this costs the collision-free property

The link's rule was "the FPGA never transmits unasked", which made it collision-free
by construction. An advertisement breaks that rule -- deliberately, and at a bounded
price:

  * A frame occupies 174 us out of every 100 ms: **0.17% duty**.
  * Both ends are open-drain, so an overlap is two pull-downs, not a short.
  * An overlapped command or reply fails Apollo's CRC and is retried; an overlapped
    advertisement is one of three inside the 300 ms window, so the port is not lost.

`hold` and the idle guard remove the far larger risk -- starting a frame *inside* a
transaction the FPGA already knows about.

## Off at reset, and the CPU turns it on

Upstream advertises by default and `ApolloAdvertiserRequestHandler` provides `stop`.
The polarity is inverted here: `enable` resets low. A bitstream that seized CONTROL
on configuration would take the port from Apollo's own debug interface, which is the
path used to recover the board. Asking is a decision firmware makes; see
`gateware/soc/peripherals/sideband_csr.py` bit 5.
"""

from amaranth import Cat, Const, Elaboratable, Module, Mux, Signal


__all__ = ["SidebandAdvertiser", "PATTERN"]


# HEARTBEAT_PATTERN in Apollo's fpga_adv.c. Duplicated rather than shared because one
# side is C on a Cortex-M0+ and the other is Python; there is no build step that sees
# both. A mismatch is silent -- Apollo simply never grants the port.
PATTERN = (0xC1, 0x14, 0x01, 0xA5)

# HEARTBEAT_TIMEOUT_MS in the same file.
APOLLO_TIMEOUT_MS = 300


class SidebandAdvertiser(Elaboratable):
    """ Emits the port-request frame on FPGA_ADV, between sideband transactions.

    Attributes
    ----------
    enable : Signal(), in
        Ask for the CONTROL port. Resets low; the frame stops when this clears and
        Apollo hands the port back one timeout later.
    hold : Signal(), in
        The responder is transmitting. No frame starts, and none is truncated.
    rx : Signal(), in
        The line level, synchronised. Low means the other end is driving.
    tx_active : Signal(), out
        High for the whole frame. Wire to the responder's receiver gate.
    pad_o, pad_oe : Signal(), out
        Open-drain, matching `SidebandResponder`: `pad_o` is always 0 and `pad_oe`
        tracks the bit, so the pad is simply OR-ed with the responder's.
    """

    def __init__(self, *, clk_freq_hz=60e6, baud=230400, interval_ms=100,
                 guard_bits=20):
        # The bit period is a cycle count fixed at build time, exactly as in
        # SidebandResponder -- a design that raises `sync` and leaves this alone
        # gets a dead advertisement, not a slow one, and silently loses the port.
        # Pass the domain's real frequency; the check below only catches the
        # direction that cannot work at all.
        self.clk_freq_hz = clk_freq_hz
        self.baud = baud
        self.divisor = int(clk_freq_hz // baud)

        if self.divisor < 8:
            raise ValueError(
                f"clock {clk_freq_hz/1e6:.1f} MHz is too slow for {baud} baud: "
                f"divisor {self.divisor} leaves no room to sample mid-bit")

        # Three frames per Apollo timeout, so two consecutive losses -- to a
        # collision, a bit error, or a reply that overran -- still hold the port.
        # One per timeout would surrender it on the first.
        assert interval_ms * 3 <= APOLLO_TIMEOUT_MS, (
            f"{interval_ms} ms between frames leaves fewer than three inside "
            f"Apollo's {APOLLO_TIMEOUT_MS} ms HEARTBEAT_TIMEOUT_MS")
        self.interval_cycles = max(int(clk_freq_hz * interval_ms / 1000), 1)

        # Continuous idle-high required before a frame may start, in bit periods.
        # The longest gap *inside* a transaction is the 40 us turnaround -- 9.2 bit
        # periods at 230400 -- so 20 proves no transaction is in flight rather than
        # merely that the wire is quiet this instant.
        self.guard_bits = guard_bits

        self.enable    = Signal()
        self.hold      = Signal()
        self.rx        = Signal(init=1)

        self.tx_active = Signal()
        self.pad_o     = Signal()
        self.pad_oe    = Signal()

    @property
    def frame(self):
        """The four bytes as one 40-bit shift value, LSB first, 8-N-1."""
        value = 0
        for index, byte in enumerate(PATTERN):
            # start(0) | data | stop(1)
            value |= ((byte << 1) | (1 << 9)) << (10 * index)
        return value

    def elaborate(self, platform):
        m = Module()

        bits = 10 * len(PATTERN)

        # One bit-rate tick, shared by the shifter and the idle guard, so the two
        # cannot disagree about how long a bit is.
        bit_timer = Signal(range(self.divisor))
        tick      = Signal()
        m.d.comb += tick.eq(bit_timer == 0)
        m.d.sync += bit_timer.eq(Mux(tick, self.divisor - 1, bit_timer - 1))

        # Idle guard. Cleared by anything that says the wire is in use, so it
        # measures *continuous* quiet rather than accumulating quiet moments.
        guard     = Signal(range(self.guard_bits + 1))
        line_busy = Signal()
        m.d.comb += line_busy.eq(~self.rx | self.hold)
        with m.If(line_busy):
            m.d.sync += guard.eq(0)
        with m.Elif(tick & (guard != self.guard_bits)):
            m.d.sync += guard.eq(guard + 1)

        # Saturating, and reset at each frame START, so the period is start to
        # start and a long frame does not stretch it.
        interval = Signal(range(self.interval_cycles + 1))
        with m.If(interval != self.interval_cycles):
            m.d.sync += interval.eq(interval + 1)

        # All-ones is idle, as a UART line at rest must be.
        shifter = Signal(bits, init=-1)
        left    = Signal(range(bits + 1))

        # Open-drain, and the only reason this can share the pad with the
        # responder by OR-ing: `o` is never 1, so two drivers are two pull-downs.
        m.d.comb += [
            self.pad_o.eq(0),
            self.pad_oe.eq(self.tx_active & ~shifter[0]),
        ]

        with m.FSM():

            with m.State("IDLE"):
                # Start on a tick, so the first bit gets a full bit period rather
                # than whatever remained of the one in progress.
                with m.If(self.enable & tick
                          & (interval == self.interval_cycles)
                          & (guard == self.guard_bits)):
                    m.d.sync += [
                        shifter.eq(Const(self.frame, bits)),
                        left.eq(bits),
                        interval.eq(0),
                    ]
                    m.next = "SEND"

            with m.State("SEND"):
                m.d.comb += self.tx_active.eq(1)
                with m.If(tick):
                    m.d.sync += [
                        shifter.eq(Cat(shifter[1:], Const(1, 1))),
                        left.eq(left - 1),
                    ]
                    # Leave only once the final stop bit has had its full period;
                    # releasing earlier truncates it into the idle line.
                    with m.If(left == 1):
                        m.next = "IDLE"

        return m
