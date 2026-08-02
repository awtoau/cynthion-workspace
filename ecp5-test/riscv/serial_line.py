#!/usr/bin/env python3
#
# An asynchronous serial line on a real pin, with the three things a real pin needs.
# SPDX-License-Identifier: BSD-3-Clause

"""
`amaranth_stdio.serial.AsyncSerial` wired to a real pad, with the four things a
real pad needs.

`uart16550.py` is a byte pipe with no bits on a wire. This is the other half for
the one port that does have them: the Apollo-facing console on R14/T14. It
converts between the 16550's `amaranth.lib.stream` ports and AsyncSerial's
rdy/ack handshake.

R14/T14 are the same nets as JTAG TDI/TMS, and nothing on the board tells the
FPGA a JTAG session is running. Each property below exists because of that or
because AsyncSerial does not supply it (issue #113).

## The four properties

**1. A synchroniser on the receive pin.** Two flops, `init=1` so a design
leaving reset believes the line is idle rather than mid-break.

  * `AsyncSerialRX` only instantiates an `FFSynchronizer` when handed a `pins`
    object; with `pins=None` it uses `self.i` raw
    (`amaranth_stdio/serial.py:186`). R14 is asynchronous to `sync` -- the
    SAMD11 has its own oscillator -- so every unsynchronised sample is a setup
    violation, and metastability presents as characters that are mostly right
    and wrong more often when the line is busy.

**2. Frames with a bad stop bit are dropped, not delivered.** Noise on the line
otherwise arrives at the shell as a character.

**3. An idle qualifier.** The receiver is disarmed out of reset and after every
framing error; while disarmed its input is forced to a constant mark, so the FSM
sits in IDLE and no frame can complete. It arms after the pad has been
continuously high for `idle_bits` bit periods.

  * **`idle_bits` must exceed the 10-bit frame, hence 12, and the constructor
    refuses less.** A frame in flight when the qualifier disarms finishes
    against the forced mark and produces a `rdy` pulse of garbage; requiring
    more than a frame of quiet guarantees that pulse is gone before re-arming.
  * Against JTAG this is decisive: a programming run never leaves TDI quiet for
    12 bit periods (104 us at 115200), so the receiver stays disarmed for the
    session and re-arms once, on the idle mark the SAMD11 restores.
  * **The trade:** one corrupted character mid-burst costs the rest of the
    burst, because a back-to-back sender never gives 12 idle bits to re-arm on.
    That is standard resynchronise-after-framing-error, and a human at a
    console leaves milliseconds between keystrokes.

**4. The transmitter drives its own stop bit.** `oe` is held for the whole
frame, counted from the cycle the byte is accepted, and released after the stop
bit.

  * Driving `oe` from `~phy.tx.rdy` (what `luna_soc`'s UARTProvider does) does
    not work: `rdy` is combinational on the transmitter's IDLE state and the FSM
    enters IDLE on the cycle it shifts the stop bit out. Measured at divisor 8 --
    data bits end at cycle 79, `o` and `rdy` both rise at 80, the stop bit
    occupies 80..87, driven for none of it.
  * An ECP5 pull-up is tens of kilohms and every ASCII character has bit 7 low,
    so the line RC-charges from a hard 0 and may not reach a valid mark before
    the SAMD11 samples mid-bit.
  * Releasing when idle is still the policy, and still the only arbitration on
    the FPGA side of these pins -- see `APOLLO_UART_BASE` in
    `vexii_hello_soc.py`.

## What this cannot fix

Nothing here stops the FPGA transmitting into a live JTAG session, because
nothing here can know there is one. That is firmware policy (never transmit
unbidden on this port); making it more than policy needs an Apollo-side change,
since the SAMD11 is the only device that knows which function owns PA11/PA14.
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
    frame_error : Signal(), out
        One cycle per frame dropped for a bad stop bit -- the same event the
        counter above counts. Wire to a 16550's `frame_error`, which turns it
        into LSR.FE.
    overrun : Signal(), out
        One cycle per byte this module destroyed because nothing was ready for
        it. `source.valid` is a one-cycle pulse whatever `source.ready` says, so
        a byte offered to a full sink is GONE -- this line is the only record
        that it existed. Wire to a 16550's `overrun`, which turns it into LSR.OE.
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
            "frame_error":  Out(1),
            "overrun":      Out(1),
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
        # silently. Holding it high means
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

            # What this module loses, reported rather than inferred.
            #
            # `valid` is one cycle and does not wait for `ready`, so a byte
            # offered while the sink is full is destroyed here -- which is
            # exactly what "receiver overrun" means on a line with no flow
            # control, and it is otherwise a byte that nothing anywhere ever
            # mentions. The 16550 downstream cannot see it: its own `sink`
            # backpressures, so a full FIFO there is a stall and not a loss.
            self.overrun.eq(self.source.valid & ~self.source.ready),
            self.frame_error.eq(armed & phy.rx.rdy & phy.rx.err.frame),
        ]

        # A framing error disarms. See the module docstring for what this costs.
        with m.If(self.frame_error):
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
