#!/usr/bin/env python3
#
# An asynchronous serial line on a real pin, with the three things a real pin needs.
# SPDX-License-Identifier: BSD-3-Clause

"""
`amaranth_stdio.serial.AsyncSerial`, wired to a pad the way a pad has to be wired.

`uart16550.py` is a byte pipe with no bits on a wire. This is the other half for
the one port that does have bits on a wire: the Apollo-facing console on R14/T14.
It converts between the 16550's `amaranth.lib.stream` ports and AsyncSerial's
older rdy/ack handshake, and it owns the three properties that the pin needs and
that AsyncSerial alone does not supply.

## Why this module exists: issue #113

The Apollo console corrupted and truncated characters, worst immediately after
`apollo configure`, and reliably answered `unknown command` to the first line
typed after the tty was opened. The interrupt path was the obvious suspect and
was exonerated by measurement -- `scripts/soc_apollo_probe.py` gave 9/10
mismatches with the shell POLLING and 10/10 with it interrupt-driven, same
bitstream. The transport was at fault in both.

It was at fault in three separate ways, all of them in how the pad was wired
rather than in AsyncSerial:

**1. The receive pin went into the receiver's FSM unsynchronised.** The SoC
did `phy.rx.i.eq(pins.rx.i)`, straight from the pad. `AsyncSerialRX` only
instantiates an `FFSynchronizer` when it is handed a `pins` object; construct it
with `pins=None`, as every design here does, and `self.i` is used raw
(`amaranth_stdio/serial.py:186`). R14 is asynchronous to `sync` by definition --
the SAMD11 has its own oscillator -- so every start-bit edge and every data-bit
sample was a setup violation waiting to resolve whichever way it liked. The
symptom of a metastable sample is exactly what was measured: characters that are
mostly right, occasionally wrong, more often wrong when the line is busy.
`sideband_debug.py` already synchronises its receive pin and says why; this path
did not.

**2. Framing errors were delivered as data.** The SoC pushed every completed
frame into the FIFO (`rx_buf.sink.valid.eq(phy.rx.rdy)`) and never looked at
`phy.rx.err.frame`. So a frame whose stop bit was low -- which is what noise on
the line produces -- arrived at the shell as a character.

**3. Nothing waited for the line to be idle before believing it.** R14 and T14
are the same nets as JTAG TDI and TMS. Nothing tells the FPGA that a JTAG
session is in progress and there is no signal on the board that could, so during
`apollo configure` the receiver saw megahertz-rate JTAG edges, framed them into
whatever ten-bit windows the divisor happened to slice them into, and delivered
the results. The same mechanism, one character long, is the lost first command:
the SAMD11 leaves PA14 as `GPIO_PIN_FUNCTION_OFF` while JTAG owns the pins and
only muxes it back to SERCOM2 when the host opens the CDC interface
(`repos/apollo/firmware/src/boards/cynthion_d11/uart.c:37`, reached from
`jtag.c:38`). The moment the pinmux lands, the pad goes from undriven to a
driven mark, and a receiver that is armed reads the transition as a start bit.

## What this module does about each

**A synchroniser on the receive pin.** Two flops, `init=1` so a design coming
out of reset believes the line is idle rather than mid-break.

**Frames with a bad stop bit are dropped, not delivered.**

**An idle qualifier.** The receiver is *disarmed* out of reset and after every
framing error. While disarmed its input is forced to a constant mark, so the
FSM sits in IDLE and no frame can complete. It arms only after the pad has been
continuously high for `idle_bits` bit periods.

`idle_bits` defaults to 12, which is longer than the 10-bit frame it is
protecting -- that is the point, and it is a correctness requirement rather than
margin. A frame in flight when the qualifier disarms is finished off against the
forced mark and produces a `rdy` pulse with garbage in it; requiring more than a
frame's worth of quiet guarantees that pulse has come and gone (and been
suppressed, since `armed` is low) before the receiver is armed again. Setting
`idle_bits` below 10 would let that garbage frame through on re-arming, which is
the failure this exists to prevent, so the constructor refuses.

Against JTAG the qualifier is decisive: a programming run never leaves TDI
quiet for 12 bit periods (104 us at 115200), so the receiver stays disarmed for
the whole session and re-arms once, on the idle mark the SAMD11 restores.

The cost is a trade and it is worth naming: a single corrupted character in the
middle of a burst now costs the *rest of the burst*, because the framing error
disarms the receiver and a back-to-back sender never gives it 12 idle bits to
re-arm on. That is the standard resynchronise-after-framing-error behaviour and
it is the right side of the trade here -- a human typing at a console leaves
milliseconds between keystrokes, and a burst that contains a framing error was
not going to be delivered intact anyway.

**The transmitter drives its own stop bit.** The SoC drove the pad's output
enable from `~phy.tx.rdy`, which is what `luna_soc`'s UARTProvider does. `rdy`
is combinational on the transmitter's IDLE state, and the FSM enters IDLE on the
same cycle it shifts the stop bit out -- so `oe` fell at the *start* of the stop
bit and the last bit of every character was left to the pad's pull-up. Measured
directly, divisor 8: data bits end at cycle 79, `o` goes high at 80, `rdy` goes
high at 80, and the stop bit occupies 80..87. It was driven for none of it.

An ECP5 internal pull-up is tens of kilohms, and every ASCII character has bit 7
low, so the line was released from a hard 0 and had to RC-charge to a valid mark
before the SAMD11's oversampler took its sample at the middle of the bit. It has
enough time on a good day. `oe` is now held for the whole frame instead, counted
from the cycle the byte is accepted, and released after the stop bit rather than
before it.

Releasing when idle is still the policy, and it is still the only arbitration
that exists on the FPGA side of these pins -- see the comment on
`APOLLO_UART_BASE` in `vexii_hello_soc.py`. Holding the line during a
transmission is not a change to that policy; it is what the policy meant.

## What this module cannot fix

Nothing here stops the FPGA transmitting into a live JTAG session, because
nothing here can know there is one. That remains a firmware policy (never
transmit unbidden on this port) and, if it is ever to be more than a policy, a
change on the Apollo side -- the SAMD11 is the only device on the board that
knows which function owns PA11/PA14.
"""

