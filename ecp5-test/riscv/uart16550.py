#!/usr/bin/env python3
#
# A standard NS16550A register map in front of a byte stream.
# SPDX-License-Identifier: BSD-3-Clause

"""
The ordinary 16550 UART, as a CSR peripheral. Its value is being unremarkable.

## Why this replaces the bespoke console peripheral

The peripheral this supersedes packed four byte registers into one 32-bit word:

    +0  data      W   write a byte
    +1  ready     R   room to write
    +2  rx_data   R   READING THIS POPPED THE RX FIFO
    +3  rx_valid  R   a byte is waiting

Firmware polls `rx_valid` in a tight loop, and a side-effecting register one byte
away from the polled one is a trap laid for every layer between the CPU and the
FIFO. Anything that widens, prefetches, speculates, replays or retries a read --
a cache line fill, a bus bridge that sweeps all four byte lanes, a debugger
peeking at memory -- consumes a received byte that no software ever asked for,
and there is nothing in the firmware to see. On this board a build that never
called `Console::get()` printed normally while one that polled it went silent.

**The rule this file enforces: a read must never change state, and status must
never share a 32-bit word with a side-effecting register.** The same class of bug
cost a day on `luna_soc`'s SPIController, where reading `data` pops its RX FIFO.

Here RBR is at +0 and LSR is at +5. They are in *different 32-bit words*, so the
poll loop and the pop can no longer be aliased by anything, whatever the bus
fabric does with byte enables. That is the structural fix; the standard register
map just happens to be laid out correctly for it, which is not a coincidence --
16550s have lived behind widening bridges since 1987.

## Strictly standard, and deliberately dull

    +0  RBR (R) / THR (W)  receive buffer / transmit holding   (DLL when LCR.DLAB)
    +1  IER                interrupt enable                    (DLM when LCR.DLAB)
    +2  IIR (R) / FCR (W)  interrupt id / FIFO control
    +3  LCR                line control (bit 7 is DLAB)
    +4  MCR                modem control
    +5  LSR (R)            line status: bit 0 DR, bit 5 THRE, bit 6 TEMT
    +6  MSR (R)            modem status
    +7  SCR                scratch

Fixed 16-byte FIFOs. No depth register, no 16650/16750 extensions, no enhanced
mode -- adding those would buy a driver nobody has and cost the one property
worth having, which is that this is byte-identical to the NS16550A QEMU's
`-M virt` presents. The firmware that drives the board and the firmware
`scripts/soc_test.py` drives under QEMU are then the same code, so a green test
says something about the hardware instead of about a second implementation of
the same shell.

## There is no UART in this UART

No baud rate generator, no start bits, no shift register, no line at all. This
is a byte pipe onto an `amaranth.lib.stream` source/sink pair; what sits behind
it is the instantiator's choice (USB CDC here, an FPGA pin elsewhere). The
following exist only so a generic driver's setup sequence succeeds:

  * DLL/DLM (the divisor latches) store and read back and set no rate whatsoever.
    They are here because a driver that sets DLAB, writes a divisor and clears
    DLAB again would otherwise transmit the divisor as two characters.
  * LCR's word-length, parity and stop-bit fields are stored and ignored. There
    are no bits on a wire to frame.
  * MCR is stored and ignored -- including bit 4, LOOP: no loopback is wired.
  * MSR reads a constant "modem ready" (CTS, DSR and DCD asserted, no deltas), so
    a driver that waits for CTS before transmitting does not wait forever.
  * LSR bits 1..4 (overrun, parity, framing, break) always read 0. Parity,
    framing and break cannot occur without a line. Overrun can -- a byte arriving
    with the RX FIFO full is dropped -- but a real 16550 reports it in a bit that
    a *read of LSR clears*, and a read that changes state is the entire thing
    this peripheral exists to eliminate. Silence about overrun is the cheaper
    lie: use the transport's own buffering to avoid it (see `stream_buffer.py`).

## The interrupt, and the one place this is deliberately not an NS16550A

`irq` is a level, asserted while `(IER.ERBFI and LSR.DR)` or
`(IER.ETBEI and LSR.THRE)`, and reported through IIR in the standard encoding.
It goes to `vexii_plic.py`. IER resets to zero, so a design that ignores this
output and polls LSR -- which is what everything here did until now -- is
unaffected.

**Reading IIR does not clear anything.** On a real part it clears the
transmit-empty interrupt, which would be a state-changing read at +2, in the
same 32-bit word as RBR at +0: the exact hazard this file was written to remove,
moved over by two bytes. The full argument, and what a driver must do instead,
is in the comment on the IIR block below. It is the only behavioural difference
between this peripheral and the one QEMU's `-M virt` presents, and it is
therefore the only thing `scripts/soc_test.py` cannot speak for.

## Buffering is not this module's business

The FIFOs here are 16 bytes because the NS16550A's are 16 bytes, and that is the
only reason. Deep or elastic buffering belongs between this peripheral and
whatever transport carries the bytes, sized for that transport -- see
`ecp5-test/riscv/stream_buffer.py`. The two concerns were previously in one
module and silently invalidated each other: a 1024-byte FIFO justified as "two
512-byte USB packets" outlived the change to one byte per packet
(`serial.tx.last.eq(1)`, for console latency) and went on costing a block RAM for
a reason that no longer existed.

## Instantiating more than one

Nothing here knows its own address. There is no module-level state, no fixed
base, and no name baked into the register map, so a design may instantiate as
many as it has stream endpoints to attach:

    console = Uart16550()
    apollo  = Uart16550()

and place each with its own `WishboneCSRBridge` at its own base address.
"""

from amaranth               import Module, Mux, Cat, C, Signal
from amaranth.lib           import wiring, stream
from amaranth.lib.fifo      import SyncFIFOBuffered
from amaranth.lib.wiring    import In, Out
from amaranth.hdl           import ResetInserter

from amaranth_soc           import csr


__all__ = ["Uart16550", "SplitRW"]


# The NS16550A's FIFO depth, and not a parameter.
#
# Making it adjustable would mean firmware had to discover it, which is what the
# 16650/16750 depth registers exist for and why nothing agrees about them. 16 is
# what every driver already assumes when it sees a 16550A in IIR, and 16 entries
# of 8 bits map to distributed LUT RAM on the ECP5 rather than to a DP16KD, so
# the cost of being standard here is a few LUTs.
FIFO_DEPTH = 16


class SplitRW(csr.FieldAction):
    """A field whose read side and write side are different registers.

    The 16550 defines three addresses this way -- RBR/THR, IER/DLM (by DLAB) and
    IIR/FCR -- and `amaranth_soc.csr.action` has no action for it: `R` and `W`
    are one-directional and `RW` owns its own storage, so none of them can
    express "reading here and writing here reach different hardware".

    Exposes both directions raw, so the peripheral decides what a read returns
    and what a write does. In particular `r_stb` is a *strobe*, which the RBR
    path uses to pop the receive FIFO -- the one place in this design where a
    read is allowed a side effect, because that is what a data register is for.
    Everything a driver polls is somewhere else, in another 32-bit word.
    """

    def __init__(self, shape):
        super().__init__(shape, access="rw", members={
            "r_data": In(shape),
            "r_stb":  Out(1),
            "w_data": Out(shape),
            "w_stb":  Out(1),
        })

    def elaborate(self, platform):
        m = Module()
        m.d.comb += [
            self.port.r_data.eq(self.r_data),
            self.r_stb.eq(self.port.r_stb),
            self.w_data.eq(self.port.w_data),
            self.w_stb.eq(self.port.w_stb),
        ]
        return m