from amaranth               import Module, Signal, C
from amaranth.lib           import wiring, stream
from amaranth.lib.cdc       import FFSynchronizer
from amaranth.lib.wiring    import In, Out

from amaranth_stdio.serial  import AsyncSerial


__all__ = ["SerialLine"]


# A frame is start + data + stop, with no parity. Used for the transmit hold
# counter and as the floor for `idle_bits`.
#
# AsyncSerialTX also spends one bit period holding the idle mark between
# accepting a byte and emitting its start bit, which is why the transmit hold is
# one period longer than the frame -- see FRAME_BITS_TX below.
def _frame_bits(data_bits):
    return 1 + data_bits + 1


class SerialLine(wiring.Component):
    """AsyncSerial on a pad, synchronised, qualified, and driving its stop bit.

    Parameters
    ----------
    divisor : int
        `sync` cycles per bit. Compute it as `clock // baud` rather than writing
        a number: the error then scales with the clock instead of silently
        becoming a dead link when someone changes the frequency.
    data_bits : int
        Bits per character. 8, unless something on the other end says otherwise.
    idle_bits : int
        Bit periods of continuous mark required before the receiver will believe
        a start bit. Must be greater than the frame length; see the module
        docstring for why that is correctness rather than taste.

    Attributes
    ----------
    rx_i : Signal(), in
        The receive pad, straight from `platform.request(...).rx.i`. Crossing
        into `sync` is this module's job, not the caller's.
    tx_o : Signal(), out
        The transmit pad value.
    tx_oe : Signal(), out
        The transmit pad's output enable. High for the whole of a character,
        including its stop bit, and low otherwise so the pull-up holds the mark
        and the pin is free for whatever else shares it.
    source : stream.Signature(8), out
        Bytes received, framing errors already dropped. Feed to a 16550's `sink`.
    sink : stream.Signature(8), in
        Bytes to transmit. Feed from a 16550's `source`.
    armed : Signal(), out
        Diagnostic: the receiver currently believes the line. Low out of reset
        and after a framing error, until `idle_bits` of quiet.
    frame_errors : Signal(8), out
        Diagnostic: saturating count of frames dropped for a bad stop bit. It
        saturates rather than wrapping, because "255" answers the question this
        counter is asked ("is this line noisy") and a wrapped 3 does not.
    """

    def __init__(self, *, divisor, data_bits=8, idle_bits=12):
        frame_bits = _frame_bits(data_bits)
        if idle_bits <= frame_bits:
            raise ValueError(
                f"idle_bits must exceed the {frame_bits}-bit frame so a frame "
                f"cut short by disarming cannot survive into the next arming, "
                f"not {idle_bits!r}")
        if divisor < 5:
            # AsyncSerialRX's own floor: below 5 its half-bit offset cannot keep
            # the sampler in the middle of a bit.
            raise ValueError(f"divisor must be at least 5, not {divisor!r}")

        self._divisor    = divisor
        self._data_bits  = data_bits
        self._idle_bits  = idle_bits

        super().__init__({
            "rx_i":         In(1),
            "tx_o":         Out(1),
            "tx_oe":        Out(1),
            "source":       Out(stream.Signature(data_bits)),
            "sink":         In(stream.Signature(data_bits)),
            "armed":        Out(1),
            "frame_errors": Out(8),
        })

    @property
    def divisor(self):
        return self._divisor

    def elaborate(self, platform):
        m = Module()

        phy = AsyncSerial(divisor=self._divisor, data_bits=self._data_bits,
                          parity="none")
        m.submodules.phy = phy

        # ---- the receive pad ------------------------------------------------
        #
        # Two flops, and `init=1` because a mark is what an idle line looks like.
        # Coming out of reset with these at 0 would present a start bit to a
        # receiver that had no reason to doubt it.
        rx_pad = Signal(init=1)
        m.submodules.rx_cdc = FFSynchronizer(self.rx_i, rx_pad, init=1)

        # ---- the idle qualifier ---------------------------------------------
        #
        # Count consecutive marks. `armed` is set when the count reaches a full
        # `idle_bits` periods and cleared by a framing error; the counter resets
        # on any space, armed or not.
        idle_cycles = self._idle_bits * self._divisor
        idle_count  = Signal(range(idle_cycles + 1))
        armed       = Signal()
        m.d.comb += self.armed.eq(armed)

        with m.If(~rx_pad):
            m.d.sync += idle_count.eq(0)
        with m.Elif(idle_count != idle_cycles):
            m.d.sync += idle_count.eq(idle_count + 1)

        # While disarmed the receiver is fed a constant mark rather than the pad,
        # so its FSM cannot leave IDLE and no frame can complete. This is what
        # makes the qualifier a gate on the *line* rather than a filter on the
        # bytes -- a filter would still be resynchronising its bit phase to
        # whatever noise arrived, and would still be wrong when the noise stopped.
        m.d.comb += phy.rx.i.eq(rx_pad | ~armed)

        # ALWAYS ACCEPT FROM THE PHY. `AsyncSerialRX` leaves DONE after exactly
        # one cycle whatever `ack` does; `ack` only decides whether the character
        # is captured, and a low `ack` discards it and sets `err.overflow`. So
        # wiring `ack` to downstream readiness does not backpressure -- it drops
        # silently, which is what the SoC used to do. Holding it high means
        # `rdy`, `data` and `err.frame` all become valid on the same cycle, which
        # is what the gating below needs.
        m.d.comb += phy.rx.ack.eq(1)

        # ---- what reaches the stream ----------------------------------------
        #
        # A character is delivered only if the receiver was armed and the stop
        # bit was a mark. Everything else is a frame that noise produced.
        good = Signal()
        m.d.comb += [
            good.eq(armed & phy.rx.rdy & ~phy.rx.err.frame),
            self.source.payload.eq(phy.rx.data),
            self.source.valid.eq(good),
        ]

        # A framing error disarms. See the module docstring for what this costs.
        with m.If(armed & phy.rx.rdy & phy.rx.err.frame):
            m.d.sync += armed.eq(0)
            with m.If(self.frame_errors != 0xff):
                m.d.sync += self.frame_errors.eq(self.frame_errors + 1)
        with m.Elif(idle_count == idle_cycles):
            m.d.sync += armed.eq(1)

        # ---- the transmit pad -----------------------------------------------
        #
        # AsyncSerialTX's `ack` is the PRODUCER saying "take this", the reverse
        # of what a stream's `ready` means, and `rdy` is the consumer's ready.
        m.d.comb += [
            phy.tx.data.eq(self.sink.payload),
            phy.tx.ack.eq(self.sink.valid),
            self.sink.ready.eq(phy.tx.rdy),
            self.tx_o.eq(phy.tx.o),
        ]

        # Drive the pad for a whole character, counted rather than inferred.
        #
        # `~phy.tx.rdy` -- which is what this design and luna_soc both used --
        # goes false at the START of the stop bit, because the FSM reaches IDLE
        # on the same cycle it shifts the stop bit onto the wire. Counting
        # instead makes the released edge fall after the frame rather than
        # inside it.
        #
        # The period is one bit longer than the frame because AsyncSerialTX
        # holds the mark for a full bit period between accepting the byte and
        # emitting the start bit. Verified against the transmitter directly in
        # scripts/soc_serial_sim.py, which fails if this constant drifts from
        # what the PHY actually does.
        frame_cycles = (1 + _frame_bits(self._data_bits)) * self._divisor
        tx_hold = Signal(range(frame_cycles + 1))

        # The reload wins over the decrement, so back-to-back characters keep
        # `oe` continuously asserted instead of blinking between them.
        with m.If(phy.tx.rdy & phy.tx.ack):
            m.d.sync += tx_hold.eq(frame_cycles)
        with m.Elif(tx_hold != 0):
            m.d.sync += tx_hold.eq(tx_hold - 1)

        m.d.comb += self.tx_oe.eq(tx_hold != 0)

        return m