class Uart16550(wiring.Component):
    """An NS16550A register map in front of a stream source and sink.

    Attributes
    ----------
    bus : csr.Interface(addr_width=3, data_width=8)
        The eight byte-wide registers. Add through a `WishboneCSRBridge`.
    source : stream.Signature(8), out
        Bytes written to THR, in order. Attach to whatever transmits.
    sink : stream.Signature(8), in
        Bytes to be delivered to RBR. Attach to whatever receives.
    irq : Signal(), out
        The interrupt request, active high and LEVEL sensitive. Attach to a
        source input of `vexii_plic.Plic`, or leave it unconnected and poll LSR
        as before -- it costs nothing when nothing reads it, and IER resets to
        zero, so this line is low until a driver asks for it.

    The bus, the FIFOs and both stream ports are all in the `sync` domain. If a
    transport runs in another domain, cross it *outside* this module -- see
    `stream_buffer.StreamBuffer`, which is what a crossing costs and where it
    belongs.
    """

    def __init__(self):
        # +0. RBR on read, THR on write -- and DLL in both directions when
        # LCR.DLAB is set, which is why this cannot be a plain W register.
        self._rbr_thr = csr.Register({"data": csr.Field(SplitRW, 8)},
                                     access="rw")
        # +1. IER, or DLM when DLAB is set. Both are storage, but which storage
        # depends on a bit in another register, so the routing is done here
        # rather than by a csr.action.RW that would own the wrong one.
        self._ier_dlm = csr.Register({"data": csr.Field(SplitRW, 8)},
                                     access="rw")
        # +2. IIR on read, FCR on write. Genuinely different registers on a real
        # part too; this is not an economy.
        self._iir_fcr = csr.Register({"data": csr.Field(SplitRW, 8)},
                                     access="rw")
        # +3. LCR. Only bit 7 (DLAB) does anything; the rest is stored so a
        # driver that writes 8N1 and reads it back is satisfied.
        self._lcr = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                 access="rw")
        # +4. MCR. Entirely stored and ignored -- there are no modem pins.
        self._mcr = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                 access="rw")
        # +5. LSR, read-only and PURELY COMBINATIONAL FROM THE FIFO FLAGS.
        #
        # This is the register firmware polls, and it is the one that must have
        # no side effect of any kind. It is also, by the standard map, in the
        # second 32-bit word -- four bytes clear of RBR at +0. Do not move it.
        self._lsr = csr.Register({"data": csr.Field(csr.action.R, 8)},
                                 access="r")
        # +6. MSR, a constant. See the module docstring.
        self._msr = csr.Register({"data": csr.Field(csr.action.R, 8)},
                                 access="r")
        # +7. SCR, eight bits of scratch. The classic "is there a UART here"
        # probe: write a value, read it back. Costs eight flops and answers a
        # question that has otherwise needed a bitstream rebuild to ask.
        self._scr = csr.Register({"data": csr.Field(csr.action.RW, 8)},
                                 access="rw")

        # addr_width=3 -- exactly the eight registers, so the window is 8 bytes
        # and the decoder cannot silently alias a ninth address onto RBR.
        builder = csr.Builder(addr_width=3, data_width=8)
        builder.add("rbr_thr", self._rbr_thr)
        builder.add("ier",     self._ier_dlm)
        builder.add("iir_fcr", self._iir_fcr)
        builder.add("lcr",     self._lcr)
        builder.add("mcr",     self._mcr)
        builder.add("lsr",     self._lsr)
        builder.add("msr",     self._msr)
        builder.add("scr",     self._scr)
        self._bridge = csr.Bridge(builder.as_memory_map())

        super().__init__({
            "bus":    In(csr.Signature(addr_width=3, data_width=8)),
            "source": Out(stream.Signature(8)),
            "sink":   In(stream.Signature(8)),
            "irq":    Out(1),
        })
        self.bus.memory_map = self._bridge.bus.memory_map

    def elaborate(self, platform):
        m = Module()
        m.submodules.bridge = self._bridge
        wiring.connect(m, wiring.flipped(self.bus), self._bridge.bus)

        # FCR bits 1 and 2 clear the receive and transmit FIFOs. Amaranth's FIFOs
        # have no clear input, so the clear is a one-cycle synchronous reset of
        # the whole FIFO through ResetInserter -- which is exactly what "clear"
        # means for a structure whose entire state is its pointers and level.
        #
        # One cycle is enough because `w_stb` from the CSR multiplexer is already
        # registered and lasts exactly one cycle, and every pointer in the FIFO
        # is in the same domain as that strobe.
        rx_clear = Signal()
        tx_clear = Signal()

        # 16 deep, per the standard. SyncFIFOBuffered rather than SyncFIFO: the
        # buffered variant registers its output, so `r_data` is a flop rather
        # than a memory read port feeding the CSR read mux, and the read path
        # through the bus bridge stays short. At this depth the storage is
        # distributed LUT RAM on the ECP5, not a block RAM.
        rx = ResetInserter(rx_clear)(SyncFIFOBuffered(width=8, depth=FIFO_DEPTH))
        tx = ResetInserter(tx_clear)(SyncFIFOBuffered(width=8, depth=FIFO_DEPTH))
        m.submodules.rx = rx
        m.submodules.tx = tx

        # The divisor latches. Written, read back, and connected to nothing.
        # Init 1 rather than 0 because 0 is "divisor unset" to some drivers and
        # 1 is the fastest legal value, which is the closest thing to the truth
        # here: this pipe runs at whatever the transport runs at.
        dll = Signal(8, init=1)
        dlm = Signal(8, init=0)

        # Interrupt enable, and it now enables interrupts.
        #
        #   bit 0  ERBFI  received data available
        #   bit 1  ETBEI  transmit holding register empty
        #   bit 2  ELSI   receiver line status -- no error can occur here, so
        #                 setting it enables nothing (see the module docstring
        #                 on LSR bits 1..4)
        #   bit 3  EDSSI  modem status -- MSR is a constant, so likewise
        #
        # This used to be storage and nothing else, with a comment saying a
        # driver that enabled interrupts would wait forever. It no longer waits.
        ier = Signal(8)

        # FCR bit 0. Reported back through IIR bits 7:6 so a driver's FIFO probe
        # (write FCR=1, read IIR, look for 0b11 in the top bits) identifies a
        # 16550A. The FIFOs are always present and always 16 deep whatever this
        # says -- clearing it does not turn them into a one-byte holding
        # register, because a one-byte mode would be a second data path to get
        # wrong for the benefit of software written before 1987.
        fifo_en = Signal(init=0)

        dlab = self._lcr.f.data.data[7]

        # ---- +0  RBR / THR / DLL -------------------------------------------
        thr = self._rbr_thr.f.data
        m.d.comb += [
            tx.w_data.eq(thr.w_data),
            # DLAB gates the FIFO push. Without this, a driver programming the
            # baud divisor transmits two junk characters, which on a console is
            # a corrupt banner and on a protocol is a framing error -- and the
            # driver is doing nothing wrong.
            tx.w_en.eq(thr.w_stb & ~dlab),

            # THE ONLY SIDE-EFFECTING READ IN THIS PERIPHERAL, and it is the one
            # a data register is defined to have. Reading with DLAB set returns
            # the divisor and pops nothing.
            thr.r_data.eq(Mux(dlab, dll, rx.r_data)),
            rx.r_en.eq(thr.r_stb & ~dlab),
        ]
        with m.If(thr.w_stb & dlab):
            m.d.sync += dll.eq(thr.w_data)

        # ---- +1  IER / DLM --------------------------------------------------
        ier_reg = self._ier_dlm.f.data
        m.d.comb += ier_reg.r_data.eq(Mux(dlab, dlm, ier))
        with m.If(ier_reg.w_stb):
            with m.If(dlab):
                m.d.sync += dlm.eq(ier_reg.w_data)
            with m.Else():
                m.d.sync += ier.eq(ier_reg.w_data)

        # ---- the interrupt ---------------------------------------------------
        #
        # Two conditions, each a FIFO flag ANDed with its IER bit, and no state
        # of any kind:
        #
        #   rx_pending   IER.ERBFI and a byte is waiting  (LSR.DR)
        #   tx_pending   IER.ETBEI and there is room       (LSR.THRE)
        #
        # LEVEL SENSITIVE, held for as long as the condition holds. The PLIC in
        # front of this expects a level (`vexii_plic.py`), and so does a handler
        # that drains only part of a FIFO: an edge would mean the remaining
        # bytes are never announced, giving a console that accepts one burst and
        # then appears to hang.
        rx_pending = Signal()
        tx_pending = Signal()
        m.d.comb += [
            rx_pending.eq(ier[0] & rx.r_rdy),
            tx_pending.eq(ier[1] & tx.w_rdy),
            self.irq.eq(rx_pending | tx_pending),
        ]

        # ---- +2  IIR (R) / FCR (W) ------------------------------------------
        iir = self._iir_fcr.f.data

        # The interrupt id, per the standard's priority order. We can raise only
        # two of the five, so the order collapses to: receive beats transmit.
        #
        #   0b011  receiver line status  -- no error can occur, never raised
        #   0b010  received data available
        #   0b110  character timeout     -- no timer, never raised
        #   0b001  transmit holding register empty
        #   0b000  modem status          -- MSR is a constant, never raised
        iir_id = Signal(3)
        m.d.comb += iir_id.eq(Mux(rx_pending, 0b010, 0b001))

        m.d.comb += [
            # Bit 0 CLEAR means an interrupt is pending -- the sense is
            # inverted, and getting it the wrong way round gives a driver that
            # services an interrupt it was never told about.
            # Bits 3:1 are the id, meaningless while bit 0 is set.
            # Bits 5:4 are always zero. Bits 7:6 mirror the FIFO-enable bit,
            # which is how a driver tells a 16550A from a 16550 or a 16450.
            iir.r_data.eq(Cat(~self.irq, iir_id, C(0, 2), fifo_en, fifo_en)),

            # A clear is a strobe, not a stored bit: FCR bits 1 and 2 are
            # self-clearing on a real part and nothing reads them back.
            rx_clear.eq(iir.w_stb & iir.w_data[1]),
            tx_clear.eq(iir.w_stb & iir.w_data[2]),
        ]
        with m.If(iir.w_stb):
            m.d.sync += fifo_en.eq(iir.w_data[0])

        # READING IIR HERE HAS NO SIDE EFFECT, AND ON A REAL NS16550A IT DOES.
        #
        # On the real part, reading IIR while the pending interrupt is "transmit
        # holding register empty" CLEARS that interrupt. That is a read with a
        # side effect, at +2, in the SAME 32-bit word as RBR at +0 -- precisely
        # the arrangement this peripheral exists to eliminate, and precisely the
        # one that cost a day here when it was RBR being popped by a widened
        # read. Implementing it faithfully would put the trap back, one byte
        # over, and the failure it produced would be a transmit path that wedges
        # forever after some unrelated agent touched the low word.
        #
        # So the THRE interrupt is a level instead, and it clears when the
        # condition clears: write bytes until the FIFO is full, or clear ETBEI.
        # A correct driver already does one of those -- Linux's 8250 clears
        # IER.THRI in __stop_tx when its ring empties -- so this is invisible to
        # anything well behaved.
        #
        # What it is NOT invisible to: a driver that sets ETBEI with nothing to
        # send, takes the interrupt, reads IIR, and returns without writing or
        # masking. On a real part that is one interrupt. Here it is an interrupt
        # storm, because THRE is still true. The firmware in this tree does not
        # do that -- it never sets ETBEI at all (see
        # firmware/cynthion-soc/src/irq.rs) -- and this comment is the reason a
        # future one must not either.
        #
        # This is the only place where the board and QEMU's `-M virt` 16550
        # differ in behaviour rather than in address. It is written down here
        # because `scripts/soc_test.py` is only evidence about the board to the
        # extent that the two agree.

        # ---- +5  LSR --------------------------------------------------------
        #
        # Every bit here is a wire from a FIFO flag. No storage, no clear-on-read,
        # nothing that a second read would answer differently from the first.
        #
        #   bit 0  DR    a byte is waiting in the receive FIFO
        #   bits 1-4     overrun, parity, framing, break -- always 0, see the
        #                module docstring
        #   bit 5  THRE  the transmit FIFO has room. Note this is FIFO SPACE and
        #                not "the holding register is empty": with a 16-deep FIFO
        #                a driver may legally write 16 bytes after seeing THRE
        #                once, which is what the standard says and what a driver
        #                that trusts it will do.
        #   bit 6  TEMT  the transmit FIFO is empty. There is no shift register
        #                below it, so "transmitter empty" means the last byte has
        #                been handed to the transport -- not that it has arrived.
        #   bit 7        receive FIFO error; no error can occur, so 0.
        m.d.comb += self._lsr.f.data.r_data.eq(
            Cat(rx.r_rdy, C(0, 4), tx.w_rdy, ~tx.r_rdy, C(0, 1)))

        # ---- +6  MSR --------------------------------------------------------
        # CTS, DSR and DCD asserted; RI clear; no delta bits. A driver that waits
        # for CTS before transmitting, or treats DCD low as "carrier lost" and
        # closes the port, would otherwise hang against a peripheral that has no
        # modem pins to report on.
        m.d.comb += self._msr.f.data.r_data.eq(0xb0)

        # ---- the stream ports ------------------------------------------------
        m.d.comb += [
            self.source.payload.eq(tx.r_data),
            self.source.valid.eq(tx.r_rdy),
            tx.r_en.eq(self.source.ready),

            rx.w_data.eq(self.sink.payload),
            rx.w_en.eq(self.sink.valid),
            # Backpressure rather than silent loss where the transport can carry
            # it. A byte that arrives with the FIFO full is dropped, and LSR does
            # not say so -- so the elastic buffering in front of this is what
            # keeps that from happening. See stream_buffer.py.
            self.sink.ready.eq(rx.w_rdy),
        ]

        return m
